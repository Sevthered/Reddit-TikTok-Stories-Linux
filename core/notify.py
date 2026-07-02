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

    def edit_message_text(self, message_id: int, text: str, *,
                          parse_mode: str | None = "HTML",
                          reply_markup: dict[str, Any] | None = None) -> None:
        """Rewrite a plain-text message. Used for the render progress
        checklist and the inline-keyboard config screens. Failures are
        logged, not raised — the caller shouldn't crash because a chat
        got deleted."""
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            self._post("editMessageText", payload)
        except NotifierError as e:
            log.warning("editMessageText %d failed: %s", message_id, e)

    def send_text_with_markup(self, text: str, reply_markup: dict[str, Any], *,
                              parse_mode: str | None = "HTML") -> int:
        """Send a text message with an inline keyboard attached. Returns
        message_id so callers can edit it later (e.g. after the user
        taps Start we blank the keyboard)."""
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "reply_markup": json.dumps(reply_markup),
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = self._post("sendMessage", payload)
        return int(result.get("message_id", 0))

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

    if data == "noop":
        notifier.answer_callback(q_id)
        return

    if data.startswith(_CB_APPROVE_PREFIX):
        post_id = data[len(_CB_APPROVE_PREFIX):]
        ok = db.approve(post_id)
        if ok:
            notifier.answer_callback(q_id, "✅ Approved")
            notifier.edit_review_caption(msg_id, f"✅ <b>Approved</b> — {post_id}")
            log.info("approved %s via telegram", post_id)
        else:
            notifier.answer_callback(q_id, "Already decided", show_alert=True)
        return

    if data.startswith(_CB_REJECT_PREFIX):
        post_id = data[len(_CB_REJECT_PREFIX):]
        row = db.get_render(post_id)
        ok = db.reject(post_id)
        if ok:
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
        return

    # ---- Config-screen navigation --------------------------------------
    if data.startswith("nav|"):
        _handle_nav(notifier, msg_id, q_id, data)
        return

    if data.startswith("r|"):
        _handle_render_cb(notifier, msg_id, q_id, data)
        return

    if data == "upl_pick" or data.startswith("upl_pick|"):
        _handle_upload_picker(notifier, msg_id, q_id, data)
        return

    if data.startswith("u|"):
        _handle_upload_cb(notifier, msg_id, q_id, data)
        return

    if data.startswith("c|"):
        _handle_confirm_cb(notifier, msg_id, q_id, data)
        return

    if data.startswith("web|"):
        _handle_webapp_cb(notifier, msg_id, q_id, data)
        return

    notifier.answer_callback(q_id, "unknown action", show_alert=True)


def _handle_nav(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    from core import tg_flows
    kind = data.split("|", 1)[1]
    if kind == "render":
        notifier.edit_message_text(
            msg_id, tg_flows.render_text(1, False),
            reply_markup=tg_flows.render_keyboard(1, False),
        )
    elif kind == "upload":
        # Show the picker FIRST — user chooses target, then lands on the
        # settings screen with that post_id baked in.
        _render_upload_picker(notifier, msg_id)
    elif kind == "confirm":
        notifier.edit_message_text(
            msg_id, tg_flows.confirm_text(False),
            reply_markup=tg_flows.confirm_keyboard(False),
        )
    notifier.answer_callback(q_id)


def _render_upload_picker(notifier: Notifier, msg_id: int) -> None:
    """Fetch approved rows from the webapp and repaint `msg_id` as a
    picker. Falls back to a friendly empty state."""
    from core import tg_flows
    from core.webapp_client import WebappClient, WebappError
    try:
        approved = WebappClient().list_approved()
    except WebappError as e:
        notifier.edit_message_text(msg_id, f"🚀 <b>Upload</b>\n\n❌ {e}")
        return
    notifier.edit_message_text(
        msg_id,
        tg_flows.upload_picker_text(approved),
        reply_markup=tg_flows.upload_picker_keyboard(approved),
    )


def _handle_upload_picker(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    """`upl_pick` alone = re-list. `upl_pick|<post_id>` = go to settings
    with that target."""
    from core import tg_flows

    if data == "upl_pick":
        _render_upload_picker(notifier, msg_id)
        notifier.answer_callback(q_id)
        return

    pid_raw = data.split("|", 1)[1]
    post_id: str | None = None if pid_raw == tg_flows.NEXT_TOKEN else pid_raw

    # Look up the row title so the settings screen can preview what
    # you're about to send.
    from core.webapp_client import WebappClient, WebappError
    title: str | None = None
    if post_id:
        try:
            for r in WebappClient().list_approved():
                if r["post_id"] == post_id:
                    title = r.get("title")
                    break
        except WebappError:
            pass

    notifier.edit_message_text(
        msg_id,
        tg_flows.upload_text("only_me", True, False, True, post_id=post_id, title=title),
        reply_markup=tg_flows.upload_keyboard("only_me", True, False, True,
                                              post_id=post_id),
    )
    notifier.answer_callback(q_id)


def _handle_render_cb(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    from core import tg_flows
    from core.webapp_client import WebappClient, WebappError

    parsed = tg_flows.parse_render(data)
    if parsed is None:
        notifier.answer_callback(q_id, "bad payload", show_alert=True)
        return
    limit, dry, action = parsed

    if action == "s":
        # Reset the message to a checklist stub so main.py can edit it
        # in place, and blank the keyboard so the user can't re-click.
        notifier.edit_message_text(
            msg_id,
            f"🎬 <b>Rendering</b>\nlimit={limit} dry={'on' if dry else 'off'}\n\n⏳ queued…",
            reply_markup={"inline_keyboard": []},
        )
        try:
            client = WebappClient()
            job = client.start_render(
                limit=limit, dry_run=dry,
                progress_chat_id=notifier.chat_id,
                progress_message_id=msg_id,
            )
            notifier.answer_callback(q_id, f"job {job.get('id','?')} started")
        except WebappError as e:
            notifier.edit_message_text(msg_id, f"❌ render failed to start: {e}")
            notifier.answer_callback(q_id, "start failed", show_alert=True)
        return

    # Toggles just repaint the same message.
    notifier.edit_message_text(
        msg_id, tg_flows.render_text(limit, dry),
        reply_markup=tg_flows.render_keyboard(limit, dry),
    )
    notifier.answer_callback(q_id)


def _handle_upload_cb(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    from core import tg_flows
    from core.webapp_client import WebappClient, WebappError

    parsed = tg_flows.parse_upload(data)
    if parsed is None:
        notifier.answer_callback(q_id, "bad payload", show_alert=True)
        return
    post_id, vis, aigc, force, dry, action = parsed

    if action == "s":
        target = post_id or "oldest"
        summary_hdr = (
            f"🚀 <b>Upload</b>\ntarget=<code>{target}</code> vis={vis} "
            f"aigc={'on' if aigc else 'off'} "
            f"force={'on' if force else 'off'} dry={'on' if dry else 'off'}"
        )
        notifier.edit_message_text(
            msg_id, f"{summary_hdr}\n\n⏳ queued…",
            reply_markup={"inline_keyboard": []},
        )
        try:
            client = WebappClient()
            job = client.start_upload(visibility=vis, force=force,
                                      dry_run=dry, aigc=aigc, post_id=post_id)
            job_id = job.get("id", "?")
            notifier.answer_callback(q_id, f"job {job_id} started")
            final = client.wait_for_short_job(job_id, timeout_s=5.0)
            _finalize_upload_message(notifier, client, msg_id, summary_hdr, final)
        except WebappError as e:
            notifier.edit_message_text(msg_id, f"{summary_hdr}\n\n❌ failed to start: {e}")
            notifier.answer_callback(q_id, "start failed", show_alert=True)
        return

    notifier.edit_message_text(
        msg_id, tg_flows.upload_text(vis, aigc, force, dry, post_id=post_id),
        reply_markup=tg_flows.upload_keyboard(vis, aigc, force, dry, post_id=post_id),
    )
    notifier.answer_callback(q_id)


def _handle_confirm_cb(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    from core import tg_flows
    from core.webapp_client import WebappClient, WebappError

    parsed = tg_flows.parse_confirm(data)
    if parsed is None:
        notifier.answer_callback(q_id, "bad payload", show_alert=True)
        return
    force, action = parsed

    if action == "s":
        summary_hdr = f"🔍 <b>Confirm</b>\nforce={'on' if force else 'off'}"
        notifier.edit_message_text(
            msg_id, f"{summary_hdr}\n\n⏳ queued…",
            reply_markup={"inline_keyboard": []},
        )
        try:
            client = WebappClient()
            job = client.start_confirm(force=force)
            job_id = job.get("id", "?")
            notifier.answer_callback(q_id, f"job {job_id} started")
            final = client.wait_for_short_job(job_id, timeout_s=10.0)
            _finalize_confirm_message(notifier, client, msg_id, summary_hdr, final)
        except WebappError as e:
            notifier.edit_message_text(msg_id, f"{summary_hdr}\n\n❌ failed to start: {e}")
            notifier.answer_callback(q_id, "start failed", show_alert=True)
        return

    notifier.edit_message_text(
        msg_id, tg_flows.confirm_text(force),
        reply_markup=tg_flows.confirm_keyboard(force),
    )
    notifier.answer_callback(q_id)


def _finalize_upload_message(notifier, client, msg_id: int, hdr: str,
                             final_job: dict[str, Any]) -> None:
    """Called after wait_for_short_job. Renders an outcome badge into
    the Telegram msg so the user knows if the upload actually ran or
    skipped for a reason (gates closed / dry-run / auth error)."""
    if final_job.get("running"):
        notifier.edit_message_text(
            msg_id,
            f"{hdr}\n\n⏳ running (long upload — check Jobs page for tail)",
        )
        return
    exit_code = final_job.get("exit_code")
    tail = client.job_tail_text(final_job.get("id", ""), timeout_s=1.5,
                                max_bytes=1200)
    last = _last_meaningful_line(tail)
    if exit_code == 0:
        # Gates-closed / dry-run exit cleanly with a `nothing to do` /
        # `dry-run: would upload` INFO line at the end. Bubble it up.
        badge = "⏸" if ("nothing to do" in last or "gates closed" in last) else "✅"
        notifier.edit_message_text(
            msg_id,
            f"{hdr}\n\n{badge} exit 0\n<pre>{_html_escape(last[:400])}</pre>",
            parse_mode="HTML",
        )
    else:
        notifier.edit_message_text(
            msg_id,
            f"{hdr}\n\n❌ exit {exit_code}\n<pre>{_html_escape(last[:400])}</pre>",
            parse_mode="HTML",
        )


def _finalize_confirm_message(notifier, client, msg_id: int, hdr: str,
                              final_job: dict[str, Any]) -> None:
    if final_job.get("running"):
        notifier.edit_message_text(msg_id, f"{hdr}\n\n⏳ still scraping…")
        return
    exit_code = final_job.get("exit_code")
    tail = client.job_tail_text(final_job.get("id", ""), timeout_s=1.5,
                                max_bytes=1200)
    last = _last_meaningful_line(tail)
    badge = "✅" if exit_code == 0 else "❌"
    notifier.edit_message_text(
        msg_id,
        f"{hdr}\n\n{badge} exit {exit_code}\n<pre>{_html_escape(last[:400])}</pre>",
        parse_mode="HTML",
    )


def _last_meaningful_line(tail: str) -> str:
    """Grab the last non-empty log line from a stream tail. Skips SSE
    heartbeats + framing noise."""
    for ln in reversed(tail.splitlines()):
        s = ln.strip()
        if s and s != "ping" and not s.isdigit():
            return s
    return "(no output)"


_WEBAPP_UNIT = "tiktok-webapp.service"


def _webapp_state() -> tuple[str, str, str]:
    """Return (active_state, sub_state, listen_hint).

    active_state ∈ {active, inactive, activating, deactivating, failed, ...}
    sub_state    e.g. `running`, `dead`, `start-post`.
    listen_hint  human-friendly URL if the process is up, "" otherwise.
    """
    import socket
    import subprocess as _sp
    try:
        out = _sp.run(
            ["systemctl", "show", _WEBAPP_UNIT,
             "-p", "ActiveState", "-p", "SubState", "--no-page"],
            capture_output=True, text=True, timeout=5,
        )
        fields = {}
        for line in out.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                fields[k.strip()] = v.strip()
        active = fields.get("ActiveState", "unknown")
        sub = fields.get("SubState", "unknown")
    except (_sp.TimeoutExpired, FileNotFoundError):
        return "unknown", "unknown", ""

    hint = ""
    if active == "active":
        try:
            hint = f"http://{socket.gethostbyname(socket.gethostname())}:8765"
        except OSError:
            hint = "http://<server-ip>:8765"
    return active, sub, hint


def _webapp_action(action: str) -> tuple[int, str]:
    """Run `systemctl <action> tiktok-webapp.service` (start|stop|restart).
    Returns (exit_code, merged_output)."""
    import subprocess as _sp
    proc = _sp.run(
        ["systemctl", action, _WEBAPP_UNIT],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _webapp_keyboard(active_state: str) -> dict:
    """Inline buttons: show Start when down, Stop when up. Always show Refresh."""
    if active_state == "active":
        rows = [[
            {"text": "🔴 Stop", "callback_data": "web|stop"},
            {"text": "🔄 Refresh", "callback_data": "web|refresh"},
        ]]
    else:
        rows = [[
            {"text": "🟢 Start", "callback_data": "web|start"},
            {"text": "🔄 Refresh", "callback_data": "web|refresh"},
        ]]
    return {"inline_keyboard": rows}


def _webapp_status_text(active_state: str, sub_state: str, hint: str) -> str:
    emoji = {"active": "🟢", "inactive": "⚫️", "failed": "🔴"}.get(active_state, "⚪️")
    lines = [f"🌐 <b>Web app</b>: {emoji} <code>{_html_escape(active_state)}</code> "
             f"(<code>{_html_escape(sub_state)}</code>)"]
    if hint:
        lines.append(f"→ <a href=\"{hint}\">{hint}</a>")
    return "\n".join(lines)


def _handle_webapp_cmd(notifier: Notifier, text: str) -> None:
    """Handle `/webapp`, `/webapp on`, `/webapp off` slash commands."""
    parts = text.split()
    arg = parts[1].lower() if len(parts) > 1 else ""

    if arg in ("on", "up", "start"):
        rc, out = _webapp_action("start")
        if rc != 0:
            notifier.send_text(f"❌ start failed (exit={rc}):\n<pre>{_html_escape(out[:400])}</pre>",
                               parse_mode="HTML")
            return
    elif arg in ("off", "down", "stop"):
        rc, out = _webapp_action("stop")
        if rc != 0:
            notifier.send_text(f"❌ stop failed (exit={rc}):\n<pre>{_html_escape(out[:400])}</pre>",
                               parse_mode="HTML")
            return
    elif arg in ("restart", "kickstart"):
        rc, out = _webapp_action("restart")
        if rc != 0:
            notifier.send_text(f"❌ restart failed (exit={rc}):\n<pre>{_html_escape(out[:400])}</pre>",
                               parse_mode="HTML")
            return
    elif arg not in ("", "status"):
        notifier.send_text("usage: /webapp [on|off|restart|status]")
        return

    active, sub, hint = _webapp_state()
    notifier.send_text_with_markup(
        _webapp_status_text(active, sub, hint),
        _webapp_keyboard(active),
        parse_mode="HTML",
    )


def _handle_webapp_cb(notifier: Notifier, msg_id: int, q_id: str, data: str) -> None:
    """Inline-button dispatch for the /webapp status card."""
    action = data.split("|", 1)[1] if "|" in data else ""
    if action == "start":
        rc, out = _webapp_action("start")
        notifier.answer_callback(q_id, "starting" if rc == 0 else f"exit {rc}")
    elif action == "stop":
        rc, out = _webapp_action("stop")
        notifier.answer_callback(q_id, "stopping" if rc == 0 else f"exit {rc}")
    elif action == "refresh":
        notifier.answer_callback(q_id)
    else:
        notifier.answer_callback(q_id, "unknown action", show_alert=True)
        return

    # Give systemd ~800ms to settle then repaint.
    time.sleep(0.8)
    active, sub, hint = _webapp_state()
    notifier.edit_message_text(
        msg_id,
        _webapp_status_text(active, sub, hint),
        reply_markup=_webapp_keyboard(active),
        parse_mode="HTML",
    )


def _handle_message(notifier: Notifier, db, msg: dict[str, Any]) -> None:
    from core import tg_flows
    from core.agents import list_agent_status
    from core.time_fmt import pretty

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
        today = db.posts_today(1)
        last = db.last_uploaded_at()
        enabled = db.is_uploads_enabled()
        agents = list_agent_status()
        agent_lines = "\n".join(
            f"  <code>{a.label.replace('tiktok-','')}</code>: "
            f"{'pid ' + str(a.pid) if a.pid else ('loaded' if a.loaded else 'unloaded')}"
            for a in agents
        )
        notifier.send_text(
            "📊 <b>Status</b>\n"
            f"pending: <b>{pending}</b>\n"
            f"under review: <b>{review}</b>\n"
            f"posted today: <b>{today}/2</b>\n"
            f"last upload: {pretty(last)}\n"
            f"uploads: {'✅ enabled' if enabled else '⏸ paused'}\n\n"
            "<b>Agents</b>\n"
            f"{agent_lines}",
            parse_mode="HTML",
        )

    elif cmd == "/queue":
        pend = db.pending_renders()[:5]
        rev = db.under_review()[:3]
        parts = ["🗂 <b>Queue</b>"]
        if pend:
            parts.append("\n<b>Pending review</b>")
            for r in pend:
                parts.append(f"  <code>{r.post_id}</code> — {_html_escape(r.title[:60])}")
        else:
            parts.append("\n<i>no pending renders</i>")
        if rev:
            parts.append("\n<b>Awaiting confirm-live</b>")
            for r in rev:
                parts.append(f"  <code>{r.post_id}</code> — {_html_escape(r.title[:60])}")
        notifier.send_text("\n".join(parts), parse_mode="HTML")

    elif cmd == "/menu":
        notifier.send_text_with_markup(tg_flows.menu_text(), tg_flows.menu_keyboard())

    elif cmd == "/render":
        notifier.send_text_with_markup(
            tg_flows.render_text(1, False), tg_flows.render_keyboard(1, False),
        )

    elif cmd == "/upload":
        # Send an empty shell, then repaint into a picker. That's one
        # extra edit call but keeps the "picker first" UX consistent
        # with the /menu → Upload flow.
        shell_id = notifier.send_text_with_markup(
            "🚀 <b>Upload</b>\n\nloading approved rows…",
            {"inline_keyboard": []},
        )
        _render_upload_picker(notifier, shell_id)

    elif cmd == "/confirm":
        notifier.send_text_with_markup(
            tg_flows.confirm_text(False), tg_flows.confirm_keyboard(False),
        )

    elif cmd == "/webapp":
        _handle_webapp_cmd(notifier, text)

    else:
        notifier.send_text(
            "commands: /menu /render /upload /confirm /status /queue /pause /resume /webapp"
        )


_BOT_COMMANDS: list[tuple[str, str]] = [
    ("menu", "Main menu"),
    ("render", "Render slot controls"),
    ("upload", "Upload approved picker"),
    ("confirm", "Confirm-live controls"),
    ("status", "Pipeline status"),
    ("queue", "Pending + review queue"),
    ("webapp", "Start/stop webapp"),
    ("pause", "Pause uploads"),
    ("resume", "Resume uploads"),
]


def _register_bot_commands(notifier: Notifier) -> None:
    """Publish the slash-command list to Telegram so clients autocomplete `/`.

    Idempotent: `setMyCommands` overwrites the default scope. Failures are
    logged but non-fatal — the bot still works, users just lose autocomplete.
    """
    payload = {"commands": [{"command": c, "description": d} for c, d in _BOT_COMMANDS]}
    try:
        notifier._post("setMyCommands", payload)
        log.info("telegram bot: registered %d commands", len(_BOT_COMMANDS))
    except Exception as e:
        log.warning("telegram bot: setMyCommands failed: %s", e)


def run_callback_bot(db, *, notifier: Notifier | None = None,
                     stop_after_s: float | None = None) -> None:
    """Long-poll `getUpdates` and dispatch to _handle_callback / _handle_message.
    Persists the getUpdates offset in the DB config table so restarts skip
    already-processed updates.

    stop_after_s: return after ~this many seconds (test hook). None = run
    forever."""
    notifier = notifier or Notifier.from_env()
    start = time.monotonic()

    _register_bot_commands(notifier)

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
