"""Serve rendered artifacts (final MP4 + cover PNG) for in-browser preview.

Starlette's `FileResponse` already handles HTTP Range 206 for `<video>`
scrubbing plus `ETag` / `Last-Modified` conditional GETs — no manual
Range parser needed (research report §D, 2026-07-01).

Path-traversal guard: the file MUST live under `settings.OUTPUT_DIR`
even though the input is a post_id (never a raw path). This is defence
in depth in case a caller ever accepts an artifact_path override.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import FileResponse

from core.db import Db, RenderRow
from webapp.backend import settings
from webapp.backend.deps import get_db

log = logging.getLogger("webapp.routers.artifacts")

router = APIRouter(tags=["artifacts"])


def _safe_path(raw: str | None, expected_suffix: str) -> Path:
    if not raw:
        raise HTTPException(404, detail="artifact path not recorded")
    p = Path(raw).resolve()
    if not p.exists():
        raise HTTPException(404, detail=f"file missing on disk: {p.name}")
    root = settings.OUTPUT_DIR.resolve()
    if not p.is_relative_to(root):
        # Log and reject — the DB row references a file outside the sandbox.
        log.warning("path traversal blocked: %s not under %s", p, root)
        raise HTTPException(403, detail="artifact path outside sandbox")
    if p.suffix.lower() != expected_suffix:
        raise HTTPException(415, detail=f"unexpected suffix {p.suffix!r}")
    return p


def _row(post_id: str, db: Db) -> RenderRow:
    row = db.get_render(post_id)
    if row is None:
        raise HTTPException(404, detail=f"post_id {post_id!r} not found")
    return row


# No rate limit on these two (R2.3): a <video> player fires many HTTP
# Range requests per playback/scrub, and a per-minute cap sized for
# normal API polling would break video preview unpredictably. Treated
# like /health -- read-only, already behind Zero Trust + the path-
# traversal guard above.
@router.get("/video/{post_id}")
def get_video(post_id: str, db: Db = Depends(get_db)) -> FileResponse:
    row = _row(post_id, db)
    path = _safe_path(row.video_path, ".mp4")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{post_id}.mp4",
        # Inline so <video src> plays without triggering a download.
        content_disposition_type="inline",
    )


@router.get("/cover/{post_id}")
def get_cover(post_id: str, db: Db = Depends(get_db)) -> FileResponse:
    row = _row(post_id, db)
    path = _safe_path(row.cover_path, ".png")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"{post_id}.png",
        content_disposition_type="inline",
    )
