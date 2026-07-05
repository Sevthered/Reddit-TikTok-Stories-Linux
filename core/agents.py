"""Snapshot the pipeline's systemd units.

Shared between the webapp `GET /api/status` and the Telegram bot's
`/status` command. Reads `systemctl show` for the units we care about
and returns (loaded, pid, last_exit_code). Never raises — a systemd
hiccup shows as an empty snapshot, callers surface `unknown`.

Labels are the systemd unit names without `.service`. This module
appends `.service` only when invoking systemctl.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def _systemd_available() -> bool:
    """True only when running under systemd as PID1 (host), False in containers.
    Mirrors libsystemd sd_booted() — /run/systemd/system exists iff systemd is init."""
    return Path("/run/systemd/system").is_dir()

AGENT_LABELS: tuple[str, ...] = (
    "tiktok-bot",
    "tiktok-upload",
    "tiktok-confirm",
    "tiktok-webapp",
    "tiktok-xvfb",
)


@dataclass(frozen=True)
class AgentStatus:
    label: str
    loaded: bool
    pid: int | None
    last_exit_code: int | None


def _systemctl_snapshot() -> dict[str, tuple[int | None, int | None]]:
    """Query systemd for {label: (pid_or_None, exit_or_None)}.

    Uses `systemctl show --value -p MainPID -p ExecMainStatus -p ActiveState`
    per unit. A unit is `loaded` iff ActiveState in {active, activating}.
    """
    snap: dict[str, tuple[int | None, int | None]] = {}
    if not _systemd_available():
        return snap
    for label in AGENT_LABELS:
        unit = f"{label}.service"
        try:
            out = subprocess.check_output(
                [
                    "systemctl", "show", unit,
                    "--property=MainPID",
                    "--property=ExecMainStatus",
                    "--property=ActiveState",
                    "--no-page",
                ],
                text=True,
                timeout=5,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning("systemctl show %s failed: %s", unit, e)
            continue

        fields: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                fields[k.strip()] = v.strip()

        active = fields.get("ActiveState", "")
        pid_raw = fields.get("MainPID", "0")
        exit_raw = fields.get("ExecMainStatus", "")

        try:
            pid = int(pid_raw)
        except ValueError:
            pid = 0
        pid = pid if pid > 0 else None

        try:
            exit_code = int(exit_raw) if exit_raw else None
        except ValueError:
            exit_code = None

        if active in ("active", "activating") or pid is not None or exit_code is not None:
            snap[label] = (pid, exit_code)
    return snap


def list_agent_status() -> list[AgentStatus]:
    """Public API — returns one AgentStatus per known label, in
    AGENT_LABELS order."""
    snap = _systemctl_snapshot()
    return [
        AgentStatus(
            label=label,
            loaded=label in snap,
            pid=snap.get(label, (None, None))[0],
            last_exit_code=snap.get(label, (None, None))[1],
        )
        for label in AGENT_LABELS
    ]
