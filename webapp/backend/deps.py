"""FastAPI dependencies — request-scoped DB handles + shared helpers."""
from __future__ import annotations

from typing import Iterator

from core.db import Db
from webapp.backend import settings


def get_db() -> Iterator[Db]:
    """Yield a Db bound to a fresh sqlite3 connection per request.

    The dependency is a SYNC generator; FastAPI runs sync deps in a
    threadpool automatically, so this doesn't block the event loop.
    `Db.open()` sets `PRAGMA busy_timeout=20000` and
    `check_same_thread=False` so this pattern is safe alongside the
    long-running Telegram bot writer (research report §G, 2026-07-01)."""
    with Db.open(settings.DB_PATH) as db:
        yield db
