"""Live render checklist for the Telegram review chat.

`main.py` calls `progress.mark("bg")` after each artifact stage; this
edits ONE message in-place so the user sees a growing checklist without
6 separate notifications. Failure branches call `progress.done(ok=False)`
so the checklist settles into a terminal state.

Every network call is best-effort — the render must never fail because
Telegram is unreachable.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# (key, human label). Order = display order.
STAGES: tuple[tuple[str, str], ...] = (
    ("bg", "background"),
    ("tts", "TTS"),
    ("wsp", "transcribe"),
    ("cap", "captions"),
    ("cov", "cover card"),
    ("asm", "assemble"),
)


class RenderProgress:
    def __init__(self, notifier, chat_id: int, message_id: int,
                 post_id: str, title: str) -> None:
        self._notifier = notifier
        self._chat_id = chat_id
        self._message_id = message_id
        self._post_id = post_id
        self._title = title
        self._done: set[str] = set()
        self._active: str | None = None
        self._terminated = False
        # Fire the first paint so the reviewer sees the checklist even
        # before stage 1 finishes.
        self._push()

    def mark(self, key: str) -> None:
        """Move `key` from ⏳ to ✅ and advance the next unstarted stage
        to ⏳. Silently ignores unknown keys."""
        if self._terminated:
            return
        if key not in {k for k, _ in STAGES}:
            return
        self._done.add(key)
        self._active = None
        for k, _ in STAGES:
            if k not in self._done:
                self._active = k
                break
        self._push()

    def done(self, ok: bool, note: str = "") -> None:
        """Terminal transition — every remaining stage collapses to ❌
        (fail) or ✅ (success). No more edits after this."""
        if self._terminated:
            return
        self._terminated = True
        self._push(final=True, ok=ok, note=note)

    # ---- internals ----

    def _render(self, *, final: bool = False, ok: bool = True,
                note: str = "") -> str:
        header = f"🎬 <b>Rendering</b> <code>{self._post_id}</code>"
        title_line = self._title[:80] + ("…" if len(self._title) > 80 else "")
        lines = [header, title_line, ""]
        for k, label in STAGES:
            if k in self._done:
                lines.append(f"✅ {label}")
            elif not final and k == self._active:
                lines.append(f"⏳ {label}")
            elif final and not ok:
                lines.append(f"❌ {label}")
            else:
                lines.append(f"⬜ {label}")
        if final:
            lines.append("")
            lines.append("✅ <b>done</b>" if ok else f"❌ <b>failed</b>{': ' + note if note else ''}")
        return "\n".join(lines)

    def _push(self, *, final: bool = False, ok: bool = True,
              note: str = "") -> None:
        try:
            self._notifier.edit_message_text(
                self._message_id, self._render(final=final, ok=ok, note=note),
                parse_mode="HTML",
            )
        except Exception as e:  # noqa: BLE001  best-effort
            log.warning("progress edit failed: %s", e)
