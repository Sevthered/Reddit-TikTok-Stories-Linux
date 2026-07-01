"""Snapshot the pipeline's launchd agents.

Shared between the webapp `GET /api/status` and the Telegram bot's
`/status` command. Reads `launchctl list` and returns the four labels
we care about with (loaded, pid, last_exit_code). Never raises — a
launchd hiccup shows as an empty snapshot, callers surface `unknown`.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

AGENT_LABELS: tuple[str, ...] = (
    "com.sebastian.tiktok-bot",
    "com.sebastian.tiktok-upload",
    "com.sebastian.tiktok-confirm",
    "com.sebastian.tiktok-webapp",
)


@dataclass(frozen=True)
class AgentStatus:
    label: str
    loaded: bool
    pid: int | None
    last_exit_code: int | None


def _launchctl_snapshot() -> dict[str, tuple[int | None, int | None]]:
    """Parse `launchctl list` into {label: (pid_or_None, exit_or_None)}."""
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as e:
        log.warning("launchctl list failed: %s", e)
        return {}

    snap: dict[str, tuple[int | None, int | None]] = {}
    for line in out.splitlines()[1:]:  # skip header
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, status_s, label = parts
        try:
            pid = int(pid_s) if pid_s != "-" else None
        except ValueError:
            pid = None
        try:
            status = int(status_s)
        except ValueError:
            status = None
        snap[label] = (pid, status)
    return snap


def list_agent_status() -> list[AgentStatus]:
    """Public API — returns one AgentStatus per known label, in
    AGENT_LABELS order."""
    snap = _launchctl_snapshot()
    return [
        AgentStatus(
            label=label,
            loaded=label in snap,
            pid=snap.get(label, (None, None))[0],
            last_exit_code=snap.get(label, (None, None))[1],
        )
        for label in AGENT_LABELS
    ]
