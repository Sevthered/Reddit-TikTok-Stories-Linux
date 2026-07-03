"""Read-only render endpoints backing the queue views."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from core.db import Db
from webapp.backend import settings
from webapp.backend.deps import get_db
from webapp.backend.rate_limit import limiter
from webapp.backend.schemas import RenderOut

router = APIRouter(prefix="/renders", tags=["renders"])


@router.get("/pending", response_model=list[RenderOut])
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def pending(request: Request, db: Db = Depends(get_db)) -> list[RenderOut]:
    """Rows awaiting a human decision (Approve / Reject)."""
    return [RenderOut.from_row(r) for r in db.pending_renders()]


@router.get("/approved", response_model=list[RenderOut])
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def approved(request: Request, db: Db = Depends(get_db)) -> list[RenderOut]:
    """Rows awaiting the upload worker."""
    return [RenderOut.from_row(r) for r in db.approved_ready()]


@router.get("/under-review", response_model=list[RenderOut])
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def under_review(request: Request, db: Db = Depends(get_db)) -> list[RenderOut]:
    """Rows already posted to TikTok but awaiting the confirm-live scrape
    to promote them to `posted`."""
    return [RenderOut.from_row(r) for r in db.under_review()]


@router.get("/{post_id}", response_model=RenderOut)
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def get_render(request: Request, post_id: str, db: Db = Depends(get_db)) -> RenderOut:
    row = db.get_render(post_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"post_id {post_id!r} not found")
    return RenderOut.from_row(row)
