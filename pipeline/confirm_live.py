"""Confirm-live scrape for Phase 6 (Q16).

Rows in `posted_under_review` that we uploaded ≥30 min ago get
promoted to `posted` once we can find the corresponding public video
URL by scraping the TikTok profile page.

Assumes:
  * `TIKTOK_HANDLE` is set in .env (e.g. `RealRedditStories`).
  * `data/cookies/tiktok_cookies.txt` is fresh enough to see the profile.
    (Public profile is scrapeable without cookies, but we send them so
    'Only me' smoke posts are also visible for testing.)

Match heuristic:
  * The profile grid returns videos newest-first.
  * We take the top-N (default 5) hrefs like
    `/@RealRedditStories/video/<id>`.
  * The oldest row in `posted_under_review` that's ≥30 min old maps to
    whichever grid position matches its rank among unconfirmed uploads.
  * If any grid URL isn't already claimed by another `posted` row's
    `tiktok_url`, we take the top unclaimed one.

Runs as a one-shot script under launchd every 30 min.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `python pipeline/confirm_live.py` under launchd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import os

from playwright.sync_api import sync_playwright, Page

from core.config import _load_dotenv
from core.db import Db, RenderRow, UPLOAD_POSTED, UPLOAD_POSTED_UNDER_REVIEW
from core.logging_setup import setup_logging
from core.notify import Notifier, NotifierError, _html_escape
from pipeline.upload import _parse_netscape_cookies

log = logging.getLogger("confirm_live")


_MIN_AGE_MIN = 30                       # Q16
_MAX_CONFIRM_ATTEMPTS = 6               # ~3 h of hourly ticks before giving up
_GRID_LIMIT = 8                         # how many recent posts to fetch
_CFG_CONFIRM_ATTEMPTS = "confirm_attempts:{post_id}"

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/131.0.0.0 Safari/537.36")


# ---- Playwright scrape ----------------------------------------------------

def _scrape_recent_video_urls(page: Page, handle: str, limit: int = _GRID_LIMIT) -> list[str]:
    """Return up to `limit` newest video URLs from the profile grid.

    TikTok lowercases the handle in video URLs (`/@realredditstories/video/...`
    even for account `@RealRedditStories`) so we key off the DOM data-e2e
    hook instead of the href pattern."""
    url = f"https://www.tiktok.com/@{handle}"
    log.info("scraping %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    try:
        page.wait_for_selector('[data-e2e="user-post-item"]', timeout=15000)
    except Exception:
        log.warning("no user-post-item elements visible on profile within 15s")
        return []
    hrefs = page.evaluate("""
    () => {
        const seen = new Set();
        const out = [];
        document.querySelectorAll('[data-e2e="user-post-item"] a[href*="/video/"]')
            .forEach(a => {
                if (!seen.has(a.href)) { seen.add(a.href); out.push(a.href); }
            });
        return out;
    }
    """)
    return hrefs[:limit]


def _confirmed_urls(db: Db) -> set[str]:
    """Return every `tiktok_url` we've already stamped on a `posted` row —
    so we don't re-map an already-claimed grid slot."""
    cur = db._conn.execute(  # noqa: SLF001 - internal use
        "SELECT tiktok_url FROM used WHERE tiktok_url IS NOT NULL"
    )
    return {row[0] for row in cur.fetchall() if row[0]}


# ---- Age gate + retry counters -------------------------------------------

def _minutes_since(iso: str | None) -> float:
    if not iso:
        return -1.0
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return -1.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 60.0


def _bump_confirm_attempts(db: Db, post_id: str) -> int:
    key = _CFG_CONFIRM_ATTEMPTS.format(post_id=post_id)
    prior = int(db.get_config(key, "0") or "0")
    now = prior + 1
    db.set_config(key, str(now))
    return now


def _clear_confirm_attempts(db: Db, post_id: str) -> None:
    key = _CFG_CONFIRM_ATTEMPTS.format(post_id=post_id)
    db.set_config(key, "0")


# ---- Core ----------------------------------------------------------------

def run_once(*, force: bool = False) -> int:
    _load_dotenv()
    setup_logging()

    handle = (os.environ.get("TIKTOK_HANDLE") or "").strip()
    if not handle:
        log.error("TIKTOK_HANDLE missing in .env")
        return 2

    try:
        notifier: Notifier | None = Notifier.from_env()
    except NotifierError:
        notifier = None

    with Db.open() as db:
        eligible: list[RenderRow] = []
        for r in db.under_review():
            age = _minutes_since(r.uploaded_at)
            if force or age >= _MIN_AGE_MIN:
                eligible.append(r)
            else:
                log.info("skip %s: only %.0fm old (need %d)",
                         r.post_id, age, _MIN_AGE_MIN)

        if not eligible:
            log.info("nothing under review is old enough to confirm")
            return 0

        # Oldest-first: rows uploaded earlier should map to older
        # grid positions (deeper down the profile).
        eligible.sort(key=lambda r: r.uploaded_at or "")

        cookies_path = Path("data/cookies/tiktok_cookies.txt")
        cookies = _parse_netscape_cookies(cookies_path) if cookies_path.exists() else []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="es-ES",
                timezone_id="Europe/Madrid",
                user_agent=_UA,
            )
            if cookies:
                context.add_cookies(cookies)
            page = context.new_page()
            try:
                grid = _scrape_recent_video_urls(page, handle)
            finally:
                context.close()
                browser.close()

        log.info("profile grid: %d urls", len(grid))
        if not grid:
            _handle_all_missing(db, eligible, notifier,
                                reason="empty profile grid")
            return 0

        claimed = _confirmed_urls(db)
        unclaimed = [u for u in grid if u not in claimed]
        log.info("unclaimed grid slots: %d", len(unclaimed))

        # Pair rows to unclaimed grid slots newest-first. Since our rows
        # are oldest-first and the grid is newest-first, we consume grid
        # from the START (grid[0] = newest = latest post) for the
        # NEWEST unconfirmed row (eligible[-1]).
        newest_first_rows = list(reversed(eligible))
        assignments: list[tuple[RenderRow, str]] = []
        i = 0
        for row in newest_first_rows:
            if i >= len(unclaimed):
                break
            assignments.append((row, unclaimed[i]))
            i += 1

        for row, url in assignments:
            db.confirm_posted(row.post_id, url)
            _clear_confirm_attempts(db, row.post_id)
            log.info("confirmed %s -> %s", row.post_id, url)
            _notify_confirmed(notifier, row, url)

        # Rows that didn't get a slot get an attempt bump; terminal after
        # _MAX_CONFIRM_ATTEMPTS.
        assigned_ids = {r.post_id for r, _ in assignments}
        for row in eligible:
            if row.post_id in assigned_ids:
                continue
            attempts = _bump_confirm_attempts(db, row.post_id)
            log.warning("no grid slot for %s (attempt %d/%d)",
                        row.post_id, attempts, _MAX_CONFIRM_ATTEMPTS)
            if attempts >= _MAX_CONFIRM_ATTEMPTS:
                _mark_missing_and_notify(db, row, notifier)

        return 0


def _handle_all_missing(db: Db, rows: list[RenderRow], notifier: Notifier | None,
                        reason: str) -> None:
    for row in rows:
        attempts = _bump_confirm_attempts(db, row.post_id)
        log.warning("cannot confirm %s (%s, attempt %d/%d)",
                    row.post_id, reason, attempts, _MAX_CONFIRM_ATTEMPTS)
        if attempts >= _MAX_CONFIRM_ATTEMPTS:
            _mark_missing_and_notify(db, row, notifier)


def _mark_missing_and_notify(db: Db, row: RenderRow, notifier: Notifier | None) -> None:
    # Terminal 'posted_missing' state — flip via direct UPDATE since it's
    # not part of the state-machine helpers.
    db._conn.execute(  # noqa: SLF001
        "UPDATE used SET upload_status = 'posted_missing' WHERE post_id = ? AND upload_status = ?",
        (row.post_id, UPLOAD_POSTED_UNDER_REVIEW),
    )
    log.error("giving up on %s → posted_missing", row.post_id)
    if notifier is not None:
        try:
            notifier.send_text(
                f"⚠️ <b>Confirm-live gave up on {row.post_id}</b>\n"
                f"<b>r/{row.subreddit}</b>\n"
                f"<i>{_html_escape(row.title)[:200]}</i>\n\n"
                "Video may still be pending TikTok moderation. "
                "Check @RealRedditStories manually.",
                parse_mode="HTML",
            )
        except NotifierError as e:
            log.warning("posted_missing notify failed: %s", e)


def _notify_confirmed(notifier: Notifier | None, row: RenderRow, url: str) -> None:
    if notifier is None:
        return
    try:
        notifier.send_text(
            f"✅ <b>Confirmed live</b> — {row.post_id}\n"
            f"<b>r/{row.subreddit}</b>\n"
            f"<i>{_html_escape(row.title)[:200]}</i>\n\n"
            f"{url}",
            parse_mode="HTML",
        )
    except NotifierError as e:
        log.warning("confirm notify failed: %s", e)


# ---- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Confirm-live scraper (Q16).")
    p.add_argument("--force", action="store_true",
                   help="skip the 30-minute age gate (still requires the row "
                        "to be `posted_under_review`).")
    args = p.parse_args(argv)
    return run_once(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
