"""GET /api/health — cheap end-to-end reachability check."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from core.config import ConfigError, load_config
from core.db import Db
from webapp.backend import settings
from webapp.backend.deps import get_db

log = logging.getLogger("webapp.health")

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Db = Depends(get_db)) -> dict[str, Any]:
    """Verify: (1) DB reachable + schema present, (2) config.toml parses,
    (3) .env / cookies file accessible. Never raises — always returns a
    JSON body the SvelteKit health card can render."""
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

    return {
        "ok": db_ok and cfg_ok,
        "db_reachable": db_ok,
        "db_path": str(settings.DB_PATH),
        "config_loaded": cfg_ok,
        "config_path": str(settings.CONFIG_PATH),
        "config_error": cfg_err,
        "env_path_exists": settings.ENV_PATH.exists(),
        "cookies_exists": settings.COOKIES_PATH.exists(),
        "logs_dir": str(settings.LOGS_DIR),
        "dev_mode": settings.DEV_MODE,
        "post_tz": str(settings.POST_TZ),
        "madrid_offset_hours": settings.madrid_tz_offset_hours(),
    }
