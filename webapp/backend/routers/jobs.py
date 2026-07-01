"""Trigger + monitor endpoints for pipeline subprocess jobs.

Route shape:
    POST /api/jobs/render   {limit?, dry_run?}
    POST /api/jobs/upload   {force?, dry_run?, visibility?}
    POST /api/jobs/confirm  {force?}
    GET  /api/jobs
    GET  /api/jobs/{id}
    GET  /api/jobs/{id}/stream  → SSE, one `line` event per stdout line,
                                   `end` event with exit_code on completion
    POST /api/jobs/{id}/cancel  → SIGTERM (SIGKILL after 5 s)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel, Field

from webapp.backend.jobs import Job, JobBusyError, JobManager

log = logging.getLogger("webapp.routers.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobOut(BaseModel):
    id: str
    kind: str
    args: list[str]
    started_at: str
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    running: bool
    line_count: int

    @classmethod
    def from_job(cls, j: Job) -> "JobOut":
        return cls(
            id=j.id,
            kind=j.kind,
            args=j.args,
            started_at=j.started_at,
            ended_at=j.ended_at,
            exit_code=j.exit_code,
            running=j.running,
            line_count=len(j.lines),
        )


class RenderIn(BaseModel):
    limit: int = Field(1, ge=1, le=10)
    dry_run: bool = False
    # When set, main.py sends per-stage progress edits to this Telegram
    # message (bot → API round-trip; the message is pre-created by the
    # bot so it can echo `✅ background`, `✅ TTS`, …).
    progress_chat_id: int | None = None
    progress_message_id: int | None = None


class UploadIn(BaseModel):
    force: bool = False
    dry_run: bool = False
    visibility: Literal["public", "only_me", "friends"] = "public"
    aigc: bool = True
    post_id: str | None = None  # target a specific approved row


class ConfirmIn(BaseModel):
    force: bool = False


def _mgr(request: Request) -> JobManager:
    return request.app.state.jobs


@router.get("", response_model=list[JobOut])
def list_jobs(request: Request) -> list[JobOut]:
    return [JobOut.from_job(j) for j in _mgr(request).list()]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request) -> JobOut:
    j = _mgr(request).get(job_id)
    if j is None:
        raise HTTPException(404, detail=f"job {job_id!r} not found")
    return JobOut.from_job(j)


async def _start(request: Request, kind, args: list[str]) -> JobOut:
    try:
        job = await _mgr(request).start(kind, args)
    except JobBusyError as e:
        raise HTTPException(409, detail=str(e)) from e
    return JobOut.from_job(job)


@router.post("/render", response_model=JobOut)
async def start_render(payload: RenderIn, request: Request) -> JobOut:
    args = ["--limit", str(payload.limit)]
    if payload.dry_run:
        args.append("--dry-run")
    if payload.progress_chat_id is not None and payload.progress_message_id is not None:
        args += [
            "--progress-chat-id", str(payload.progress_chat_id),
            "--progress-message-id", str(payload.progress_message_id),
        ]
    return await _start(request, "render", args)


@router.post("/upload", response_model=JobOut)
async def start_upload(payload: UploadIn, request: Request) -> JobOut:
    args = ["--visibility", payload.visibility]
    if payload.force:
        args.append("--force")
    if payload.dry_run:
        args.append("--dry-run")
    if not payload.aigc:
        args.append("--no-aigc")
    if payload.post_id:
        args += ["--post-id", payload.post_id]
    return await _start(request, "upload", args)


@router.post("/confirm", response_model=JobOut)
async def start_confirm(payload: ConfirmIn, request: Request) -> JobOut:
    args: list[str] = []
    if payload.force:
        args.append("--force")
    return await _start(request, "confirm", args)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str, request: Request) -> JobOut:
    mgr = _mgr(request)
    j = mgr.get(job_id)
    if j is None:
        raise HTTPException(404, detail=f"job {job_id!r} not found")
    if not j.running:
        raise HTTPException(409, detail="job already ended")
    await mgr.cancel(job_id)
    return JobOut.from_job(j)


def _sse(event: str, data: str) -> str:
    """Format one SSE frame. Multi-line `data` is split into repeated
    `data:` fields (per spec), and any embedded CR is stripped."""
    lines = data.replace("\r", "").split("\n")
    payload = "\n".join(f"data: {ln}" for ln in lines)
    return f"event: {event}\n{payload}\n\n"


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> EventSourceResponse:
    mgr = _mgr(request)
    job = mgr.get(job_id)
    if job is None:
        raise HTTPException(404, detail=f"job {job_id!r} not found")

    async def gen():
        q = mgr.subscribe(job)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield _sse("ping", "")
                    continue
                if line is None:
                    exit_str = "" if job.exit_code is None else str(job.exit_code)
                    yield _sse("end", exit_str)
                    break
                yield _sse("line", line)
        finally:
            mgr.unsubscribe(job, q)

    return EventSourceResponse(gen())
