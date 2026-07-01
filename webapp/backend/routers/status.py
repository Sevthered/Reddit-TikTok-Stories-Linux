"""GET /api/status — dashboard's top-of-page summary."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from core.db import Db
from pipeline.upload import sessionid_expires_in_days
from webapp.backend import settings
from webapp.backend.deps import get_db
from webapp.backend.schemas import AgentStatus, StatusOut

log = logging.getLogger("webapp.status")


router = APIRouter(tags=["status"])


_AGENT_LABELS = (
    "com.sebastian.tiktok-bot",
    "com.sebastian.tiktok-upload",
    "com.sebastian.tiktok-confirm",
    "com.sebastian.tiktok-webapp",
)


def _launchctl_snapshot() -> dict[str, tuple[int | None, int | None]]:
    """Parse `launchctl list` output into {label: (pid_or_None, exit_or_None)}.

    launchctl list columns: PID | Status | Label. PID is `-` when not
    running; Status is the last exit code. We tolerate parsing errors
    (return None for that field) — this endpoint must not fail hard on
    a launchd hiccup."""
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        log.warning("launchctl list failed: %s", e)
        return {}

    snap: dict[str, tuple[int | None, int | None]] = {}
    for line in out.splitlines()[1:]:  # skip header
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, status_s, label = parts
        try:
            pid = int(pid_s) if pid_s != "-" else None
        except ValueError:
            pid = None
        try:
            status = int(status_s)
        except ValueError:
            status = None
        snap[label] = (pid, status)
    return snap


@router.get("/status", response_model=StatusOut)
async def status(db: Db = Depends(get_db)) -> StatusOut:
    # launchctl subprocess in a threadpool so we don't block the loop.
    snap = await asyncio.to_thread(_launchctl_snapshot)
    agents = [
        AgentStatus(
            label=label,
            loaded=label in snap,
            pid=snap.get(label, (None, None))[0],
            last_exit_code=snap.get(label, (None, None))[1],
        )
        for label in _AGENT_LABELS
    ]

    days = None
    try:
        days = sessionid_expires_in_days(settings.COOKIES_PATH)
    except Exception as e:  # noqa: BLE001
        log.warning("sessionid probe failed: %s", e)

    return StatusOut(
        posts_today=db.posts_today(settings.madrid_tz_offset_hours()),
        uploads_enabled=db.is_uploads_enabled(),
        last_uploaded_at=db.last_uploaded_at(),
        sessionid_days_remaining=days,
        post_tz=str(settings.POST_TZ),
        madrid_offset_hours=settings.madrid_tz_offset_hours(),
        now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        agents=agents,
        pending_count=len(db.pending_renders()),
        under_review_count=len(db.under_review()),
    )
