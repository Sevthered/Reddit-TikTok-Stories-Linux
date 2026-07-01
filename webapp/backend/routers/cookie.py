"""GET /api/cookie/health — TikTok sessionid cookie freshness probe."""
from __future__ import annotations

from fastapi import APIRouter

from pipeline.upload import sessionid_expires_in_days
from webapp.backend import settings
from webapp.backend.schemas import CookieHealthOut

router = APIRouter(prefix="/cookie", tags=["cookie"])


@router.get("/health", response_model=CookieHealthOut)
def health() -> CookieHealthOut:
    days = sessionid_expires_in_days(settings.COOKIES_PATH)
    return CookieHealthOut(
        cookies_path=str(settings.COOKIES_PATH),
        exists=settings.COOKIES_PATH.exists(),
        sessionid_days_remaining=days,
    )
