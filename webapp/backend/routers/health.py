"""Health / liveness / readiness endpoints.

Three distinct surfaces, deliberately split (kube-books [[Kubernetes-Probes]]):

- `/api/health`  — rich human/UI body, always HTTP 200, never raises. The
  SvelteKit health card renders `db_reachable` / `config_loaded` from it, so it
  must stay 200 even when a dependency is down (the frontend `apiGet` throws on
  any non-2xx). This is NOT a kubelet probe target.
- `/api/live`    — liveness: process-up only, no dependency touches. Wiring
  liveness to a dep check would restart-loop the pod when the DB is down, which
  a restart can't fix.
- `/api/ready`   — readiness: returns 503 when the DB or config is unreachable
  so the kubelet pulls the pod out of the Service endpoints until it recovers,
  instead of routing traffic into a broken replica.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response

from core.config import ConfigError, load_config
from core.db import Db
from webapp.backend import settings
from webapp.backend.deps import get_db

log = logging.getLogger("webapp.health")

router = APIRouter(tags=["health"])


def _probe_deps(db: Db) -> tuple[bool, bool, str | None]:
    """Return (db_ok, cfg_ok, cfg_err). Never raises."""
    db_ok = True
    try:
        db.is_uploads_enabled()
    except Exception as e:  # noqa: BLE001 - health probe, swallow all
        log.warning("db probe failed: %s", e)
        db_ok = False

    cfg_ok = True
    cfg_err: str | None = None
    try:
        load_config(str(settings.CONFIG_PATH))
    except (ConfigError, FileNotFoundError, Exception) as e:  # noqa: BLE001
        cfg_ok = False
        cfg_err = str(e)[:200]
    return db_ok, cfg_ok, cfg_err


@router.get("/live")
def live() -> dict[str, str]:
    """Liveness: the process can serve HTTP. No dependency checks — a dead DB
    must never trigger a kubelet restart (a restart won't fix it)."""
    return {"status": "alive"}


@router.get("/ready")
def ready(response: Response, db: Db = Depends(get_db)) -> dict[str, Any]:
    """Readiness: 200 when the DB + config are reachable, else 503 so the
    kubelet removes this pod from the Service endpoints until it recovers."""
    db_ok, cfg_ok, cfg_err = _probe_deps(db)
    ok = db_ok and cfg_ok
    if not ok:
        response.status_code = 503
    return {"ok": ok, "db_reachable": db_ok, "config_loaded": cfg_ok,
            "config_error": cfg_err}


@router.get("/health")
def health(db: Db = Depends(get_db)) -> dict[str, Any]:
    """Verify: (1) DB reachable + schema present, (2) config.toml parses,
    (3) .env / cookies file accessible. Never raises — always returns a
    JSON body (HTTP 200) the SvelteKit health card can render."""
    db_ok, cfg_ok, cfg_err = _probe_deps(db)

    # Report file BASENAMES only — the health card just needs the filename;
    # exposing absolute server paths is needless disclosure ([[Excessive-Data-Exposure]]).
    return {
        "ok": db_ok and cfg_ok,
        "db_reachable": db_ok,
        "db_path": Path(str(settings.DB_PATH)).name,
        "config_loaded": cfg_ok,
        "config_path": Path(str(settings.CONFIG_PATH)).name,
        "config_error": cfg_err,
        "env_path_exists": settings.ENV_PATH.exists(),
        "cookies_exists": settings.COOKIES_PATH.exists(),
        "logs_dir": Path(str(settings.LOGS_DIR)).name,
        "dev_mode": settings.DEV_MODE,
        "post_tz": str(settings.POST_TZ),
        "madrid_offset_hours": settings.madrid_tz_offset_hours(),
    }
