"""Inline-keyboard state machine for /render, /upload, /confirm.

State lives inside callback_data — the bot is stateless so restarts
don't lose partial config screens. Encoding stays under Telegram's
64-byte callback_data cap by using single-char actions and compact
value slots. Grammar:

    render:  `r|<limit>|<dry>|<action>`         e.g. `r|2|0|s`
    upload:  `u|<vis>|<aigc>|<force>|<dry>|<act>` e.g. `u|only_me|1|0|1|s`
    confirm: `c|<force>|<action>`               e.g. `c|1|s`
    nav:     `nav|<kind>`                       e.g. `nav|render`

Values: dry/force/aigc are `0`/`1`. Visibility ∈ {`public`, `friends`,
`only_me`}. Actions: `s` start, `l+`/`l-` limit ±1, `d` toggle dry,
`f` toggle force, `a` toggle AIGC, `v` rotate visibility.
"""
from __future__ import annotations

from typing import Any

# ---- render ---------------------------------------------------------------

LIMIT_MIN = 1
LIMIT_MAX = 10


def _row(*btns: dict[str, str]) -> list[dict[str, str]]:
    return list(btns)


def _btn(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def render_text(limit: int, dry: bool) -> str:
    return (
        "🎬 <b>Render</b>\n"
        f"limit: <code>{limit}</code>\n"
        f"dry-run: <code>{'on' if dry else 'off'}</code>\n\n"
        "Edit and press <b>Start</b>."
    )


def render_keyboard(limit: int, dry: bool) -> dict[str, Any]:
    dry_i = 1 if dry else 0
    rows = [
        _row(
            _btn("➖ limit", f"r|{max(LIMIT_MIN, limit - 1)}|{dry_i}|l-"),
            _btn(f"{limit}", "noop"),
            _btn("➕ limit", f"r|{min(LIMIT_MAX, limit + 1)}|{dry_i}|l+"),
        ),
        _row(_btn(f"dry-run: {'ON ✅' if dry else 'OFF'}", f"r|{limit}|{1 - dry_i}|d")),
        _row(_btn("▶️ Start render", f"r|{limit}|{dry_i}|s")),
    ]
    return {"inline_keyboard": rows}


# ---- upload ---------------------------------------------------------------

VIS_ORDER = ("public", "friends", "only_me")


def _next_vis(v: str) -> str:
    try:
        return VIS_ORDER[(VIS_ORDER.index(v) + 1) % len(VIS_ORDER)]
    except ValueError:
        return VIS_ORDER[0]


# Sentinel post-id token meaning "let the worker claim the oldest". Keeps
# callback_data shape uniform whether or not the user picked a row.
NEXT_TOKEN = "_"


def upload_text(vis: str, aigc: bool, force: bool, dry: bool,
                post_id: str | None = None, title: str | None = None) -> str:
    target = f"<code>{post_id}</code>" if post_id else "<i>oldest approved</i>"
    title_line = f"\n{title[:70]}…" if title and len(title) > 70 else (f"\n{title}" if title else "")
    return (
        "🚀 <b>Upload</b>\n"
        f"target: {target}{title_line}\n"
        f"visibility: <code>{vis}</code>\n"
        f"AIGC (AI-content): <code>{'on' if aigc else 'off'}</code>\n"
        f"force gates: <code>{'on' if force else 'off'}</code>\n"
        f"dry-run: <code>{'on' if dry else 'off'}</code>\n\n"
        "Edit and press <b>Start</b>."
    )


def upload_keyboard(vis: str, aigc: bool, force: bool, dry: bool,
                    post_id: str | None = None) -> dict[str, Any]:
    a_i = 1 if aigc else 0
    f_i = 1 if force else 0
    d_i = 1 if dry else 0
    nxt_v = _next_vis(vis)
    pid = post_id or NEXT_TOKEN
    base = f"u|{pid}|{vis}|{a_i}|{f_i}|{d_i}"
    rows = [
        _row(_btn(f"visibility: {vis} → tap for {nxt_v}",
                  f"u|{pid}|{nxt_v}|{a_i}|{f_i}|{d_i}|v")),
        _row(
            _btn(f"AIGC: {'ON ✅' if aigc else 'OFF'}",
                 f"u|{pid}|{vis}|{1 - a_i}|{f_i}|{d_i}|a"),
            _btn(f"force: {'ON ✅' if force else 'OFF'}",
                 f"u|{pid}|{vis}|{a_i}|{1 - f_i}|{d_i}|f"),
        ),
        _row(_btn(f"dry-run: {'ON ✅' if dry else 'OFF'}",
                  f"u|{pid}|{vis}|{a_i}|{f_i}|{1 - d_i}|d")),
        _row(_btn("↩ change target", "upl_pick")),
        _row(_btn("▶️ Start upload", f"{base}|s")),
    ]
    return {"inline_keyboard": rows}


def upload_picker_text(approved: list[dict[str, Any]]) -> str:
    if not approved:
        return (
            "🚀 <b>Upload</b>\n\n"
            "<i>No approved renders waiting.</i>\n"
            "Approve one via /queue first."
        )
    return (
        "🚀 <b>Upload — pick a render</b>\n"
        f"{len(approved)} row(s) approved. Tap to configure and start."
    )


def upload_picker_keyboard(approved: list[dict[str, Any]]) -> dict[str, Any]:
    """One button per approved row plus a `↩ oldest` fallback that keeps
    the classic claim-next behaviour."""
    rows: list[list[dict[str, str]]] = []
    for r in approved[:8]:  # cap so the keyboard fits
        pid = r["post_id"]
        title = r.get("title") or ""
        label = f"{pid} — {title[:36]}"
        rows.append(_row(_btn(label, f"upl_pick|{pid}")))
    rows.append(_row(_btn("⏭ oldest approved (default)", f"upl_pick|{NEXT_TOKEN}")))
    return {"inline_keyboard": rows}


# ---- confirm --------------------------------------------------------------

def confirm_text(force: bool) -> str:
    return (
        "🔍 <b>Confirm live</b>\n"
        f"force: <code>{'on' if force else 'off'}</code>\n\n"
        "Scrapes @RealRedditStories, promotes matched rows."
    )


def confirm_keyboard(force: bool) -> dict[str, Any]:
    f_i = 1 if force else 0
    rows = [
        _row(_btn(f"force: {'ON ✅' if force else 'OFF'}", f"c|{1 - f_i}|f")),
        _row(_btn("▶️ Start confirm", f"c|{f_i}|s")),
    ]
    return {"inline_keyboard": rows}


# ---- menu -----------------------------------------------------------------

def menu_text() -> str:
    return (
        "🎛 <b>Control plane</b>\n"
        "Pick a workflow. Every screen has knobs before you commit."
    )


def menu_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            _row(
                _btn("🎬 Render", "nav|render"),
                _btn("🚀 Upload", "nav|upload"),
                _btn("🔍 Confirm", "nav|confirm"),
            )
        ]
    }


# ---- parsers --------------------------------------------------------------

def parse_render(data: str) -> tuple[int, bool, str] | None:
    """`r|<limit>|<dry>|<action>` → (limit, dry_bool, action). None on
    malformed input."""
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != "r":
        return None
    try:
        limit = max(LIMIT_MIN, min(LIMIT_MAX, int(parts[1])))
    except ValueError:
        return None
    dry = parts[2] == "1"
    return limit, dry, parts[3]


def parse_upload(data: str) -> tuple[str | None, str, bool, bool, bool, str] | None:
    """Returns (post_id_or_None, vis, aigc, force, dry, action). None on
    malformed input. Post-id `_` sentinel decodes as None (oldest)."""
    parts = data.split("|")
    if len(parts) != 7 or parts[0] != "u":
        return None
    pid = parts[1]
    post_id: str | None = None if pid == NEXT_TOKEN else pid
    vis = parts[2]
    if vis not in VIS_ORDER:
        vis = "public"
    return post_id, vis, parts[3] == "1", parts[4] == "1", parts[5] == "1", parts[6]


def parse_confirm(data: str) -> tuple[bool, str] | None:
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != "c":
        return None
    return parts[1] == "1", parts[2]
