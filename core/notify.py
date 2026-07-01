from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from core.db import RenderRow

log = logging.getLogger(__name__)


_API_BASE = "https://api.telegram.org"

# Long-poll timeout (seconds). Telegram allows up to 50; 25 is a good tradeoff
# — keeps a socket open long enough that idle wake cost is near zero, but
# recovers within half a minute on network glitches.
_LONG_POLL_TIMEOUT_S = 25

# HTTP timeout for non-polling calls (sendMessage, editMessageText).
_HTTP_TIMEOUT_S = 20

# Telegram callback_data is capped at 64 bytes. Keep the format short.
_CB_APPROVE_PREFIX = "a:"
_CB_REJECT_PREFIX = "r:"

# Config keys used by the callback bot to persist getUpdates offset.
_CFG_TG_OFFSET = "telegram_update_offset"


class NotifierError(RuntimeError):
    pass


@dataclass(frozen=True)
class NotifierEnv:
    token: str
    chat_id: int


def _load_env() -> NotifierEnv:
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not tok or not chat:
        raise NotifierError("TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID must be set in .env")
    try:
        return NotifierEnv(token=tok, chat_id=int(chat))
    except ValueError as e:
        raise NotifierError(f"TELEGRAM_CHAT_ID must be an integer, got {chat!r}") from e


class Notifier:
    """Thin Telegram Bot API client scoped to a single chat."""

    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self._base = f"{_API_BASE}/bot{self.token}"

    @classmethod
    def from_env(cls) -> "Notifier":
        env = _load_env()
        return cls(token=env.token, chat_id=env.chat_id)

    # ---------------- HTTP plumbing ----------------

    def _post(self, method: str, payload: dict[str, Any], *,
              files: dict | None = None, timeout: float = _HTTP_TIMEOUT_S) -> dict[str, Any]:
        url = f"{self._base}/{method}"
        try:
            if files is None:
                resp = requests.post(url, json=payload, timeout=timeout)
            else:
                resp = requests.post(url, data=payload, files=files, timeout=timeout)
        except requests.RequestException as e:
            raise NotifierError(f"telegram {method} network error: {e}") from e
        try:
            body = resp.json()
        except ValueError as e:
            raise NotifierError(f"telegram {method} non-JSON response: {resp.text[:200]!r}") from e
        if not body.get("ok"):
            raise NotifierError(f"telegram {method} failed: {body}")
        return body.get("result") or {}

    def _get(self, method: str, params: dict[str, Any] | None = None,
             *, timeout: float = _HTTP_TIMEOUT_S) -> dict[str, Any]:
        url = f"{self._base}/{method}"
        try:
            resp = requests.get(url, params=params or {}, timeout=timeout)
        except requests.RequestException as e:
            raise NotifierError(f"telegram {method} network error: {e}") from e
        body = resp.json()
        if not body.get("ok"):
            raise NotifierError(f"telegram {method} failed: {body}")
        return body.get("result") or {}

    # ---------------- Outbound: text / photo / edits ----------------

    def send_text(self, text: str, *, parse_mode: str | None = None,
                  disable_notification: bool = False) -> int:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = self._post("sendMessage", payload)
        return int(result.get("message_id", 0))

    def send_review_request(self, row: RenderRow) -> int:
        """Send the render's cover PNG + caption preview + inline
        Approve/Reject keyboard. Returns the Telegram message_id."""
        cover = Path(row.cover_path)
        if not cover.exists():
            raise NotifierError(f"cover missing: {cover}")

        preview_caption = (
            f"📥 <b>New render — {row.post_id}</b>\n"
            f"<b>r/{row.subreddit}</b> · u/{row.author or '?'}\n\n"
            f"<i>{_html_escape(row.title)[:280]}</i>\n\n"
            f"<pre>{_html_escape(row.caption)[:900]}</pre>"
        )
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"{_CB_APPROVE_PREFIX}{row.post_id}"},
                {"text": "❌ Reject",  "callback_data": f"{_CB_REJECT_PREFIX}{row.post_id}"},
            ]]
        }
        payload = {
            "chat_id": self.chat_id,
            "caption": preview_caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard),
        }
        with cover.open("rb") as f:
            result = self._post("sendPhoto", payload, files={"photo": f}, timeout=60)
        return int(result.get("message_id", 0))

    def edit_review_caption(self, message_id: int, suffix: str) -> None:
        """Append `suffix` to the review-request caption and strip the buttons.
        Called after Approve/Reject so the buttons go inert."""
        try:
            self._post("editMessageReplyMarkup", {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "reply_markup": json.dumps({"inline_keyboard": []}),
            })
        except NotifierError as e:
            log.warning("edit reply markup failed for %d: %s", message_id, e)
        try:
            self._post("editMessageCaption", {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "caption": suffix,
                "parse_mode": "HTML",
            })
        except NotifierError as e:
            log.warning("edit caption failed for %d: %s", message_id, e)

    def answer_callback(self, callback_query_id: str, text: str = "", *,
                        show_alert: bool = False) -> None:
        try:
            self._post("answerCallbackQuery", {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            })
        except NotifierError as e:
            log.warning("answerCallbackQuery failed: %s", e)

    # ---------------- Inbound: long-poll ----------------

    def poll_updates(self, offset: int) -> list[dict[str, Any]]:
        """Blocking long-poll. Returns updates and the next offset the caller
        should persist (max(update.update_id)+1). On network error, returns
        [] so caller can retry."""
        try:
            result = self._get(
                "getUpdates",
                params={
                    "offset": offset,
                    "timeout": _LONG_POLL_TIMEOUT_S,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
                timeout=_LONG_POLL_TIMEOUT_S + 10,
            )
        except NotifierError as e:
            log.warning("getUpdates transient error: %s — sleeping 5s", e)
            time.sleep(5)
            return []
        return result if isinstance(result, list) else []


# ---------------- Utilities ----------------

def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# ---------------- Callback bot loop ----------------

def _handle_callback(notifier: Notifier, db, query: dict[str, Any]) -> None:
    from core.db import Db  # for type hinting only
    _: Db = db  # noqa

    data = (query.get("data") or "").strip()
    q_id = query.get("id") or ""
    msg = query.get("message") or {}
    msg_id = int(msg.get("message_id") or 0)

    if data.startswith(_CB_APPROVE_PREFIX):
        post_id = data[len(_CB_APPROVE_PREFIX):]
        ok = db.approve(post_id)
        if ok:
            notifier.answer_callback(q_id, "✅ Approved")
            notifier.edit_review_caption(msg_id, f"✅ <b>Approved</b> — {post_id}")
            log.info("approved %s via telegram", post_id)
        else:
            notifier.answer_callback(q_id, "Already decided", show_alert=True)
    elif data.startswith(_CB_REJECT_PREFIX):
        post_id = data[len(_CB_REJECT_PREFIX):]
        row = db.get_render(post_id)
        ok = db.reject(post_id)
        if ok:
            # Delete artifacts.
            for p in (row.video_path, row.cover_path) if row else ():
                if p and Path(p).exists():
                    try:
                        Path(p).unlink()
                    except OSError as e:
                        log.warning("failed to delete %s: %s", p, e)
            notifier.answer_callback(q_id, "❌ Rejected")
            notifier.edit_review_caption(msg_id, f"❌ <b>Rejected</b> — {post_id}")
            log.info("rejected %s via telegram", post_id)
        else:
            notifier.answer_callback(q_id, "Already decided", show_alert=True)
    else:
        notifier.answer_callback(q_id, "unknown action", show_alert=True)


def _handle_message(notifier: Notifier, db, msg: dict[str, Any]) -> None:
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd = text.split()[0].lower()

    if cmd in ("/pause", "/stop"):
        db.set_uploads_enabled(False)
        notifier.send_text("⏸ uploads paused")
    elif cmd in ("/resume", "/start"):
        db.set_uploads_enabled(True)
        notifier.send_text("▶ uploads resumed")
    elif cmd == "/status":
        pending = len(db.pending_renders())
        review = len(db.under_review())
        today = db.posts_today(1)  # CET offset
        last = db.last_uploaded_at() or "never"
        enabled = db.is_uploads_enabled()
        notifier.send_text(
            f"📊 pending: {pending}\n"
            f"under review: {review}\n"
            f"posted today: {today}/2\n"
            f"last upload: {last}\n"
            f"uploads enabled: {'yes' if enabled else 'no'}"
        )
    else:
        notifier.send_text(f"unknown command: {cmd}. commands: /pause /resume /status")


def run_callback_bot(db, *, notifier: Notifier | None = None,
                     stop_after_s: float | None = None) -> None:
    """Long-poll `getUpdates` and dispatch to _handle_callback / _handle_message.
    Persists the getUpdates offset in the DB config table so restarts skip
    already-processed updates.

    stop_after_s: return after ~this many seconds (test hook). None = run
    forever."""
    notifier = notifier or Notifier.from_env()
    start = time.monotonic()

    offset_raw = db.get_config(_CFG_TG_OFFSET, "0") or "0"
    try:
        offset = int(offset_raw)
    except ValueError:
        offset = 0

    log.info("telegram bot: starting from offset %d", offset)

    while True:
        if stop_after_s is not None and time.monotonic() - start > stop_after_s:
            log.info("telegram bot: stop_after_s elapsed, returning")
            return

        updates = notifier.poll_updates(offset)
        for u in updates:
            uid = int(u.get("update_id", 0))
            if uid >= offset:
                offset = uid + 1
            try:
                if "callback_query" in u:
                    _handle_callback(notifier, db, u["callback_query"])
                elif "message" in u:
                    _handle_message(notifier, db, u["message"])
            except Exception as e:  # never let a bad update kill the loop
                log.exception("update %d handler crashed: %s", uid, e)

        if updates:
            db.set_config(_CFG_TG_OFFSET, str(offset))
