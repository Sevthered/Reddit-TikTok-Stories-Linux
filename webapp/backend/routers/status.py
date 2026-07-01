"""GET /api/status — dashboard's top-of-page summary."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from core.agents import list_agent_status
from core.db import Db
from pipeline.upload import sessionid_expires_in_days
from webapp.backend import settings
from webapp.backend.deps import get_db
from webapp.backend.schemas import AgentStatus, StatusOut

log = logging.getLogger("webapp.status")


router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusOut)
async def status(db: Db = Depends(get_db)) -> StatusOut:
    # launchctl subprocess in a threadpool so we don't block the loop.
    snapshot = await asyncio.to_thread(list_agent_status)
    agents = [
        AgentStatus(
            label=a.label,
            loaded=a.loaded,
            pid=a.pid,
            last_exit_code=a.last_exit_code,
        )
        for a in snapshot
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
