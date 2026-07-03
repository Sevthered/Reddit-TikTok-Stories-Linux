"""baseline existing schema

Revision ID: 0001
Revises:
Create Date: 2026-07-03 13:05:59.288809

Reproduces the schema core/db.py has hand-managed since the project began
(base `_SCHEMA` string, core/db.py:11-31, plus the 14 additive "Phase 6"
columns from `_PHASE6_COLUMNS`, core/db.py:48-63). This migration is not
meant to run standalone -- every real environment already has this schema
via core/db.py's own idempotent bootstrap (`Db.open()`), which is
deliberately left untouched. `alembic stamp head` is the intended way to
adopt this baseline on an existing DB (marks it applied without re-running
DDL); `alembic upgrade head` also works from empty (CREATE TABLE IF NOT
EXISTS makes both paths safe). See
wiki/decisions/2026-07-03-alembic-manual-migrations.md.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the current hand-rolled schema, if not already present."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS used (
            post_id         TEXT PRIMARY KEY,
            title           TEXT,
            platform        TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            subreddit       TEXT,
            author          TEXT,
            caption         TEXT,
            video_path      TEXT,
            cover_path      TEXT,
            upload_status   TEXT DEFAULT 'pending',
            approved_at     TEXT,
            uploaded_at     TEXT,
            upload_attempts INTEGER DEFAULT 0,
            last_error      TEXT,
            next_retry_at   TEXT,
            tiktok_url      TEXT,
            rejected_at     TEXT,
            telegram_msg_id INTEGER
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            instance     TEXT PRIMARY KEY,
            render_time  TEXT NOT NULL,
            upload_time  TEXT NOT NULL,
            auto_approve INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def downgrade() -> None:
    """Drop everything. Destructive by nature for a baseline revision --
    there is no earlier state to return to."""
    op.execute("DROP TABLE IF EXISTS slots")
    op.execute("DROP TABLE IF EXISTS config")
    op.execute("DROP TABLE IF EXISTS used")
