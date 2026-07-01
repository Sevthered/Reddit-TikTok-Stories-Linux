"""Repo-scoped paths, host/port, and dev-mode toggle for the dashboard.

Everything derives from the repo root so the app runs from any CWD (main
use case: launchd under `WorkingDirectory=/Users/sebastian/Automated-TikTok-Upload`).
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

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
# loopback dashboard (research report §H, 2026-07-01). Extend when the
# port or bind address changes.
ALLOWED_HOSTS: set[str] = {
    f"127.0.0.1:{PORT}",
    f"localhost:{PORT}",
    "127.0.0.1",
    "localhost",
}
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
