from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS used (
    post_id    TEXT PRIMARY KEY,
    title      TEXT,
    platform   TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @classmethod
    @contextmanager
    def open(cls, path: str | Path = "data/used_stories.db"):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p)
        try:
            conn.execute(_SCHEMA)
            conn.commit()
            yield cls(conn)
        finally:
            conn.close()

    def is_used(self, post_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM used WHERE post_id = ?", (post_id,))
        return cur.fetchone() is not None

    def mark_used(self, post_id: str, title: str, platform: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO used (post_id, title, platform) VALUES (?, ?, ?)",
            (post_id, title, platform),
        )
        self._conn.commit()
