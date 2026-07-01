"""GET /api/logs/{name}/tail — last N lines from journald.
GET /api/logs/{name}/stream — SSE follower via `journalctl -f`.

On Linux/systemd the pipeline writes to the journal, not to per-file
logs. This router maps the legacy log-name whitelist to systemd unit
names and delegates to `journalctl`.

Name -> unit mapping:
    webapp        -> tiktok-webapp.service
    bot           -> tiktok-bot.service
    upload_worker -> tiktok-upload.service
    confirm_live  -> tiktok-confirm.service

The `stream` query param (stdout|stderr) is accepted for API compat but
ignored — journald interleaves both streams under STDERR/STDOUT priorities
which the frontend does not distinguish.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse

from webapp.backend.schemas import LogName, LogTailOut

log = logging.getLogger("webapp.routers.logs")

router = APIRouter(prefix="/logs", tags=["logs"])

_LOG_UNITS: dict[str, str] = {
    "webapp":        "tiktok-webapp.service",
    "bot":           "tiktok-bot.service",
    "upload_worker": "tiktok-upload.service",
    "confirm_live":  "tiktok-confirm.service",
}


def _unit_for(name: str) -> str:
    if name not in _LOG_UNITS:
        raise HTTPException(status_code=404, detail=f"unknown log {name!r}")
    return _LOG_UNITS[name]


def _journal_tail(unit: str, lines: int) -> list[str]:
    """Return the last `lines` journal lines for `unit` (no colours, ISO time)."""
    try:
        out = subprocess.run(
            [
                "journalctl",
                "-u", unit,
                "-n", str(lines),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.warning("journalctl tail %s failed: %s", unit, exc)
        return []
    if out.returncode != 0:
        log.warning("journalctl tail %s exited %s: %s", unit, out.returncode, out.stderr.strip())
        return []
    return out.stdout.splitlines()


@router.get("/{name}/tail", response_model=LogTailOut)
def tail(
    name: LogName,
    lines: int = Query(default=200, ge=1, le=5000),
    stream: Literal["stdout", "stderr"] = Query(default="stderr"),
) -> LogTailOut:
    unit = _unit_for(name)
    got = _journal_tail(unit, lines)
    total_bytes = sum(len(l) + 1 for l in got)
    return LogTailOut(
        name=name,
        stream=stream,
        lines=got,
        truncated=False,
        bytes_read=total_bytes,
        file_size=total_bytes,
    )


# ---- SSE follower --------------------------------------------------------

_HEARTBEAT_INTERVAL_S = 15.0
_MAX_COALESCE_LINES = 40


def _sse(event: str, data: str) -> str:
    payload_lines = data.replace("\r", "").split("\n")
    payload = "\n".join(f"data: {ln}" for ln in payload_lines)
    return f"event: {event}\n{payload}\n\n"


async def _follow(unit: str, request: Request):
    """Spawn `journalctl -f -u <unit>` and yield SSE frames for each line."""
    proc = await asyncio.create_subprocess_exec(
        "journalctl",
        "-f",
        "-n", "0",
        "-u", unit,
        "--no-pager",
        "--output=short-iso",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None

    last_heartbeat = time.monotonic()
    pending: list[str] = []
    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = b""
            if raw:
                line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
                if line:
                    pending.append(line)
                if len(pending) >= _MAX_COALESCE_LINES:
                    yield _sse("line", "\n".join(pending))
                    pending.clear()
                    last_heartbeat = time.monotonic()
                continue

            if pending:
                yield _sse("line", "\n".join(pending))
                pending.clear()
                last_heartbeat = time.monotonic()
                continue

            if time.monotonic() - last_heartbeat > _HEARTBEAT_INTERVAL_S:
                yield _sse("ping", "")
                last_heartbeat = time.monotonic()
    finally:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass


@router.get("/{name}/stream")
async def stream_log(
    name: LogName,
    request: Request,
    stream: Literal["stdout", "stderr"] = Query(default="stderr"),
) -> EventSourceResponse:
    unit = _unit_for(name)
    return EventSourceResponse(_follow(unit, request))
