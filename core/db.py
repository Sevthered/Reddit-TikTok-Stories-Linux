from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS used (
    post_id    TEXT PRIMARY KEY,
    title      TEXT,
    platform   TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


# Phase 6 columns added to `used` (idempotent additions guarded by
# `_add_column_if_missing`). Kept in a data-declared list so `open()` can
# migrate an existing SQLite file on-open without any external tooling.
_PHASE6_COLUMNS: list[tuple[str, str]] = [
    ("subreddit",       "TEXT"),
    ("author",          "TEXT"),
    ("caption",         "TEXT"),
    ("video_path",      "TEXT"),
    ("cover_path",      "TEXT"),
    ("upload_status",   "TEXT DEFAULT 'pending'"),
    ("approved_at",     "TEXT"),
    ("uploaded_at",     "TEXT"),
    ("upload_attempts", "INTEGER DEFAULT 0"),
    ("last_error",      "TEXT"),
    ("next_retry_at",   "TEXT"),
    ("tiktok_url",      "TEXT"),
    ("rejected_at",     "TEXT"),
    ("telegram_msg_id", "INTEGER"),
]

_DEFAULT_CONFIG: list[tuple[str, str]] = [
    ("uploads_enabled", "1"),
]


# upload_status values.
UPLOAD_PENDING = "pending"
UPLOAD_APPROVED = "approved"
UPLOAD_UPLOADING = "uploading"
UPLOAD_POSTED_UNDER_REVIEW = "posted_under_review"
UPLOAD_POSTED = "posted"
UPLOAD_REJECTED = "rejected"
UPLOAD_FAILED = "failed"


@dataclass(frozen=True)
class RenderRow:
    post_id: str
    title: str
    subreddit: str
    author: str
    caption: str
    video_path: str
    cover_path: str
    upload_status: str
    upload_attempts: int
    next_retry_at: Optional[str]
    telegram_msg_id: Optional[int]
    uploaded_at: Optional[str] = None
    tiktok_url: Optional[str] = None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col_name: str, col_def: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")


class Db:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @classmethod
    @contextmanager
    def open(cls, path: str | Path = "data/used_stories.db"):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, isolation_level=None)  # autocommit; we manage txns
        try:
            conn.executescript(_SCHEMA)
            for col_name, col_def in _PHASE6_COLUMNS:
                _add_column_if_missing(conn, "used", col_name, col_def)
            # Backfill: rows that predate Phase 6 were inserted with only
            # (post_id, title, platform), so the review-gate has no
            # video_path/cover_path/caption to notify against. Clear their
            # upload_status so they don't show up in `pending_renders()`.
            # Anything future `mark_rendered()` writes will have a
            # non-NULL video_path and stay `pending`.
            conn.execute(
                """
                UPDATE used
                   SET upload_status = NULL
                 WHERE upload_status = 'pending'
                   AND (video_path IS NULL OR video_path = '')
                """
            )
            for key, value in _DEFAULT_CONFIG:
                conn.execute(
                    "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                    (key, value),
                )
            yield cls(conn)
        finally:
            conn.close()

    # ------------ Dedup (existing) ------------

    def is_used(self, post_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM used WHERE post_id = ?", (post_id,))
        return cur.fetchone() is not None

    def mark_used(self, post_id: str, title: str, platform: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO used (post_id, title, platform) VALUES (?, ?, ?)",
            (post_id, title, platform),
        )

    # ------------ Phase 6: render → review-gate → upload lifecycle ------------

    def mark_rendered(
        self,
        post_id: str,
        *,
        title: str,
        subreddit: str,
        author: str,
        caption: str,
        video_path: str,
        cover_path: str,
    ) -> None:
        """Insert or upgrade a row for a freshly rendered artifact. Sets
        upload_status='pending' so the review-gate notifier can pick it up."""
        self._conn.execute(
            """
            INSERT INTO used (
                post_id, title, platform, subreddit, author, caption,
                video_path, cover_path, upload_status
            )
            VALUES (?, ?, 'rendered', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                title = excluded.title,
                platform = 'rendered',
                subreddit = excluded.subreddit,
                author = excluded.author,
                caption = excluded.caption,
                video_path = excluded.video_path,
                cover_path = excluded.cover_path,
                upload_status = CASE
                    WHEN used.upload_status IN ('posted', 'posted_under_review', 'rejected')
                        THEN used.upload_status
                    ELSE ?
                END
            """,
            (post_id, title, subreddit, author, caption, video_path, cover_path,
             UPLOAD_PENDING, UPLOAD_PENDING),
        )

    def set_telegram_msg_id(self, post_id: str, msg_id: int) -> None:
        self._conn.execute(
            "UPDATE used SET telegram_msg_id = ? WHERE post_id = ?",
            (msg_id, post_id),
        )

    def approve(self, post_id: str) -> bool:
        """`pending` → `approved`. Returns True if the row transitioned."""
        cur = self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, approved_at = ?
             WHERE post_id = ? AND upload_status = ?
            """,
            (UPLOAD_APPROVED, _utc_iso(), post_id, UPLOAD_PENDING),
        )
        return cur.rowcount > 0

    def reject(self, post_id: str) -> bool:
        """`{pending, approved, failed}` → `rejected`. Returns True if the row
        transitioned. Caller is responsible for removing the video/cover files
        on success. Terminal / in-flight states (`uploading`, `posted*`,
        `rejected`) are not reversible from here."""
        cur = self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, rejected_at = ?
             WHERE post_id = ?
               AND upload_status IN (?, ?, ?)
            """,
            (UPLOAD_REJECTED, _utc_iso(), post_id,
             UPLOAD_PENDING, UPLOAD_APPROVED, UPLOAD_FAILED),
        )
        return cur.rowcount > 0

    def get_render(self, post_id: str) -> RenderRow | None:
        cur = self._conn.execute(
            """
            SELECT post_id, title, subreddit, author, caption,
                   video_path, cover_path, upload_status,
                   upload_attempts, next_retry_at, telegram_msg_id,
                   uploaded_at, tiktok_url
              FROM used
             WHERE post_id = ?
            """,
            (post_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return RenderRow(
            post_id=row[0], title=row[1] or "",
            subreddit=row[2] or "", author=row[3] or "",
            caption=row[4] or "",
            video_path=row[5] or "", cover_path=row[6] or "",
            upload_status=row[7] or UPLOAD_PENDING,
            upload_attempts=int(row[8] or 0),
            next_retry_at=row[9], telegram_msg_id=row[10],
            uploaded_at=row[11], tiktok_url=row[12],
        )

    def claim_next_upload(self, now_iso: str | None = None) -> RenderRow | None:
        """Atomically pick the oldest `approved` row whose `next_retry_at` has
        passed (or is NULL), flip it to `uploading`, and return it. Returns
        None if nothing is ready.

        Race-safe: the WHERE clause re-checks upload_status inside the same
        UPDATE, so two concurrent claimers can't both win the same row."""
        now = now_iso or _utc_iso()
        # Pick candidate id, then UPDATE-guarded on upload_status.
        cur = self._conn.execute(
            """
            SELECT post_id
              FROM used
             WHERE upload_status = ?
               AND (next_retry_at IS NULL OR next_retry_at <= ?)
             ORDER BY COALESCE(approved_at, created_at) ASC
             LIMIT 1
            """,
            (UPLOAD_APPROVED, now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        post_id = row[0]
        upd = self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, next_retry_at = NULL
             WHERE post_id = ? AND upload_status = ?
            """,
            (UPLOAD_UPLOADING, post_id, UPLOAD_APPROVED),
        )
        if upd.rowcount == 0:
            # Lost the race, try again.
            return self.claim_next_upload(now)
        return self.get_render(post_id)

    def release_uploading_claim(self, post_id: str) -> bool:
        """`uploading` → `approved` without touching attempts or error.
        For dry-runs and abort-on-shutdown scenarios where a claimed row
        should return to the queue as if nothing happened."""
        cur = self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, next_retry_at = NULL
             WHERE post_id = ? AND upload_status = ?
            """,
            (UPLOAD_APPROVED, post_id, UPLOAD_UPLOADING),
        )
        return cur.rowcount > 0

    def mark_upload_success(self, post_id: str) -> None:
        """`uploading` → `posted_under_review`. Stamps `uploaded_at`. Actual
        `posted` promotion happens after the profile-scrape confirm step."""
        self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, uploaded_at = ?, last_error = NULL
             WHERE post_id = ?
            """,
            (UPLOAD_POSTED_UNDER_REVIEW, _utc_iso(), post_id),
        )

    def confirm_posted(self, post_id: str, tiktok_url: str) -> None:
        self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, tiktok_url = ?
             WHERE post_id = ? AND upload_status = ?
            """,
            (UPLOAD_POSTED, tiktok_url, post_id, UPLOAD_POSTED_UNDER_REVIEW),
        )

    def mark_upload_failure(
        self,
        post_id: str,
        error: str,
        *,
        max_attempts: int = 3,
        backoff_schedule_s: tuple[int, ...] = (1800, 7200, 21600),
    ) -> tuple[int, bool]:
        """Increment attempt counter, schedule next retry, and either return
        to `approved` (for another go) or transition to `failed` after
        `max_attempts`.

        Returns (attempts_now, is_terminal). Backoff seconds map 1-indexed:
        attempt 1 fail → backoff_schedule_s[0], etc."""
        cur = self._conn.execute(
            "SELECT upload_attempts FROM used WHERE post_id = ?",
            (post_id,),
        )
        row = cur.fetchone()
        prior = int(row[0] or 0) if row else 0
        attempts = prior + 1
        if attempts >= max_attempts:
            self._conn.execute(
                """
                UPDATE used
                   SET upload_status = ?, upload_attempts = ?, last_error = ?,
                       next_retry_at = NULL
                 WHERE post_id = ?
                """,
                (UPLOAD_FAILED, attempts, error, post_id),
            )
            return attempts, True
        idx = min(attempts - 1, len(backoff_schedule_s) - 1)
        next_at = (datetime.now(timezone.utc)
                   + timedelta(seconds=backoff_schedule_s[idx])).isoformat(timespec="seconds")
        self._conn.execute(
            """
            UPDATE used
               SET upload_status = ?, upload_attempts = ?, last_error = ?,
                   next_retry_at = ?
             WHERE post_id = ?
            """,
            (UPLOAD_APPROVED, attempts, error, next_at, post_id),
        )
        return attempts, False

    # ------------ Phase 6: enumeration helpers ------------

    def pending_renders(self) -> list[RenderRow]:
        cur = self._conn.execute(
            """
            SELECT post_id FROM used
             WHERE upload_status = ?
             ORDER BY created_at ASC
            """,
            (UPLOAD_PENDING,),
        )
        return [self.get_render(r[0]) for r in cur.fetchall() if r[0]]  # type: ignore[misc]

    def under_review(self) -> list[RenderRow]:
        cur = self._conn.execute(
            """
            SELECT post_id FROM used
             WHERE upload_status = ?
             ORDER BY uploaded_at ASC
            """,
            (UPLOAD_POSTED_UNDER_REVIEW,),
        )
        return [self.get_render(r[0]) for r in cur.fetchall() if r[0]]  # type: ignore[misc]

    def posts_today(self, tz_hours_offset: int = 0) -> int:
        """Count rows with upload_status in {posted, posted_under_review} whose
        uploaded_at falls on today's local date (tz_hours_offset from UTC)."""
        # Uses SQLite date() with an inline offset via strftime.
        offset_h = f"{tz_hours_offset:+d} hours"
        cur = self._conn.execute(
            """
            SELECT COUNT(*) FROM used
             WHERE upload_status IN (?, ?)
               AND date(uploaded_at, ?) = date('now', ?)
            """,
            (UPLOAD_POSTED, UPLOAD_POSTED_UNDER_REVIEW, offset_h, offset_h),
        )
        return int(cur.fetchone()[0] or 0)

    def last_uploaded_at(self) -> str | None:
        cur = self._conn.execute(
            """
            SELECT MAX(uploaded_at) FROM used
             WHERE upload_status IN (?, ?)
            """,
            (UPLOAD_POSTED, UPLOAD_POSTED_UNDER_REVIEW),
        )
        return cur.fetchone()[0]

    # ------------ Phase 6: config kv ------------

    def get_config(self, key: str, default: str | None = None) -> str | None:
        cur = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else default

    def set_config(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def is_uploads_enabled(self) -> bool:
        return (self.get_config("uploads_enabled", "1") or "1") == "1"

    def set_uploads_enabled(self, enabled: bool) -> None:
        self.set_config("uploads_enabled", "1" if enabled else "0")
