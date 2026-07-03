"""FastAPI entry point for the dashboard.

Run locally:
    ./.venv/bin/python -m uvicorn webapp.backend.app:app --reload
Or under the tiktok-webapp.service systemd unit:
    ./.venv/bin/python -m uvicorn webapp.backend.app:app --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi_csrf_protect import CsrfProtect
from fastapi_csrf_protect.exceptions import CsrfProtectError
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse, PlainTextResponse

import sys
from pathlib import Path

from core.config import _load_dotenv
from webapp.backend import settings
from webapp.backend.jobs import JobManager
from webapp.backend.rate_limit import limiter
from webapp.backend.routers import (
    actions,
    agents,
    artifacts,
    config,
    cookie,
    health,
    jobs,
    logs,
    renders,
    schedule,
    status,
)

log = logging.getLogger("webapp")


# ---- CSRF (double-submit cookie) -------------------------------------------
#
# Cloudflare Zero Trust authenticates the user; it does not stop a forged
# cross-site request riding the browser's ambient `CF_Authorization`
# cookie. Double-submit CSRF closes that gap (research runs 3 + 7).
class CsrfSettings(BaseModel):
    secret_key: str = settings.CSRF_SECRET
    cookie_samesite: str = "lax"
    cookie_secure: bool = not settings.DEV_MODE
    header_name: str = "X-CSRF-Token"


@CsrfProtect.load_config
def _csrf_config() -> CsrfSettings:
    return CsrfSettings()


# Methods that mutate state — everything else (GET/HEAD/OPTIONS) is exempt
# by definition since CSRF only matters for state-changing requests.
_CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Paths exempt from CSRF: issuing the token itself, and health/status reads
# that happen to use POST-adjacent verbs nowhere today but kept explicit.
_CSRF_EXEMPT_PATHS = {"/api/csrf"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan hook — Phase 6+ will attach a JobManager to app.state
    here. For now just log the boot info once."""
    # Load .env into os.environ so notify.Notifier.from_env() picks up
    # TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID when the actions router edits
    # review captions after approve/reject via web.
    _load_dotenv(settings.ENV_PATH)
    # JobManager owns the pipeline subprocess fleet (Phase 6). Bound to
    # the running loop so `asyncio.create_subprocess_exec` in start()
    # picks up the correct event loop.
    app.state.jobs = JobManager(
        python=Path(sys.executable),
        repo_root=settings.REPO_ROOT,
    )
    log.info("webapp boot: dev_mode=%s host=%s port=%d db=%s",
             settings.DEV_MODE, settings.HOST, settings.PORT, settings.DB_PATH)
    yield
    log.info("webapp shutdown")


app = FastAPI(
    title="Reddit → TikTok Control Plane",
    version="0.1.0",
    lifespan=lifespan,
    # Turn off the interactive docs on `/` so they don't shadow the
    # SvelteKit SPA once we mount it (Phase 9). Explicit endpoints keep
    # /docs and /openapi.json available during development.
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # slowapi's own default handler returns {"error": ...}; every other
    # error path in this API returns {"detail": ...} (FastAPI's default
    # HTTPException shape) — normalize so frontend error parsing
    # (apiPut/apiPost/apiDelete reading `j?.detail`) works uniformly.
    return JSONResponse(status_code=429, content={"detail": f"rate limit exceeded: {exc.detail}"})


# No SlowAPIMiddleware — see webapp/backend/rate_limit.py docstring for
# why its auto-detection is non-functional against this FastAPI version.
# Enforcement is per-route via @limiter.limit() on the mutating routes.
# This exception handler works correctly (unlike the CSRF one above
# would if attached to a raw middleware): RateLimitExceeded raised by a
# decorated route function is raised from WITHIN the routing layer, not
# from a @app.middleware("http") function, so it's inside the scope
# FastAPI's exception handlers actually cover.


# ---- Host-header allowlist -------------------------------------------------
#
# Even on 127.0.0.1 we defend against DNS rebinding: an attacker page
# resolves a hostname to 127.0.0.1 and drives this API from a victim's
# browser, using whatever cookies were set. The Host header the browser
# sends in that scenario is the attacker's hostname, not one of ours —
# so a strict allowlist kills the attack. Ref: research §H (2026-07-01).
@app.middleware("http")
async def host_allowlist(request: Request, call_next):
    if settings.ALLOW_ANY_HOST:
        return await call_next(request)
    host = (request.headers.get("host") or "").lower()
    if host not in settings.ALLOWED_HOSTS:
        log.warning("rejected host header %r from %s", host, request.client)
        return PlainTextResponse(
            f"Host header not allowed: {host!r}", status_code=400,
        )
    return await call_next(request)


@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    # NOTE: a `@app.exception_handler(CsrfProtectError)` does NOT catch
    # exceptions raised here — Starlette's BaseHTTPMiddleware (which
    # `@app.middleware("http")` wraps) runs OUTSIDE the ExceptionMiddleware
    # layer that FastAPI's exception handlers attach to. An uncaught raise
    # in this function propagates as a raw 500, not the intended 403.
    # Caught + converted to a response right here instead.
    if (
        request.method in _CSRF_PROTECTED_METHODS
        and request.url.path not in _CSRF_EXEMPT_PATHS
    ):
        csrf_protect = CsrfProtect()
        try:
            await csrf_protect.validate_csrf(request)
        except CsrfProtectError as exc:
            log.warning("CSRF check failed: %s %s (%s)", request.method, request.url.path, exc.message)
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
    return await call_next(request)


@app.get("/api/csrf")
async def get_csrf_token(csrf_protect: CsrfProtect = Depends()):
    """Issue a CSRF token pair. Frontend fetches this once on load, keeps
    the plaintext token in memory, and echoes it back as `X-CSRF-Token` on
    every mutating request. The signed half lives in an HttpOnly cookie —
    JS never reads the cookie directly, only the plaintext token from this
    response body, so this is the signed variant of double-submit."""
    csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
    response = JSONResponse({"csrf_token": csrf_token})
    csrf_protect.set_csrf_cookie(signed_token, response)
    return response


# ---- CORS (dev only) ------------------------------------------------------
#
# In prod, SvelteKit is served by FastAPI (Phase 9 via app.frontend()) so
# every request is same-origin — no CORS at all. In dev, Vite's proxy
# also keeps things same-origin, but we allowlist :5173 anyway in case
# the user hits the vite dev server directly.
if settings.DEV_MODE:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---- Routers --------------------------------------------------------------

app.include_router(health.router, prefix="/api")
app.include_router(status.router, prefix="/api")
app.include_router(renders.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(artifacts.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(cookie.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(schedule.router, prefix="/api")


# ---- Serve SvelteKit SPA (Phase 9) ---------------------------------------
#
# FastAPI 0.138+ ships `app.frontend()` which mounts the static build as
# LOW-priority routes — every /api/* endpoint is matched first, then any
# unmatched request falls through to the SPA. adapter-static writes
# `index.html` (via `fallback: 'index.html'`) so client-side SvelteKit
# routing takes over for arbitrary paths like /queue, /config, /logs.
#
# In dev, Vite serves the SPA on :5173 and proxies /api to us — no need
# to run `pnpm build` between iterations. Skip mounting there so a stale
# build/ can't shadow the live-reloading dev server.
if not settings.DEV_MODE and settings.FRONTEND_BUILD_DIR.exists():
    app.frontend(
        "/",
        directory=str(settings.FRONTEND_BUILD_DIR),
        fallback="index.html",
        check_dir=False,
    )
    log.info("mounted SPA from %s", settings.FRONTEND_BUILD_DIR)
