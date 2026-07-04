"""TikTok Studio uploader driven by Playwright + Chromium.

Hand-rolled from the DOM recon at 2026-07-01 (scripts/dom_recon.py).
Every selector below is one of the `data-e2e` hooks TikTok ships on
the Studio upload page — they're the closest thing to a stable
public API this flow has. If TikTok renames one, patch it here.

Not to be confused with anything third-party — we do NOT depend on
`tiktok-uploader`. The whole flow lives in this file.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from playwright.sync_api import (
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)

log = logging.getLogger(__name__)


_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
_DEFAULT_COOKIES_PATH = "data/cookies/tiktok_cookies.txt"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0.0.0 Safari/537.36")


# -------- data-e2e selectors (2026-07-01 recon) --------

_SEL_VIDEO_INPUT = 'input[type="file"][accept*="video"]'
_SEL_SELECT_VIDEO_BTN = '[data-e2e="select_video_button"]'
_SEL_UPLOAD_STATUS = '[data-e2e="upload_status_container"]'
_SEL_CAPTION_CONTAINER = '[data-e2e="caption_container"]'
_SEL_CAPTION_EDITOR = '[data-e2e="caption_container"] [contenteditable="true"]'
_SEL_COVER_CONTAINER = '[data-e2e="cover_container"]'
_SEL_VISIBILITY_CONTAINER = '[data-e2e="video_visibility_container"]'
_SEL_USER_PERM_CONTAINER = '[data-e2e="user_perm_container"]'
_SEL_AIGC_CONTAINER = '[data-e2e="aigc_container"]'
_SEL_ADVANCED_TOGGLE = '[data-e2e="advanced_settings_container"]'
_SEL_POST_BTN = '[data-e2e="post_video_button"]'


Visibility = Literal["public", "only_me", "friends"]


class UploadError(RuntimeError):
    pass


class TikTokAuthError(UploadError):
    """Cookies rejected — landed on login page or auth wall."""


class TikTokDOMError(UploadError):
    """A selector timed out. Recon likely needs a refresh."""


@dataclass(frozen=True)
class UploadResult:
    post_id: str
    tiktok_url: str | None
    visibility: Visibility


# -------- Cookie helpers --------

def _parse_netscape_cookies(path: Path) -> list[dict]:
    """Parse a Netscape cookies.txt jar and return Playwright-shaped rows
    for `.tiktok.com` only."""
    if not path.exists():
        raise UploadError(f"cookies file missing: {path}")
    cookies: list[dict] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include_sub, cpath, secure, expires, name, value = parts[:7]
        if "tiktok.com" not in domain:
            continue
        try:
            exp = int(expires)
        except ValueError:
            exp = -1
        cookies.append({
            "name": name, "value": value, "domain": domain,
            "path": cpath or "/", "expires": exp, "httpOnly": False,
            "secure": secure.upper() == "TRUE", "sameSite": "Lax",
        })
    if not cookies:
        raise UploadError(f"no .tiktok.com cookies found in {path}")
    return cookies


def sessionid_expires_in_days(cookies_path: Path | str = _DEFAULT_COOKIES_PATH) -> float | None:
    """Return days until the `sessionid` cookie expires, or None if not found.
    Called by the uploader + a daily systemd timer to alert the user before
    the jar goes stale (Q2 = auto-detect near-expiry, 3d before)."""
    path = Path(cookies_path)
    if not path.exists():
        return None
    for row in _parse_netscape_cookies(path):
        if row["name"] == "sessionid":
            exp = row.get("expires")
            if isinstance(exp, int) and exp > 0:
                return (exp - time.time()) / 86400.0
    return None


# -------- Playwright flow --------

def _launch_context(pw: Playwright, *, headless: bool):
    browser = pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="es-ES",
        timezone_id="Europe/Madrid",
        user_agent=_UA,
    )
    return browser, context


def _dismiss_cookie_consent(page: Page) -> None:
    """EU cookie banner — the ES account will hit this on a fresh cookie
    jar. Try a handful of labels; ignore if not present."""
    for label in ("Aceptar todas las cookies", "Aceptar todo", "Aceptar",
                  "Accept all", "Accept cookies", "Allow all"):
        try:
            btn = page.get_by_role("button", name=label, exact=False).first
            if btn.is_visible(timeout=1500):
                btn.click()
                log.info("dismissed cookie consent: %r", label)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _dismiss_studio_tooltips(page: Page, max_rounds: int = 8) -> None:
    """TikTok Studio ships a multi-step onboarding walkthrough (tooltips
    pointing at the caption editor, visibility switch, etc.). Any of its
    steps captures pointer events and blocks Playwright clicks on the
    real controls underneath.

    Strategy: try one of the exit buttons per round (`Skip tour` /
    `Got it` / final-step `Done` etc., plus ES/CN mirrors), wait ~600 ms
    for the next step, repeat. Bail out once no known label is visible
    or after `max_rounds` iterations. Idempotent when the tour never
    shows up."""
    labels = (
        # EN
        "Skip tour", "Skip", "Got it", "Got it, thanks", "Done", "Finish",
        "Next", "OK", "Close",
        # ES
        "Omitir tour", "Omitir", "Entendido", "Entendido, gracias",
        "Hecho", "Finalizar", "Siguiente", "Cerrar",
        # CN — some tenants surface Chinese strings even w/ locale=en
        "跳过", "知道了", "完成",
    )
    for _ in range(max_rounds):
        clicked = False
        for label in labels:
            try:
                btn = page.get_by_role("button", name=label, exact=False).first
                if btn.is_visible(timeout=400):
                    btn.click()
                    log.info("dismissed studio tooltip: %r", label)
                    page.wait_for_timeout(600)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            # Fallback: try clicking a generic close icon inside a tour
            # popover before giving up.
            try:
                close_x = page.locator(
                    "[class*='tour'] [aria-label*='close' i], "
                    "[class*='guide'] [aria-label*='close' i], "
                    "[data-tt-tour] button[aria-label*='close' i]"
                ).first
                if close_x.is_visible(timeout=300):
                    close_x.click()
                    log.info("dismissed studio tooltip via close-icon fallback")
                    page.wait_for_timeout(400)
                    continue
            except Exception:
                pass
            return


def _fill_caption(page: Page, caption: str) -> None:
    """Draft.js editor lives inside `[data-e2e=caption_container]`. Focus it,
    select-all-delete any stub (TikTok auto-populates the caption from the
    uploaded filename, e.g. `1ul05ky` for `1ul05ky.mp4`), then type.

    Use ControlOrMeta so the select-all works on both Linux Chromium
    (Ctrl+A) and macOS Chromium (Cmd+A). The prior Meta-only shortcut
    silently no-op'd on Linux, causing the typed caption to be appended
    to the filename stub instead of replacing it.
    """
    editor = page.locator(_SEL_CAPTION_EDITOR).first
    editor.wait_for(state="visible", timeout=30000)
    editor.click()
    page.wait_for_timeout(200)
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(150)
    page.keyboard.type(caption, delay=8)
    page.wait_for_timeout(500)


def _set_visibility(page: Page, visibility: Visibility) -> None:
    """Open the visibility combobox and pick the requested option. The
    combobox is a BUTTON with role=combobox rendered inside
    `[data-e2e=video_visibility_container]`. Options are rendered in a
    popover as `[role=option]` children after clicking."""
    label_map: dict[Visibility, tuple[str, ...]] = {
        "public":   ("Todo el mundo", "Everyone", "Public", "Público"),
        "only_me":  ("Solo tú", "Sólo yo", "Only me", "Privado", "Only you"),
        "friends":  ("Amigos", "Friends"),
    }
    labels = label_map[visibility]

    container = page.locator(_SEL_VISIBILITY_CONTAINER).first
    combo = container.locator('[role="combobox"]').last
    combo.click()
    page.wait_for_timeout(300)

    # options render as [role=option] or as menu items with matching text.
    for label in labels:
        try:
            opt = page.get_by_role("option", name=label, exact=False).first
            if opt.is_visible(timeout=1500):
                opt.click()
                log.info("set visibility to %s (%r)", visibility, label)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue
    # fallback: any element inside a listbox/menu with matching text
    for label in labels:
        try:
            el = page.locator(f'[role="listbox"] :text("{label}")').first
            if el.is_visible(timeout=1500):
                el.click()
                log.info("set visibility to %s (fallback :text %r)", visibility, label)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue
    raise TikTokDOMError(f"visibility option not found for {visibility}")


def _expand_advanced_settings(page: Page) -> None:
    """The AIGC toggle sits inside the collapsed 'Mostrar más' /
    'Show more' section. Click it if present so the toggle becomes
    interactable. Idempotent — if already expanded, the container's
    inner text will contain 'Mostrar menos'."""
    try:
        el = page.locator(_SEL_ADVANCED_TOGGLE).first
        el.wait_for(state="visible", timeout=5000)
    except PWTimeoutError:
        log.info("advanced settings container not found — skipping expand")
        return
    inner = (el.inner_text() or "").lower()
    if "menos" in inner or "less" in inner:
        return  # already expanded
    try:
        el.click()
        log.info("expanded advanced settings ('Mostrar más')")
        page.wait_for_timeout(400)
    except Exception as e:
        log.warning("could not expand advanced settings: %s", e)


def _ensure_aigc_toggle_on(page: Page) -> None:
    """AIGC (AI-generated content) toggle lives inside
    `[data-e2e=aigc_container]`, which is inside the collapsed advanced
    section. Expand first, then flip the switch. Required per posting
    policy (Q13).

    The switch has an inner `<span data-part='thumb'>` that intercepts
    pointer events, so a plain `.click()` retries forever. We dispatch a
    JS click on the switch element directly, bypassing the overlay."""
    _expand_advanced_settings(page)

    container = page.locator(_SEL_AIGC_CONTAINER).first
    container.wait_for(state="visible", timeout=15000)
    container.scroll_into_view_if_needed()
    # Source-of-truth is the hidden <input type=checkbox> under the switch.
    # `[role=switch]` and the thumb-span go out of sync during re-renders,
    # but the checkbox input's .checked property tracks state reliably.
    checkbox = container.locator('input[type="checkbox"]').first
    checkbox.wait_for(state="attached", timeout=10000)

    if checkbox.evaluate("el => el.checked"):
        log.info("AIGC toggle already ON")
        return

    # Click via the checkbox element directly; bypasses the thumb overlay
    # and the whole widget's synthetic pointer plumbing.
    checkbox.evaluate("el => el.click()")
    page.wait_for_timeout(600)

    # Flipping AIGC opens a confirmation dialog — TikTok requires the
    # creator to explicitly acknowledge the AI-content disclosure before
    # the toggle actually latches. Click the confirmation ("Activar" /
    # "Turn on") button if the dialog appears.
    _confirm_aigc_dialog(page)

    if not checkbox.evaluate("el => el.checked"):
        # Fallback 1: force-click the switch container + re-confirm.
        try:
            container.locator('[role="switch"]').first.click(force=True, timeout=3000)
            page.wait_for_timeout(600)
            _confirm_aigc_dialog(page)
        except Exception:
            pass
    if not checkbox.evaluate("el => el.checked"):
        _screenshot_debug(page, "aigc_stuck")
        raise TikTokDOMError("failed to flip AIGC toggle ON (checkbox.checked stayed false)")
    log.info("AIGC toggle: ON")


def _confirm_aigc_dialog(page: Page) -> None:
    """When the AIGC toggle is clicked, TikTok opens a confirmation
    dialog asking to acknowledge the AI-content disclosure. Click the
    confirmation button (label varies by locale)."""
    labels = (
        "Activar",         # ES
        "Turn on", "Activate", "Enable", "Confirm",  # EN
    )
    # The dialog usually renders as [role=dialog] or a portal with buttons.
    deadline = time.time() + 5
    while time.time() < deadline:
        for label in labels:
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                if btn.is_visible(timeout=500):
                    btn.click()
                    log.info("AIGC confirmation dialog: clicked %r", label)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        page.wait_for_timeout(300)
    log.info("no AIGC confirmation dialog visible (skipping)")


# Locale-tolerant match for the Content-Check-Lite ("Revisión del contenido
# simplificada") row + its in-progress / passed states.
_CONTENT_CHECK_LABEL_RE = re.compile(
    r"revisi[oó]n del contenido simplificada|content check|automatic content check",
    re.I)
_CONTENT_CHECK_INPROGRESS_RE = re.compile(
    r"comprobaci[oó]n en curso|checking|in progress", re.I)
_CONTENT_CHECK_DONE_RE = re.compile(
    r"no se han detectado problemas|no issues (found|detected)|looks good", re.I)
# The mid-review "publish before the check finishes?" modal. Answering it with
# "Publicar ahora" makes TikTok reject the publish server-side ("Ha ocurrido un
# error") — so we must recognise it and NOT click through it.
_MIDREVIEW_MODAL_RE = re.compile(
    r"seguir con la publicaci[oó]n|antes de finalizar la revisi[oó]n|"
    r"seguimos revisando|continue to post|before.*review", re.I)


def _disable_content_check(page: Page) -> None:
    """Turn OFF TikTok's Content-Check-Lite review toggle before Publicar.

    The check is optional/advisory (Creator Academy). Left ON, clicking
    Publicar mid-review opens a "¿Seguir con la publicación?" modal, and
    publishing before the ~10-min review finishes is rejected server-side
    with "Ha ocurrido un error" — the post never goes live
    (bugs/2026-07-04-tiktok-content-review-publish-break, wkaisertexas/
    tiktok-uploader#238). Disabling it means Publicar just publishes.

    Clean no-op if the row/toggle isn't present (intermittent A/B rollout).
    Never raises — the safe path in _confirm_publish_dialog is the backstop."""
    try:
        label = page.get_by_text(_CONTENT_CHECK_LABEL_RE).first
        if not label.is_visible(timeout=2000):
            log.info("content-check row not visible (skipping disable)")
            return
    except Exception:
        log.info("content-check row not found (skipping disable)")
        return

    # The switch lives in the nearest ancestor row that contains a checkbox;
    # the hidden input is the reliable source-of-truth (same as AIGC).
    try:
        row = label.locator(
            "xpath=ancestor::*[.//input[@type='checkbox']][1]").first
        checkbox = row.locator('input[type="checkbox"]').first
        checkbox.wait_for(state="attached", timeout=5000)
    except Exception as e:
        log.warning("content-check toggle input not found: %s (skipping)", e)
        return

    try:
        if not checkbox.evaluate("el => el.checked"):
            log.info("content-check already OFF")
            return
        # JS click on the input bypasses the overlay that intercepts pointer
        # events (cf. tiktok-uploader#239), same trick as the AIGC toggle.
        checkbox.evaluate("el => el.click()")
        page.wait_for_timeout(600)
        # Some locales pop a confirm ("Desactivar la revisión?") — dismiss-affirm.
        _confirm_content_check_off(page)
        if checkbox.evaluate("el => el.checked"):
            # Fallback: force-click the visible switch + re-confirm.
            try:
                row.locator('[role="switch"]').first.click(force=True, timeout=3000)
                page.wait_for_timeout(600)
                _confirm_content_check_off(page)
            except Exception:
                pass
        state = "OFF" if not checkbox.evaluate("el => el.checked") else "STILL ON"
        log.info("content-check toggle: %s", state)
    except Exception as e:
        log.warning("could not toggle content-check off: %s (continuing)", e)


def _confirm_content_check_off(page: Page) -> None:
    """Dismiss the optional 'turn off the review?' confirm, if it appears."""
    labels = ("Desactivar", "Turn off", "Confirmar", "Confirm", "OK", "Aceptar")
    deadline = time.time() + 3
    while time.time() < deadline:
        for label in labels:
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                if btn.is_visible(timeout=300):
                    btn.click()
                    log.info("content-check off-confirm: clicked %r", label)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue
        page.wait_for_timeout(250)


def _wait_content_check_clear(page: Page, timeout_s: float = 480.0) -> bool:
    """Poll until the content review is no longer in progress (passed, or the
    'Comprobación en curso' text is gone). Bounded fallback for when the toggle
    couldn't be disabled. Returns True if it cleared, False on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            body = page.locator("body").inner_text(timeout=2000)
        except Exception:
            body = ""
        if _CONTENT_CHECK_DONE_RE.search(body or ""):
            log.info("content review passed ('no issues')")
            return True
        if not _CONTENT_CHECK_INPROGRESS_RE.search(body or ""):
            log.info("content review no longer in progress")
            return True
        page.wait_for_timeout(5000)
    log.warning("content review did not clear within %.0fs", timeout_s)
    return False


# Text that means TikTok is still uploading/processing the file server-side.
_PROCESSING_RE = re.compile(
    r"procesando|processing|subiendo|uploading|cargando", re.I)


def _settle_before_publish(page: Page,
                           min_wait_s: float | None = None,
                           max_wait_s: float | None = None) -> None:
    """Wait for TikTok to finish server-side ingest/processing of the uploaded
    video BEFORE clicking Publicar.

    This is the load-bearing fix for the "Ha ocurrido un error. Retry or
    replace video" failure: Publicar becoming enabled only means the *upload*
    reached TikTok — the file is still transcoding server-side (the cover shows
    "Procesando"). Publishing mid-processing is rejected, even in a manual
    browser session. So we poll the page for any processing indicator
    ("Procesando"/"processing"/"subiendo"…) and only publish once it has been
    clear for two consecutive polls AND a floor buffer has elapsed — capped so
    a stuck check can't hang the run.

    Tunable via env: TIKTOK_PUBLISH_SETTLE_MIN_S (default 60),
    TIKTOK_PUBLISH_SETTLE_MAX_S (default 240)."""
    if min_wait_s is None:
        min_wait_s = float(os.environ.get("TIKTOK_PUBLISH_SETTLE_MIN_S", "60"))
    if max_wait_s is None:
        max_wait_s = float(os.environ.get("TIKTOK_PUBLISH_SETTLE_MAX_S", "240"))
    start = time.time()
    log.info("settling before publish (min %.0fs, max %.0fs)…", min_wait_s, max_wait_s)
    consecutive_clear = 0
    while True:
        elapsed = time.time() - start
        try:
            body = page.locator("body").inner_text(timeout=2000)
        except Exception:
            body = ""
        if _PROCESSING_RE.search(body or ""):
            consecutive_clear = 0
        else:
            consecutive_clear += 1
        if elapsed >= max_wait_s:
            log.warning("settle: reached max %.0fs still seeing processing — "
                        "publishing anyway (may error)", max_wait_s)
            return
        if elapsed >= min_wait_s and consecutive_clear >= 2:
            log.info("settle: processing cleared after %.0fs — publishing", elapsed)
            return
        page.wait_for_timeout(3000)


def _click_post(page: Page) -> None:
    btn = page.locator(_SEL_POST_BTN).first
    btn.wait_for(state="visible", timeout=15000)
    # Sometimes the button is aria-disabled while the video is still
    # processing. Wait until it's actually enabled.
    deadline = time.time() + 120
    was_disabled = False
    while time.time() < deadline:
        disabled = btn.get_attribute("disabled")
        aria_disabled = btn.get_attribute("aria-disabled")
        if not disabled and aria_disabled != "true":
            if was_disabled:
                log.info("Publicar became enabled")
            break
        was_disabled = True
        page.wait_for_timeout(1000)
    else:
        _screenshot_debug(page, "publicar_stayed_disabled")
        raise TikTokDOMError("Publicar button never became enabled within 120s")
    # Let TikTok finish server-side ingest/processing before publishing.
    # Publicar enabling only means the *upload* reached TikTok; the file is
    # still transcoding/processing server-side (cover shows "Procesando").
    # Clicking Publish mid-processing is rejected with "Ha ocurrido un error"
    # even manually. Wait for the processing indicators to clear (bounded),
    # plus a floor buffer.
    _settle_before_publish(page)
    btn.click()
    log.info("clicked Publicar")
    # Give the click a moment to register; some modals appear ~1s later.
    page.wait_for_timeout(1500)
    _confirm_publish_dialog(page)


def _confirm_publish_dialog(page: Page) -> None:
    """After Publicar, dismiss any benign publish-confirm dialog.

    IMPORTANT: the mid-review "¿Seguir con la publicación? … antes de finalizar
    la revisión" modal must NEVER be answered with "Publicar ahora" — that asks
    TikTok to publish before the Content-Check-Lite review finishes, which it
    rejects server-side with "Ha ocurrido un error" and the post never goes
    live (bugs/2026-07-04-tiktok-content-review-publish-break, wkaisertexas/
    tiktok-uploader#238). We normally prevent this by disabling the review in
    `_disable_content_check` before Publicar; this is the backstop if the
    toggle couldn't be turned off.

    - If the mid-review modal appears: click Cancelar (do NOT publish), wait
      bounded for the review to clear, then re-click Publicar once.
    - Otherwise click through genuine confirm CTAs (NOT "Publicar ahora")."""
    # "Publicar ahora" is deliberately excluded — it is only ever the
    # mid-review publish-anyway button, handled separately below.
    labels = (
        "Continuar publicando", "Seguir publicando",   # ES — 'Continue to post?' step
        "Publicar de todos modos", "Post anyway",      # ES/EN — 'Post anyway'
        "Publicar",                                    # ES — plain publish confirm
        "Confirmar", "Continuar",                      # ES
        "Post", "Publish", "Confirm", "Continue", "OK",
    )
    deadline = time.time() + 25          # room for a couple of chained dialogs
    clicks = 0
    idle_polls = 0
    handled_midreview = False
    while time.time() < deadline and clicks < 4:
        dialog = page.locator('[role="dialog"], [role="alertdialog"]').first
        try:
            visible = dialog.is_visible(timeout=500)
        except Exception:
            visible = False
        if not visible:
            # After clicking at least once, allow a short grace for a chained
            # dialog to render; if none shows up, we're done.
            idle_polls += 1
            if idle_polls >= 3:
                break
            page.wait_for_timeout(500)
            continue
        idle_polls = 0

        # Mid-review modal? Never "Publicar ahora" — cancel, wait, re-publish.
        txt = _dump_visible_dialog(page) or ""
        if _MIDREVIEW_MODAL_RE.search(txt) and not handled_midreview:
            handled_midreview = True
            log.warning("mid-review publish modal appeared (content-check not "
                        "disabled) — cancelling instead of publishing early")
            _screenshot_debug(page, "content_check_modal_cancel")
            for cancel in ("Cancelar", "Cancel"):
                try:
                    b = dialog.get_by_role("button", name=cancel, exact=True).first
                    if b.is_visible(timeout=300):
                        b.click()
                        break
                except Exception:
                    continue
            page.wait_for_timeout(500)
            _wait_content_check_clear(page)
            # Re-publish now that the review is (hopefully) done.
            try:
                post_btn = page.locator(_SEL_POST_BTN).first
                post_btn.wait_for(state="visible", timeout=10000)
                post_btn.click()
                log.info("re-clicked Publicar after content review cleared")
                page.wait_for_timeout(1500)
            except Exception as e:
                _screenshot_debug(page, "republish_failed")
                raise TikTokDOMError(
                    f"could not re-Publicar after content review: {e}") from e
            continue

        clicked_this = False
        for label in labels:
            try:
                btn = dialog.get_by_role("button", name=label, exact=True).first
                if btn.is_visible(timeout=300):
                    btn.click()
                    clicks += 1
                    clicked_this = True
                    log.info("publish confirmation dialog #%d: clicked %r", clicks, label)
                    page.wait_for_timeout(1200)   # let the next dialog (if any) render
                    break
            except Exception:
                continue

        if not clicked_this:
            # Dialog open but no known CTA — capture it so we can extend labels.
            log.warning("unhandled publish dialog (no known CTA): %r",
                        txt.replace("\n", " | ")[:200])
            _screenshot_debug(page, "publish_dialog_unhandled")
            break

    if clicks == 0 and not handled_midreview:
        log.info("no publish confirmation dialog visible (skipping)")


# Publish-confirm phrases. These are POST-publish signals — do NOT include
# pre-publish words like "cargado"/"uploaded" (they fire when the file
# reaches TikTok, well before Publicar takes effect).
_POST_CONFIRM_NEEDLES = (
    "publicado", "publicada",       # ES
    "posted", "published",           # EN
    "video ha sido publicado",       # ES full sentence
    "your video has been posted",    # EN full sentence
    "en revisión", "under review",   # moderation queue
    "programada",                    # scheduled path
)


def _dump_visible_dialog(page: Page) -> str | None:
    """If a modal/dialog is open, return its text preview. Used to surface
    unhandled AIGC / duplicate / captcha prompts after clicking Publicar."""
    try:
        dialog = page.locator('[role="dialog"], [role="alertdialog"]').first
        if dialog.is_visible(timeout=500):
            return (dialog.inner_text() or "")[:400]
    except Exception:
        pass
    return None


def _screenshot_debug(page: Page, tag: str) -> None:
    """Save a diagnostic screenshot to `data/temp/upload_debug/<tag>.png`."""
    try:
        out_dir = Path("data/temp/upload_debug")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{tag}_{int(time.time())}.png"
        page.screenshot(path=str(out), full_page=True)
        log.info("debug screenshot -> %s", out)
    except Exception as e:
        log.warning("screenshot failed: %s", e)


def _wait_for_post_confirmation(page: Page, timeout_s: float = 240.0) -> str | None:
    """After clicking Publicar, TikTok Studio either:
      (a) navigates away from /tiktokstudio/upload (best signal), or
      (b) shows a `publicado` / `posted` / `en revisión` toast.

    Immediately after the click Studio also frequently opens a modal —
    AIGC required, duplicate warning, captcha. We flag those loudly
    instead of pretending the post succeeded."""
    deadline = time.time() + timeout_s
    dialog_reported: set[str] = set()

    while time.time() < deadline:
        cur = page.url
        # Signal A: URL change (redirect to /content, /profile, etc.)
        if "tiktokstudio/upload" not in cur and "tiktok.com" in cur:
            log.info("post confirmed by url change: %s", cur)
            return cur

        # Modal check — surface any unhandled prompt.
        dialog_text = _dump_visible_dialog(page)
        if dialog_text and dialog_text[:80] not in dialog_reported:
            log.warning("modal visible after Publicar — text preview: %r",
                        dialog_text.replace("\n", " | ")[:200])
            dialog_reported.add(dialog_text[:80])
            _screenshot_debug(page, "publish_modal")

        # Signal B: strict success/queue toast.
        for needle in _POST_CONFIRM_NEEDLES:
            try:
                el = page.get_by_text(needle, exact=False).first
                if el.is_visible(timeout=300):
                    log.info("post confirmed by toast: %r", needle)
                    return None
            except Exception:
                continue

        page.wait_for_timeout(1500)

    _screenshot_debug(page, "publish_timeout")
    raise TikTokDOMError("post confirmation not observed within timeout")


def _crosscheck_post_via_display_api(publish_click_ts: float,
                                     slack_s: int = 60) -> str | None:
    """Fallback verifier for _wait_for_post_confirmation timeouts.

    Studio occasionally accepts the publish but neither redirects nor
    surfaces a toast, so the DOM detector times out on a video that
    actually went live. Query the Display API for the newest video
    on the authenticated account; if its `create_time` is at/after
    the publish click (minus `slack_s` for clock skew + API latency),
    treat that as evidence of success and return its share_url.
    Returns None on no-match or API failure — caller re-raises."""
    try:
        from pipeline.tiktok_api import video_list
    except Exception as e:
        log.warning("display-api import failed during cross-check: %s", e)
        return None
    try:
        vids, _, _ = video_list(max_count=3)
    except Exception as e:
        log.warning("display-api cross-check failed: %s", e)
        return None
    if not vids:
        log.info("display-api cross-check: no videos returned")
        return None
    newest = vids[0]
    threshold = int(publish_click_ts) - slack_s
    if newest.create_time >= threshold:
        log.warning("display-api cross-check: newest video id=%s create_time=%d "
                    ">= threshold=%d — treating as posted", newest.id,
                    newest.create_time, threshold)
        return newest.share_url or None
    log.info("display-api cross-check: newest video id=%s create_time=%d < "
             "threshold=%d — no fresh post detected", newest.id,
             newest.create_time, threshold)
    return None


# -------- Public entrypoint --------

def upload_to_tiktok(
    *,
    post_id: str,
    video_path: Path | str,
    cover_path: Path | str | None,
    caption: str,
    visibility: Visibility = "public",
    cookies_path: Path | str = _DEFAULT_COOKIES_PATH,
    headless: bool | None = None,
    aigc: bool = True,
) -> UploadResult:
    """Drive TikTok Studio to publish `video_path` under `caption`.

    Args:
        post_id: internal id (Reddit post id); used for logging + result.
        video_path: local MP4 to upload.
        cover_path: optional cover PNG. Currently the upload lets TikTok
            auto-pick a frame if not set — the recon flow for the cover
            editor is a follow-up (see repo NOTE below).
        caption: description text; will be typed into the Draft.js editor.
        visibility: 'public' | 'only_me' | 'friends'. Default 'public'.
        cookies_path: Netscape jar. Must contain `.tiktok.com` rows.
        headless: force headless bool; if None, reads env `TIKTOK_HEADLESS`
            (default False — headful, per phase-6-ops Q19 decision).
        aigc: whether to toggle the AI-generated-content disclosure ON.
            Default True per posting policy (Q13).

    Returns:
        UploadResult with the tiktok URL if we could observe it.

    NOTE: cover uploading via `pipeline/cover.py`-generated PNG is not
    wired here yet. The recon caught the `cover_container` label but the
    "Editar portada" sub-modal wasn't reached in the first pass. Add
    once we've inspected that modal.
    """
    video = Path(video_path)
    cover = Path(cover_path) if cover_path else None
    cookies_file = Path(cookies_path)

    if not video.exists():
        raise UploadError(f"video missing: {video}")
    if cover and not cover.exists():
        raise UploadError(f"cover missing: {cover}")

    # sessionid age warning — surface but don't refuse.
    days = sessionid_expires_in_days(cookies_file)
    if days is not None and days < 3:
        log.warning("sessionid expires in %.1f days — refresh cookies soon", days)
    elif days is not None:
        log.info("sessionid valid for %.0f more days", days)

    if headless is None:
        headless = os.environ.get("TIKTOK_HEADLESS", "0") == "1"

    cookies = _parse_netscape_cookies(cookies_file)

    with sync_playwright() as pw:
        browser, context = _launch_context(pw, headless=headless)
        try:
            context.add_cookies(cookies)
            page = context.new_page()
            page.goto(_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            log.info("landed on %s", page.url)

            if "login" in page.url or "signup" in page.url:
                raise TikTokAuthError(f"redirected to auth page: {page.url}")

            _dismiss_cookie_consent(page)

            # ---- Upload the video ----
            try:
                page.set_input_files(_SEL_VIDEO_INPUT, str(video))
            except PWTimeoutError as e:
                raise TikTokDOMError(f"video file input not found: {e}") from e
            log.info("set_input_files(%s) OK; waiting for form", video.name)

            try:
                page.wait_for_selector(_SEL_CAPTION_EDITOR, timeout=90000)
            except PWTimeoutError as e:
                raise TikTokDOMError(f"caption editor did not appear: {e}") from e

            # Fresh cookie jars land in a Studio onboarding walkthrough —
            # multi-step tooltip stack that blocks pointer events on the
            # caption + visibility controls. Kill it before we type.
            _dismiss_studio_tooltips(page)

            # ---- Caption ----
            _fill_caption(page, caption)
            log.info("caption filled (%d chars)", len(caption))

            # ---- Visibility ----
            _set_visibility(page, visibility)

            # ---- AIGC toggle ----
            if aigc:
                _ensure_aigc_toggle_on(page)

            # ---- Skip Content Check Lite (advisory review) so Publicar
            #      doesn't hit the mid-review "publish anyway → error" path
            #      (bug #29 / tiktok-uploader#238) ----
            _disable_content_check(page)

            # ---- Post ----
            _click_post(page)
            publish_click_ts = time.time()

            try:
                tiktok_url = _wait_for_post_confirmation(page)
            except TikTokDOMError as detector_err:
                fallback_url = _crosscheck_post_via_display_api(publish_click_ts)
                if fallback_url is None:
                    raise
                log.warning("detector missed post but Display API confirms: %s "
                            "(detector said: %s)", fallback_url, detector_err)
                tiktok_url = fallback_url
            return UploadResult(post_id=post_id, tiktok_url=tiktok_url, visibility=visibility)

        finally:
            try:
                context.close()
            finally:
                browser.close()
