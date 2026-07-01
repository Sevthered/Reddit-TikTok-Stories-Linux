"""Pydantic schemas exposed by the read endpoints.

Mirror the internal `RenderRow` / `Db` return shapes without leaking
sqlite-specific types. Ordering + field names are the contract the
SvelteKit frontend types against.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from core.db import RenderRow


class RenderOut(BaseModel):
    post_id: str
    title: str
    subreddit: str
    author: str
    caption: str
    video_path: str
    cover_path: str
    upload_status: str
    upload_attempts: int
    next_retry_at: Optional[str] = None
    telegram_msg_id: Optional[int] = None
    uploaded_at: Optional[str] = None
    tiktok_url: Optional[str] = None

    @classmethod
    def from_row(cls, r: RenderRow) -> "RenderOut":
        return cls(
            post_id=r.post_id, title=r.title, subreddit=r.subreddit,
            author=r.author, caption=r.caption,
            video_path=r.video_path, cover_path=r.cover_path,
            upload_status=r.upload_status,
            upload_attempts=r.upload_attempts,
            next_retry_at=r.next_retry_at,
            telegram_msg_id=r.telegram_msg_id,
            uploaded_at=r.uploaded_at,
            tiktok_url=r.tiktok_url,
        )


class AgentStatus(BaseModel):
    label: str
    loaded: bool
    pid: Optional[int] = None
    last_exit_code: Optional[int] = None


class StatusOut(BaseModel):
    posts_today: int
    posts_per_day_cap: int = 2
    uploads_enabled: bool
    last_uploaded_at: Optional[str] = None
    sessionid_days_remaining: Optional[float] = None
    post_tz: str
    madrid_offset_hours: int
    now_iso: str
    agents: list[AgentStatus]
    pending_count: int
    under_review_count: int


class CookieHealthOut(BaseModel):
    cookies_path: str
    exists: bool
    sessionid_days_remaining: Optional[float] = None
    warn_at_days: float = 3.0


LogName = Literal["upload_worker", "bot", "confirm_live", "webapp"]


class LogTailOut(BaseModel):
    name: LogName
    stream: Literal["stdout", "stderr"] = "stderr"
    lines: list[str]
    truncated: bool = Field(default=False,
                            description="true if the file exceeded the read budget")
    bytes_read: int
    file_size: int
