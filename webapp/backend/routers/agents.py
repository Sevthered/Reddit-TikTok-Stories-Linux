"""systemd control for the pipeline units.

Runs `systemctl start/stop/restart` for the whitelisted units. The
running user (`christian`) has a polkit rule allowing manage-units on
just these units, so no sudo is required. Labels are strictly
whitelisted so a caller can't drive systemctl at arbitrary units.

Actions preserve the legacy launchd-era names (load / unload /
kickstart) so the frontend contract is unchanged; internally they map
onto systemctl verbs:
    load       -> start
    unload     -> stop
    kickstart  -> restart
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("webapp.routers.agents")

router = APIRouter(prefix="/agents", tags=["agents"])

_ALLOWED_LABELS: set[str] = {
    "tiktok-upload",
    "tiktok-confirm",
    "tiktok-bot",
    "tiktok-xvfb",
    # tiktok-webapp is deliberately excluded — we're running inside it,
    # so `stop` would kill this process mid-response. Restart is exposed
    # separately via a signal-based mechanism if ever needed.
}

_SYSTEMCTL = "/usr/bin/systemctl"


async def _run(*argv: str) -> tuple[int, str]:
    """Run systemctl, capture merged stderr+stdout."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


def _guard(label: str) -> str:
    if label not in _ALLOWED_LABELS:
        raise HTTPException(404, detail=f"label {label!r} not managed by webapp")
    return f"{label}.service"


class AgentActionOut(BaseModel):
    label: str
    action: Literal["load", "unload", "kickstart"]
    exit_code: int
    output: str


@router.post("/{label}/load", response_model=AgentActionOut)
async def load_agent(label: str) -> AgentActionOut:
    unit = _guard(label)
    rc, out = await _run(_SYSTEMCTL, "start", unit)
    if rc != 0:
        raise HTTPException(500, detail=f"start failed: {out}")
    log.info("unit %s started", unit)
    return AgentActionOut(label=label, action="load", exit_code=rc, output=out)


@router.post("/{label}/unload", response_model=AgentActionOut)
async def unload_agent(label: str) -> AgentActionOut:
    unit = _guard(label)
    rc, out = await _run(_SYSTEMCTL, "stop", unit)
    if rc != 0:
        log.info("unit %s stop returned %s: %s", unit, rc, out)
    return AgentActionOut(label=label, action="unload", exit_code=rc, output=out)


@router.post("/{label}/kickstart", response_model=AgentActionOut)
async def kickstart_agent(label: str) -> AgentActionOut:
    unit = _guard(label)
    rc, out = await _run(_SYSTEMCTL, "restart", unit)
    if rc != 0:
        raise HTTPException(500, detail=f"restart failed: {out}")
    log.info("unit %s restarted", unit)
    return AgentActionOut(label=label, action="kickstart", exit_code=rc, output=out)
