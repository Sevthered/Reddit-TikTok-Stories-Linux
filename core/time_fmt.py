"""Human-friendly timestamp helpers for Telegram bot output.

DB stores UTC ISO strings (e.g. `2026-07-01T16:45:11+00:00`). Phone
readers want Madrid-local time plus a relative age. Pure functions, no
network, no dependencies beyond stdlib + ZoneInfo (already used by
webapp/backend/settings.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_MADRID = ZoneInfo("Europe/Madrid")


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        # `datetime.fromisoformat` accepts the `+00:00` we write via
        # `datetime.now(timezone.utc).isoformat(timespec="seconds")`.
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def to_madrid_local(iso: str | None) -> str:
    """Render an ISO string as `YYYY-MM-DD HH:MM CET/CEST`. Empty string
    on unparsable input so callers can `or 'never'` freely."""
    dt = _parse(iso)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_MADRID)
    return local.strftime("%Y-%m-%d %H:%M %Z")


def relative(iso: str | None, *, now: datetime | None = None) -> str:
    """`"5 s ago"`, `"42 m ago"`, `"3 h ago"`, `"2 d ago"`. Values in the
    future render as `"in 5 m"`. `""` on unparsable."""
    dt = _parse(iso)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now - dt).total_seconds()
    future = delta < 0
    delta = abs(delta)
    if delta < 5:
        return "just now"
    if delta < 60:
        unit, val = "s", int(delta)
    elif delta < 3600:
        unit, val = "m", int(delta // 60)
    elif delta < 86400:
        unit, val = "h", int(delta // 3600)
    else:
        unit, val = "d", int(delta // 86400)
    return f"in {val} {unit}" if future else f"{val} {unit} ago"


def pretty(iso: str | None) -> str:
    """Combine `to_madrid_local` + `relative` into one line. Returns
    `"never"` when the input is missing or unparsable."""
    if not iso:
        return "never"
    local = to_madrid_local(iso)
    rel = relative(iso)
    if not local:
        return "never"
    return f"{local} ({rel})" if rel else local
