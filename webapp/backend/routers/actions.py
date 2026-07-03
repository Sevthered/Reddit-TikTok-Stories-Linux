"""Approve/reject actions on rendered posts.

Mirrors the Telegram callback path in `core/notify.py::_handle_callback`:
- Approve: `Db.approve` (pending → approved), then edit the review-request
  caption in the Telegram thread so the buttons go inert.
- Reject: `Db.reject` ({pending, approved, failed} → rejected), delete the
  video + cover artifacts, edit the caption.

Telegram edits are best-effort: if the Notifier can't be constructed (no
env), or the API call fails, we log and still return the transitioned row
so the UI doesn't stall.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from core.db import Db, RenderRow
from core.notify import Notifier, NotifierError
from webapp.backend import settings
from webapp.backend.deps import get_db
from webapp.backend.rate_limit import limiter
from webapp.backend.schemas import RenderOut

log = logging.getLogger("webapp.actions")

router = APIRouter(prefix="/renders", tags=["actions"])


def _try_edit_caption(msg_id: int | None, suffix: str) -> None:
    """Fire-and-forget Telegram caption edit. Runs in the caller thread —
    wrap the whole action in asyncio.to_thread so the event loop isn't
    blocked by the HTTP round-trip."""
    if not msg_id:
        return
    try:
        notifier = Notifier.from_env()
    except NotifierError as e:
        log.info("skipping Telegram edit (no notifier env): %s", e)
        return
    try:
        notifier.edit_review_caption(msg_id, suffix)
    except Exception as e:  # noqa: BLE001  best-effort
        log.warning("Telegram edit_review_caption failed for %d: %s", msg_id, e)


def _delete_artifacts(row: RenderRow | None) -> None:
    if row is None:
        return
    for p in (row.video_path, row.cover_path):
        if not p:
            continue
        try:
            path = Path(p)
            if path.exists():
                path.unlink()
        except OSError as e:
            log.warning("failed to delete %s: %s", p, e)


def _do_approve(db: Db, post_id: str) -> RenderRow:
    row = db.get_render(post_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"post_id {post_id!r} not found")
    ok = db.approve(post_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"cannot approve {post_id!r} — status is {row.upload_status!r}",
        )
    _try_edit_caption(row.telegram_msg_id, f"✅ <b>Approved via web</b> — {post_id}")
    updated = db.get_render(post_id)
    assert updated is not None
    return updated


def _do_reject(db: Db, post_id: str) -> RenderRow:
    row = db.get_render(post_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"post_id {post_id!r} not found")
    ok = db.reject(post_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"cannot reject {post_id!r} — status is {row.upload_status!r}",
        )
    _delete_artifacts(row)
    _try_edit_caption(row.telegram_msg_id, f"❌ <b>Rejected via web</b> — {post_id}")
    updated = db.get_render(post_id)
    assert updated is not None
    return updated


@router.post("/{post_id}/approve", response_model=RenderOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def approve(request: Request, post_id: str, db: Db = Depends(get_db)) -> RenderOut:
    row = await asyncio.to_thread(_do_approve, db, post_id)
    log.info("approved %s via web", post_id)
    return RenderOut.from_row(row)


@router.post("/{post_id}/reject", response_model=RenderOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def reject(request: Request, post_id: str, db: Db = Depends(get_db)) -> RenderOut:
    row = await asyncio.to_thread(_do_reject, db, post_id)
    log.info("rejected %s via web (artifacts unlinked)", post_id)
    return RenderOut.from_row(row)
