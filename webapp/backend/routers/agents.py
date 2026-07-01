"""launchd control for the pipeline agents.

Runs `launchctl bootstrap/bootout/kickstart` under `gui/$(id -u)`. The
Aqua GUI domain is what the existing agents live in (see Phase 9), and
it's the only domain a non-root user can bootstrap into without sudo.
Labels are strictly whitelisted so a caller can't drive launchctl at
arbitrary services.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from webapp.backend import settings

log = logging.getLogger("webapp.routers.agents")

router = APIRouter(prefix="/agents", tags=["agents"])

_ALLOWED_LABELS: set[str] = {
    "com.sebastian.tiktok-upload",
    "com.sebastian.tiktok-confirm",
    "com.sebastian.tiktok-bot",
    # tiktok-webapp is deliberately excluded — we're running inside it,
    # so `bootout` would kill this process mid-response. Kickstart-only
    # is exposed for it via the /restart-self route below (currently
    # unimplemented — restart via CLI).
}

_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
_LAUNCHCTL = "/bin/launchctl"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _plist_path(label: str) -> Path:
    p = _LAUNCH_AGENTS_DIR / f"{label}.plist"
    if not p.exists():
        raise HTTPException(404, detail=f"plist symlink missing: {p}")
    return p


async def _run(*argv: str) -> tuple[int, str]:
    """Run launchctl, capture merged stderr+stdout."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


def _guard(label: str) -> None:
    if label not in _ALLOWED_LABELS:
        raise HTTPException(404, detail=f"label {label!r} not managed by webapp")


class AgentActionOut(BaseModel):
    label: str
    action: Literal["load", "unload", "kickstart"]
    exit_code: int
    output: str


@router.post("/{label}/load", response_model=AgentActionOut)
async def load_agent(label: str) -> AgentActionOut:
    _guard(label)
    plist = _plist_path(label)
    # bootstrap is idempotent-ish: fails if the service is already
    # bootstrapped. So bootout first, best-effort.
    await _run(_LAUNCHCTL, "bootout", f"{_domain()}/{label}")
    rc, out = await _run(_LAUNCHCTL, "bootstrap", _domain(), str(plist))
    if rc != 0:
        raise HTTPException(500, detail=f"bootstrap failed: {out}")
    log.info("agent %s bootstrapped", label)
    return AgentActionOut(label=label, action="load", exit_code=rc, output=out)


@router.post("/{label}/unload", response_model=AgentActionOut)
async def unload_agent(label: str) -> AgentActionOut:
    _guard(label)
    rc, out = await _run(_LAUNCHCTL, "bootout", f"{_domain()}/{label}")
    if rc != 0:
        # bootout returns non-zero if the service wasn't loaded; treat as
        # informational rather than an error so the UI stays responsive.
        log.info("agent %s bootout returned %s: %s", label, rc, out)
    return AgentActionOut(label=label, action="unload", exit_code=rc, output=out)


@router.post("/{label}/kickstart", response_model=AgentActionOut)
async def kickstart_agent(label: str) -> AgentActionOut:
    _guard(label)
    rc, out = await _run(_LAUNCHCTL, "kickstart", "-k", f"{_domain()}/{label}")
    if rc != 0:
        raise HTTPException(500, detail=f"kickstart failed: {out}")
    log.info("agent %s kickstarted", label)
    return AgentActionOut(label=label, action="kickstart", exit_code=rc, output=out)
