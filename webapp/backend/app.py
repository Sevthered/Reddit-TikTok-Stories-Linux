"""FastAPI entry point for the dashboard.

Run locally:
    ./venv/bin/python -m uvicorn webapp.backend.app:app --reload
Or under launchd (Phase 9):
    ./venv/bin/python -m uvicorn webapp.backend.app:app --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse

import sys
from pathlib import Path

from core.config import _load_dotenv
from webapp.backend import settings
from webapp.backend.jobs import JobManager
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
    status,
)

log = logging.getLogger("webapp")


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
