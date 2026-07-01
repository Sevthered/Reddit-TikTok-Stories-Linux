#!/usr/bin/env python3
"""Telegram failure notifier for systemd OnFailure=.

Invoked as:
    tiktok-notify@<failing-unit>.service
    -> ExecStart=... /srv/tiktok/app/scripts/notify.py <failing-unit>

Behavior:
- Sends a Telegram DM containing unit name, exit code, and the last
  20 journal lines from the failing unit.
- Per-unit anti-crash-loop guard: skip if the same unit alerted in
  the last 10 minutes.
- Optional quiet hours 01:00-07:00 Europe/Madrid.
- Reuses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from the environment
  (systemd EnvironmentFile= /srv/tiktok/app/.env).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


QUIET_HOURS = (1, 7)                       # (inclusive_start_hour, exclusive_end_hour)
STATE_DIR = Path("/tmp/tiktok-notify")     # falls back to /tmp if /run/user/... unavailable
DEDUP_SECONDS = 600


def _dedup_skip(unit: str) -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = STATE_DIR / unit.replace("/", "_")
    now = time.time()
    if state.exists() and now - state.stat().st_mtime < DEDUP_SECONDS:
        return True
    state.touch()
    return False


def _in_quiet_hours() -> bool:
    hour = datetime.now(ZoneInfo("Europe/Madrid")).hour
    start, end = QUIET_HOURS
    return start <= hour < end


def _last_journal(unit: str, lines: int = 20) -> str:
    try:
        out = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout or "(no journal output)"
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"(journalctl error: {exc})"


def _exit_code(unit: str) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "ExecMainStatus", "--value", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return (out.stdout or "").strip() or "?"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "?"


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    urllib.request.urlopen(url, data=payload, timeout=15)  # noqa: S310 — trusted URL


def main() -> int:
    unit = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    if _dedup_skip(unit):
        return 0
    if _in_quiet_hours():
        return 0

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("notify.py: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return 1

    exit_code = _exit_code(unit)
    journal = _last_journal(unit)
    msg = f"⚠️ {unit} FAILED (exit={exit_code})\n\n{journal[-3000:]}"
    try:
        _send_telegram(token, chat_id, msg)
    except Exception as exc:  # noqa: BLE001 — best-effort notifier
        print(f"notify.py: telegram send failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
