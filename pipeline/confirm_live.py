"""Confirm-live via TikTok Display API (Q16).

Rows in `posted_under_review` that we uploaded ≥30 min ago get
promoted to `posted` once we can find the corresponding public video
URL by calling video.list on the Display API. No Playwright, no
Chromium — the API returns the same feed the profile page renders.

Assumes:
  * TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REFRESH_TOKEN
    are set in .env (see scripts/tiktok_oauth.py for how to mint them).

Match heuristic:
  * video.list returns the operator's own videos newest-first.
  * We take the top-N results and drop any URL already claimed by a
    `posted` row's tiktok_url.
  * Sort eligible under-review rows oldest-first. Newest unconfirmed
    row grabs the newest unclaimed API URL, and so on. Same pairing
    the old scrape used, just with a trustworthy source.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python pipeline/confirm_live.py` under systemd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import _load_dotenv
from core.db import Db, RenderRow, UPLOAD_POSTED_UNDER_REVIEW
from core.logging_setup import setup_logging
from core.notify import Notifier, NotifierError, _html_escape
from pipeline.tiktok_api import TikTokApiError, video_list

log = logging.getLogger("confirm_live")


_MIN_AGE_MIN = 30
_MAX_CONFIRM_ATTEMPTS = 6
_API_LIMIT = 20
_CFG_CONFIRM_ATTEMPTS = "confirm_attempts:{post_id}"


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


def _confirmed_urls(db: Db) -> set[str]:
    cur = db._conn.execute(  # noqa: SLF001
        "SELECT tiktok_url FROM used WHERE tiktok_url IS NOT NULL"
    )
    return {row[0] for row in cur.fetchall() if row[0]}


def _bump_confirm_attempts(db: Db, post_id: str) -> int:
    key = _CFG_CONFIRM_ATTEMPTS.format(post_id=post_id)
    prior = int(db.get_config(key, "0") or "0")
    now = prior + 1
    db.set_config(key, str(now))
    return now


def _clear_confirm_attempts(db: Db, post_id: str) -> None:
    key = _CFG_CONFIRM_ATTEMPTS.format(post_id=post_id)
    db.set_config(key, "0")


def _handle_all_missing(db: Db, rows: list[RenderRow], notifier: Notifier | None,
                        reason: str) -> None:
    for row in rows:
        attempts = _bump_confirm_attempts(db, row.post_id)
        log.warning("cannot confirm %s (%s, attempt %d/%d)",
                    row.post_id, reason, attempts, _MAX_CONFIRM_ATTEMPTS)
        if attempts >= _MAX_CONFIRM_ATTEMPTS:
            _mark_missing_and_notify(db, row, notifier)


def _mark_missing_and_notify(db: Db, row: RenderRow, notifier: Notifier | None) -> None:
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
                "Check the account manually.",
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


def run_once(*, force: bool = False) -> int:
    _load_dotenv()
    setup_logging()

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

        eligible.sort(key=lambda r: r.uploaded_at or "")

        try:
            vids, _, _ = video_list(max_count=_API_LIMIT)
        except TikTokApiError as exc:
            log.error("Display API video.list failed: %s", exc)
            _handle_all_missing(db, eligible, notifier, reason=f"api error: {exc}")
            return 1

        log.info("display API: %d videos returned", len(vids))
        if not vids:
            _handle_all_missing(db, eligible, notifier, reason="empty API result")
            return 0

        claimed = _confirmed_urls(db)
        unclaimed = [v for v in vids if v.share_url not in claimed]
        log.info("unclaimed video slots: %d", len(unclaimed))

        newest_first_rows = list(reversed(eligible))
        assignments: list[tuple[RenderRow, str]] = []
        for row in newest_first_rows:
            if not unclaimed:
                break
            v = unclaimed.pop(0)
            assignments.append((row, v.share_url))

        for row, url in assignments:
            db.confirm_posted(row.post_id, url)
            _clear_confirm_attempts(db, row.post_id)
            log.info("confirmed %s -> %s", row.post_id, url)
            _notify_confirmed(notifier, row, url)

        assigned_ids = {r.post_id for r, _ in assignments}
        for row in eligible:
            if row.post_id in assigned_ids:
                continue
            attempts = _bump_confirm_attempts(db, row.post_id)
            log.warning("no API slot for %s (attempt %d/%d)",
                        row.post_id, attempts, _MAX_CONFIRM_ATTEMPTS)
            if attempts >= _MAX_CONFIRM_ATTEMPTS:
                _mark_missing_and_notify(db, row, notifier)

        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Confirm-live via TikTok Display API (Q16).")
    p.add_argument("--force", action="store_true",
                   help="skip the 30-minute age gate (still requires the row "
                        "to be `posted_under_review`).")
    args = p.parse_args(argv)
    return run_once(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
