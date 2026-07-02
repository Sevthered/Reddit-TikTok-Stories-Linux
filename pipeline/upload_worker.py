"""One-shot upload worker for Phase 6.

Invoked by the tiktok-slot-upload@HHMM.service systemd oneshot at each
scheduled publish minute (00/06/12/18 Europe/Madrid) with `--post-id`,
or by hand for ad-hoc uploads. Each invocation:

  1. checks the gates (window, kill switch, cadence, spacing)
  2. calls `db.claim_next_upload()` (or `claim_specific_upload(post_id)`)
     to atomically pick a row
  3. drives `upload_to_tiktok()` to actually post
  4. records success/failure back to the DB and pings Telegram

Exit codes:
  0 : posted successfully (or nothing to do / gates closed — same OK exit
      because we don't want systemd to treat idle ticks as failed units)
  1 : upload attempted but failed (transient or terminal)
  2 : hard error (config, cookies missing, telegram env missing, etc.)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running as a script (`python pipeline/upload_worker.py`) — systemd
# oneshot units invoke us that way. Without this, `from core.config import ...`
# fails because the CWD isn't the project root by default.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import _load_dotenv
from core.db import Db, RenderRow
from core.logging_setup import setup_logging
from core.notify import Notifier, NotifierError, _html_escape
from core.schedule import EffectiveSlotCfg, effective_slot_cfg
from pipeline.upload import (
    TikTokAuthError,
    TikTokDOMError,
    UploadError,
    UploadResult,
    sessionid_expires_in_days,
    upload_to_tiktok,
)

log = logging.getLogger("upload_worker")


# Policy constants (mirror phase-6-posting-policy + phase-6-ops ADRs).
_POST_TZ = ZoneInfo("Europe/Madrid")
_POST_WINDOW_HOURS = {0, 12}                       # slot cadence (00/12 CEST)
_MIN_SPACING_HOURS = 2                             # Q9

_PAUSE_FLAG = Path("data/PAUSE_UPLOADS")

_SESSIONID_WARN_DAYS = 3.0
_CFG_LAST_EXPIRY_ALERT = "sessionid_expiry_last_alert_date"


# ---- Gate checks ----------------------------------------------------------

def _gates_pass(db: Db, now_madrid: datetime) -> tuple[bool, str]:
    """Return (allow, reason). All gates must pass to permit upload."""
    if _PAUSE_FLAG.exists():
        return False, "PAUSE_UPLOADS flag file present"
    if not db.is_uploads_enabled():
        return False, "uploads_enabled=0 in config (Telegram /pause)"
    if now_madrid.hour not in _POST_WINDOW_HOURS:
        return False, f"outside slot window {{00,12}} CEST (hour={now_madrid.hour})"
    last = db.last_uploaded_at()
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            last_dt = None
        if last_dt:
            since = datetime.now(timezone.utc) - last_dt
            if since < timedelta(hours=_MIN_SPACING_HOURS):
                mins = int(since.total_seconds() / 60)
                return False, f"spacing: last upload {mins}m ago (<{_MIN_SPACING_HOURS}h)"
    return True, ""


# ---- Session-id expiry pre-flight -----------------------------------------

def _maybe_alert_session_expiry(notifier: Notifier | None, db: Db) -> None:
    """Once per calendar date (Madrid tz), alert Telegram if `sessionid`
    expires in fewer than _SESSIONID_WARN_DAYS days."""
    days = sessionid_expires_in_days()
    if days is None:
        return
    if days >= _SESSIONID_WARN_DAYS:
        return

    today = datetime.now(_POST_TZ).date().isoformat()
    last = db.get_config(_CFG_LAST_EXPIRY_ALERT, "") or ""
    if last == today:
        return
    if notifier is not None:
        try:
            notifier.send_text(
                f"🍪 <b>TikTok sessionid expires in {days:.1f} days</b>\n"
                "Re-export the cookie jar before it dies:\n"
                "1. Log into Chrome as @RealRedditStories\n"
                "2. Export cookies with 'Get cookies.txt LOCALLY'\n"
                "3. Overwrite <code>data/cookies/tiktok_cookies.txt</code>",
                parse_mode="HTML",
            )
        except NotifierError as e:
            log.warning("could not send sessionid expiry alert: %s", e)
    db.set_config(_CFG_LAST_EXPIRY_ALERT, today)


# ---- Telegram notifications -----------------------------------------------

def _notify_success(notifier: Notifier | None, row: RenderRow,
                    result: UploadResult,
                    cfg: EffectiveSlotCfg | None) -> None:
    if notifier is None:
        return
    if cfg is not None and not cfg.notify_upload_success:
        return
    text = (
        f"🚀 <b>Posted</b> — {row.post_id}\n"
        f"<b>r/{row.subreddit}</b> · u/{row.author or '?'}\n\n"
        f"<i>{_html_escape(row.title)[:200]}</i>\n\n"
        f"visibility: <code>{result.visibility}</code>\n"
        f"studio: <code>{result.tiktok_url or '(no url yet)'}</code>\n"
        f"awaiting confirm-live scrape"
    )
    try:
        notifier.send_text(text, parse_mode="HTML")
    except NotifierError as e:
        log.warning("success notify failed: %s", e)


def _notify_failure(notifier: Notifier | None, row: RenderRow, err: Exception,
                    attempts: int, terminal: bool,
                    cfg: EffectiveSlotCfg | None) -> None:
    if notifier is None:
        return
    if cfg is not None and not cfg.notify_upload_failure:
        return
    header = ("❌ <b>Upload failed (terminal)</b>"
              if terminal else f"⚠️ <b>Upload failed ({attempts}/3)</b>")
    body = (
        f"{header} — {row.post_id}\n"
        f"<b>r/{row.subreddit}</b>\n"
        f"<pre>{_html_escape(str(err))[:600]}</pre>"
    )
    if terminal:
        body += "\n<i>no more retries; row marked failed</i>"
    else:
        body += "\n<i>will retry with backoff</i>"
    try:
        notifier.send_text(body, parse_mode="HTML")
    except NotifierError as e:
        log.warning("failure notify failed: %s", e)


def _notify_gate_reject(notifier: Notifier | None, cfg: EffectiveSlotCfg | None,
                        reason: str) -> None:
    """Opt-in DM when the upload gate rejects. Off by default so today's
    log-only behavior stays intact; per-slot `notify_upload_gate_reject`
    turns it on."""
    if notifier is None or cfg is None or not cfg.notify_upload_gate_reject:
        return
    try:
        notifier.send_text(
            f"⛔ <b>Upload gate rejected slot {cfg.instance}</b>\n"
            f"<pre>{_html_escape(reason)[:400]}</pre>",
            parse_mode="HTML",
        )
    except NotifierError as e:
        log.warning("gate-reject notify failed: %s", e)


# ---- Core run --------------------------------------------------------------

def run_once(*, force: bool = False, dry_run: bool = False,
             visibility: str = "public", aigc: bool = True,
             post_id: str | None = None,
             instance: str | None = None) -> int:
    _load_dotenv()
    setup_logging()

    now_madrid = datetime.now(_POST_TZ)
    log.info("upload_worker tick @ %s", now_madrid.isoformat(timespec="seconds"))

    # Notifier is optional — the worker still runs if Telegram env is unset;
    # we just skip user alerts.
    notifier: Notifier | None
    try:
        notifier = Notifier.from_env()
    except NotifierError as e:
        log.warning("no Telegram notifier (env missing): %s", e)
        notifier = None

    with Db.open() as db:
        _maybe_alert_session_expiry(notifier, db)

        # Slot-scoped effective config gates the notify.upload.* toggles.
        # When --instance is not supplied (e.g. manual invocation), cfg
        # stays None and every notifier call keeps its today-behavior.
        cfg: EffectiveSlotCfg | None = None
        if instance is not None:
            try:
                cfg = effective_slot_cfg(instance, db)
            except KeyError:
                log.warning("unknown slot instance %r — ignoring, using global defaults",
                            instance)

        if cfg is not None and not cfg.upload_enabled and not force:
            log.info("slot %s upload disabled in DB config — nothing to do", instance)
            return 0

        allow, reason = _gates_pass(db, now_madrid)
        if not allow and not force:
            log.info("gates closed: %s — nothing to do", reason)
            _notify_gate_reject(notifier, cfg, reason)
            return 0

        if post_id is not None:
            row = db.claim_specific_upload(post_id)
            if row is None:
                log.info("post_id %s not in `approved` state — nothing to do", post_id)
                return 0
        else:
            row = db.claim_next_upload()
            if row is None:
                log.info("no approved rows waiting; nothing to do")
                return 0

        log.info("claimed %s (attempt %d) → uploading",
                 row.post_id, row.upload_attempts + 1)

        if dry_run:
            log.info("dry-run: would upload %s (%s)", row.post_id, row.video_path)
            db.release_uploading_claim(row.post_id)
            return 0

        started = time.time()
        try:
            result = upload_to_tiktok(
                post_id=row.post_id,
                video_path=row.video_path,
                cover_path=None,   # first frame of the burned card is the cover
                caption=row.caption,
                visibility=visibility,  # type: ignore[arg-type]
                aigc=aigc,
            )
        except (TikTokAuthError, TikTokDOMError, UploadError) as e:
            elapsed = time.time() - started
            log.exception("upload failed after %.1fs: %s", elapsed, e)
            attempts, terminal = db.mark_upload_failure(row.post_id, str(e))
            _notify_failure(notifier, row, e, attempts, terminal, cfg)
            return 1
        except Exception as e:
            elapsed = time.time() - started
            log.exception("unexpected error after %.1fs", elapsed)
            attempts, terminal = db.mark_upload_failure(row.post_id, f"unexpected: {e}")
            _notify_failure(notifier, row, e, attempts, terminal, cfg)
            return 1

        elapsed = time.time() - started
        log.info("uploaded %s in %.1fs — tiktok_url=%s",
                 row.post_id, elapsed, result.tiktok_url)
        db.mark_upload_success(row.post_id)
        _notify_success(notifier, row, result, cfg)
        return 0


# ---- CLI ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="One-shot Phase 6 upload worker (invoked by systemd tiktok-slot-upload@ or by hand)."
    )
    p.add_argument("--force", action="store_true",
                   help="bypass window/cadence/spacing gates (still respects "
                        "PAUSE_UPLOADS and /pause). For manual testing.")
    p.add_argument("--dry-run", action="store_true",
                   help="claim a row and pretend to upload, then release "
                        "it back to `approved`. Doesn't touch TikTok.")
    p.add_argument("--visibility", default="public",
                   choices=("public", "only_me", "friends"),
                   help="visibility to publish under. Default 'public'. "
                        "Use 'only_me' for smoke-testing without going live.")
    p.add_argument("--no-aigc", dest="aigc", action="store_false",
                   help="do NOT flip TikTok's AIGC (AI-generated content) "
                        "toggle. Default is ON — safer w.r.t. TikTok's "
                        "AI-content policy.")
    p.set_defaults(aigc=True)
    p.add_argument("--post-id", default=None,
                   help="claim this specific approved row instead of the "
                        "oldest one (used by the Telegram picker).")
    p.add_argument("--instance", default=None,
                   help="slot instance name (e.g. '0000', '1200'). When "
                        "supplied, the worker reads per-slot notify.upload.* "
                        "toggles + upload.enabled from the DB config. Omit "
                        "for manual invocations that should use global "
                        "notifier defaults.")
    args = p.parse_args(argv)
    return run_once(force=args.force, dry_run=args.dry_run,
                    visibility=args.visibility, aigc=args.aigc,
                    post_id=args.post_id, instance=args.instance)


if __name__ == "__main__":
    sys.exit(main())
