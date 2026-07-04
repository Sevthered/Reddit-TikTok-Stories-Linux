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
# CONFIG_PATH is env-overridable so the k8s deploy points it at the writable PVC
# (/app/data/config.toml) — the config-editor's atomic write then persists across
# pod restarts. Unset → repo-relative config.toml (unchanged for systemd/local).
CONFIG_PATH: Path = (
    Path(os.environ["CONFIG_PATH"]) if os.environ.get("CONFIG_PATH")
    else REPO_ROOT / "config.toml"
)

# Secrets file (P0.4, research run 7): prod points this at
# /etc/tiktok/environment (same file systemd's EnvironmentFile= loads
# into every unit) — outside the git working directory, so a repo
# backup/tarball/future release-dir deploy can't accidentally capture
# it. Falls back to the repo-local .env for local dev where that path
# doesn't exist. WEBAPP_ENV_PATH overrides explicitly if ever needed.
_PROD_ENV_PATH = Path("/etc/tiktok/environment")
_env_path_override = os.environ.get("WEBAPP_ENV_PATH", "")
ENV_PATH: Path = (
    Path(_env_path_override) if _env_path_override
    else _PROD_ENV_PATH if _PROD_ENV_PATH.exists()
    else REPO_ROOT / ".env"
)
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

# Origin allowlist (R2.1, research run 3): "belt-and-braces" on top of
# CSRF — reject a mutating request whose Origin header doesn't match
# one of these, on the theory that a legitimate same-origin fetch()
# either omits Origin or sends exactly one of these. Derived from
# ALLOWED_HOSTS (which already carries the real prod hostname via
# WEBAPP_ALLOWED_HOSTS) rather than a second hostname list to maintain.
ALLOWED_ORIGINS: set[str] = set()
for _h in ALLOWED_HOSTS:
    _scheme = "http" if _h.split(":")[0] in ("127.0.0.1", "localhost") else "https"
    ALLOWED_ORIGINS.add(f"{_scheme}://{_h}")
if DEV_MODE:
    ALLOWED_ORIGINS.update(DEV_ORIGINS)

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

# Internal service token — authenticates trusted loopback callers (the
# Telegram bot via core/webapp_client.py) that reach this API off the
# Cloudflare Tunnel and thus carry neither a Cf-Access-Jwt-Assertion nor a
# CSRF token. When set (via /etc/tiktok/environment, shared by the webapp and
# bot units), a request bearing X-Internal-Token: <this> skips the Cf-Access
# and CSRF checks — see app._is_trusted_internal. When UNSET the internal path
# is disabled entirely: no bypass, middleware behaves exactly as before. A
# bearer secret keeps the fail-closed guarantee (a misconfigured Tunnel
# reaching the origin still lacks it) rather than trusting the network path.
INTERNAL_TOKEN: str = os.environ.get("WEBAPP_INTERNAL_TOKEN", "")

# Rate limiting (P0.2, research runs 3 + 7). Applied per-route via
# @limiter.limit() decorators (see webapp/backend/rate_limit.py's
# docstring for why the middleware-based approach doesn't work against
# this FastAPI version) — protects against authenticated abuse and
# accidental client loops, complementing (not replacing) the
# Cloudflare edge Rate Limiting Rule in front of the Tunnel.
#
# Two tiers: mutating routes get the tighter default; read routes get
# a higher ceiling (R2.3) since the fastest UI poll observed is every
# 3s (~20/min for a single tab) -- 300/min covers several open tabs
# with room to spare while still bounding a genuine flood.
RATE_LIMIT_DEFAULT: str = os.environ.get("WEBAPP_RATE_LIMIT_DEFAULT", "120/minute")
RATE_LIMIT_READ_DEFAULT: str = os.environ.get("WEBAPP_RATE_LIMIT_READ_DEFAULT", "300/minute")

# Cloudflare Access JWT validation (R2.4, research runs 3 + 7). Zero Trust
# injects a signed Cf-Access-Jwt-Assertion header on every request that
# passes its edge policy; the app now verifies it server-side too instead
# of trusting the network path alone (defense-in-depth against a future
# Tunnel/firewall misconfiguration — see
# wiki/bugs/2026-07-03-webapp-lan-bypass-firewall.md for a real instance
# of that class of bug). Neither value below is a secret: the AUD tag
# just identifies which Access application to expect, and the JWKS
# endpoint serves public key material — safe to default in code, still
# overridable via env if the Access app is ever recreated.
CF_ACCESS_TEAM_DOMAIN: str = os.environ.get(
    "WEBAPP_CF_ACCESS_TEAM_DOMAIN", "polished-wind-0447.cloudflareaccess.com"
)
CF_ACCESS_AUD: str = os.environ.get(
    "WEBAPP_CF_ACCESS_AUD",
    "994b62689401fa9e192a9f4f5c11dda668af831e6550a3224e9a21c8502b961d",
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
