"""Repo-scoped paths, host/port, and dev-mode toggle for the dashboard.

Everything derives from the repo root so the app runs from any CWD (main
use case: systemd unit under `WorkingDirectory=/srv/tiktok/app`).
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo

_log = logging.getLogger("webapp")

# ---- Paths ----------------------------------------------------------------

# webapp/backend/settings.py → parents[2] = repo root.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

DB_PATH: Path = REPO_ROOT / "data" / "used_stories.db"
CONFIG_PATH: Path = REPO_ROOT / "config.toml"
ENV_PATH: Path = REPO_ROOT / ".env"
LOGS_DIR: Path = REPO_ROOT / "data" / "logs"
OUTPUT_DIR: Path = REPO_ROOT / "data" / "output"
COOKIES_PATH: Path = REPO_ROOT / "data" / "cookies" / "tiktok_cookies.txt"

# Frontend build output (Phase 4/9). App boots even if this is missing.
FRONTEND_BUILD_DIR: Path = REPO_ROOT / "webapp" / "frontend" / "build"

# ---- Server ---------------------------------------------------------------

HOST: str = os.environ.get("WEBAPP_HOST", "127.0.0.1")
PORT: int = int(os.environ.get("WEBAPP_PORT", "8765"))

# `WEBAPP_DEV=1` enables the dev CORS allowlist for the SvelteKit dev
# server (vite on :5173) and slightly friendlier error responses.
DEV_MODE: bool = os.environ.get("WEBAPP_DEV", "0") == "1"

DEV_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# ---- Security -------------------------------------------------------------

# Host-header allowlist middleware — closes DNS-rebinding on the
# loopback dashboard (research report §H, 2026-07-01). Extend via
# WEBAPP_ALLOWED_HOSTS (comma-separated) when binding to LAN so the
# server-IP and hostname pass the check.
ALLOWED_HOSTS: set[str] = {
    f"127.0.0.1:{PORT}",
    f"localhost:{PORT}",
    "127.0.0.1",
    "localhost",
}
_extra_hosts = os.environ.get("WEBAPP_ALLOWED_HOSTS", "")
for h in _extra_hosts.split(","):
    h = h.strip()
    if h:
        ALLOWED_HOSTS.add(h)
        if ":" not in h:
            ALLOWED_HOSTS.add(f"{h}:{PORT}")

# `WEBAPP_ALLOW_ANY_HOST=1` disables the check entirely — useful on a
# LAN-only server where the allowlist adds friction without security value.
ALLOW_ANY_HOST: bool = os.environ.get("WEBAPP_ALLOW_ANY_HOST", "0") == "1"

# CSRF double-submit secret (P0.1, research runs 3 + 7). Cloudflare Zero
# Trust's `CF_Authorization` cookie is still an ambient browser credential
# an attacker page can ride — Zero Trust authenticates the user, it does
# not stop a forged cross-site request. Set WEBAPP_CSRF_SECRET in prod
# (systemd EnvironmentFile=); falls back to a random per-boot secret in
# dev, which just means CSRF tokens don't survive a restart — fine
# locally, not acceptable behind a real deployment.
CSRF_SECRET: str = os.environ.get("WEBAPP_CSRF_SECRET", "")
if not CSRF_SECRET:
    CSRF_SECRET = secrets.token_hex(32)
    if not DEV_MODE:
        _log.warning(
            "WEBAPP_CSRF_SECRET not set — using a random per-boot secret. "
            "Every restart invalidates outstanding CSRF tokens. Set "
            "WEBAPP_CSRF_SECRET via systemd EnvironmentFile= for prod."
        )

if DEV_MODE:
    # SvelteKit dev server originates its own /api proxies with its Host,
    # which passes through unchanged.
    ALLOWED_HOSTS.update({"127.0.0.1:5173", "localhost:5173"})

# ---- Policy ---------------------------------------------------------------

# Q11 posting window is Europe/Madrid. Use IANA zone name; the previous
# int-hour offset introduced a DST hazard (flagged in the 2026-07-01
# research report). This zone is the source of truth for `posts_today`
# computations from the dashboard.
POST_TZ: ZoneInfo = ZoneInfo("Europe/Madrid")


def madrid_tz_offset_hours() -> int:
    """Current UTC offset in whole hours for POST_TZ, computed live so it
    tracks DST. Passed to `Db.posts_today(offset)` which still takes an
    int today (see Db.posts_today refactor tracked in the plan)."""
    from datetime import datetime
    off = datetime.now(POST_TZ).utcoffset()
    return int(off.total_seconds() // 3600) if off else 0
