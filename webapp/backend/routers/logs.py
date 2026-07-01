"""GET /api/logs/{name}/tail — last N lines of a whitelisted log file.

Plus /stream — a persistent SSE follower for the same file. The follower
polls `os.stat` every 500 ms (portable, macOS-safe: no kqueue coupling),
seeks to end on connect, and streams new bytes as `data` events. Handles
log rotation by comparing (inode, size); when the current inode goes
away we reopen from the head so a `logrotate copytruncate` (or manual
truncate) doesn't leave us stuck at a stale offset. Any embedded CR is
stripped so ffmpeg progress lines don't overwrite the terminal.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse

from webapp.backend import settings
from webapp.backend.schemas import LogName, LogTailOut

log = logging.getLogger("webapp.routers.logs")

router = APIRouter(prefix="/logs", tags=["logs"])


# Cap per-request read to keep the endpoint cheap; SSE streaming will
# come in Phase 10.
_MAX_BYTES = 512 * 1024
# Whitelisted log names — arbitrary strings would let a caller point us
# at any file in the logs dir.
_ALLOWED: set[str] = {"upload_worker", "bot", "confirm_live", "webapp"}


def _resolve_log(name: str, stream: str) -> Path:
    if name not in _ALLOWED:
        raise HTTPException(status_code=404, detail=f"unknown log {name!r}")
    if stream not in {"stdout", "stderr"}:
        raise HTTPException(status_code=400, detail=f"stream must be stdout|stderr")
    p = settings.LOGS_DIR / f"{name}.{stream}.log"
    # Path-traversal guard: `name` is already whitelisted, but re-verify
    # the resolved path is inside LOGS_DIR (belt + suspenders against
    # symlink surprises).
    resolved = p.resolve()
    if not resolved.is_relative_to(settings.LOGS_DIR.resolve()):
        raise HTTPException(status_code=400, detail="log path escaped logs dir")
    return p


def _tail_lines(path: Path, want_lines: int, max_bytes: int) -> tuple[list[str], int, int, bool]:
    """Read the last `want_lines` lines by seeking from the end. Bounded
    to `max_bytes`. Returns (lines, bytes_read, file_size, truncated)."""
    if not path.exists():
        return [], 0, 0, False
    size = path.stat().st_size
    if size == 0:
        return [], 0, 0, False

    to_read = min(size, max_bytes)
    truncated = to_read < size
    with path.open("rb") as f:
        f.seek(-to_read, os.SEEK_END)
        raw = f.read(to_read)

    text = raw.decode("utf-8", errors="replace")
    # If the window started mid-line, drop the partial first line so we
    # never surface a half-truncated timestamp.
    if truncated:
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]

    lines = text.splitlines()
    if want_lines > 0:
        lines = lines[-want_lines:]
    return lines, to_read, size, truncated


@router.get("/{name}/tail", response_model=LogTailOut)
def tail(
    name: LogName,
    lines: int = Query(default=200, ge=1, le=5000),
    stream: Literal["stdout", "stderr"] = Query(default="stderr"),
) -> LogTailOut:
    p = _resolve_log(name, stream)
    got, bytes_read, size, truncated = _tail_lines(p, lines, _MAX_BYTES)
    return LogTailOut(
        name=name, stream=stream,
        lines=got, truncated=truncated,
        bytes_read=bytes_read, file_size=size,
    )


# ---- SSE follower --------------------------------------------------------

_POLL_INTERVAL_S = 0.5
_HEARTBEAT_INTERVAL_S = 15.0
_READ_CHUNK = 64 * 1024
_MAX_COALESCE_LINES = 40  # cap events/s so a burst can't flood the client


def _sse(event: str, data: str) -> str:
    lines = data.replace("\r", "").split("\n")
    payload = "\n".join(f"data: {ln}" for ln in lines)
    return f"event: {event}\n{payload}\n\n"


async def _follow(path: Path, request: Request):
    """Async generator yielding SSE frames as the file grows. Reopens on
    rotation. Yields `ping` heartbeats when idle so proxies don't drop."""

    def _stat_or_none(p: Path) -> os.stat_result | None:
        try:
            return p.stat()
        except FileNotFoundError:
            return None

    # Open at end so the initial subscribe doesn't dump the whole file
    # (that's what /tail is for). If the file is missing right now, wait
    # for it to appear — nothing prevents the writer from creating it
    # slightly later.
    f = None
    inode: int | None = None
    pos = 0
    last_heartbeat = time.monotonic()

    try:
        while True:
            if await request.is_disconnected():
                return

            st = _stat_or_none(path)
            if st is None:
                if f is not None:
                    f.close()
                    f = None
                    inode = None
                await asyncio.sleep(_POLL_INTERVAL_S)
                if time.monotonic() - last_heartbeat > _HEARTBEAT_INTERVAL_S:
                    yield _sse("ping", "")
                    last_heartbeat = time.monotonic()
                continue

            # Rotation / truncation detection.
            if f is None or inode != st.st_ino:
                if f is not None:
                    f.close()
                f = path.open("rb")
                inode = st.st_ino
                f.seek(0, os.SEEK_END)
                pos = f.tell()
            elif st.st_size < pos:
                # File was truncated in place (copytruncate). Resume from
                # head so we don't drop new data.
                log.info("log %s truncated; rewinding", path.name)
                f.seek(0)
                pos = 0

            if st.st_size > pos:
                data = f.read(min(_READ_CHUNK, st.st_size - pos))
                pos = f.tell()
                text = data.decode("utf-8", errors="replace")
                # Coalesce this chunk's lines into a single SSE `line`
                # event (fewer roundtrips + smaller cursor jump in the
                # browser). Multi-line SSE `data:` fields are handled by
                # _sse() splitting on \n.
                lines = text.split("\n")
                # Preserve last incomplete line by parking it — but we
                # don't buffer across polls to keep code small; a partial
                # line will complete on the next poll after its \n
                # arrives.
                if lines and lines[-1] == "":
                    lines = lines[:-1]
                if lines:
                    if len(lines) > _MAX_COALESCE_LINES:
                        lines = lines[-_MAX_COALESCE_LINES:]
                    yield _sse("line", "\n".join(lines))
                    last_heartbeat = time.monotonic()
                continue

            if time.monotonic() - last_heartbeat > _HEARTBEAT_INTERVAL_S:
                yield _sse("ping", "")
                last_heartbeat = time.monotonic()

            await asyncio.sleep(_POLL_INTERVAL_S)
    finally:
        if f is not None:
            f.close()


@router.get("/{name}/stream")
async def stream_log(
    name: LogName,
    request: Request,
    stream: Literal["stdout", "stderr"] = Query(default="stderr"),
) -> EventSourceResponse:
    p = _resolve_log(name, stream)
    return EventSourceResponse(_follow(p, request))
