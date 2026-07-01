"""Delete rendered artifacts for posts that reached terminal state.

A row is considered terminal when:
    upload_status = 'posted' AND tiktok_url IS NOT NULL

`posted` is the confirm-scrape's final transition (from `posted_under_review`),
so the video is safely live before its local mp4/cover are removed.

Invoked by the systemd tiktok-retention.timer. Idempotent — repeated runs are
no-ops once artifacts are gone. Never mutates DB rows (only paths).
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.db import Db

log = logging.getLogger(__name__)


def sweep_with_conn(conn) -> tuple[int, int]:
    """Delete artifacts for all terminal-state rows.

    Returns (rows_scanned, files_deleted).
    """
    rows = 0
    deleted = 0
    cur = conn.execute(
        "SELECT post_id, video_path, cover_path FROM used "
        "WHERE upload_status = 'posted' AND tiktok_url IS NOT NULL"
    )
    for post_id, video_path, cover_path in cur.fetchall():
        rows += 1
        for label, p in (("video", video_path), ("cover", cover_path)):
            if not p:
                continue
            path = Path(p)
            if not path.exists():
                continue
            try:
                path.unlink()
                deleted += 1
                log.info("retention: removed %s (%s) for %s", path, label, post_id)
            except OSError as exc:
                log.warning("retention: failed to remove %s: %s", path, exc)
    return rows, deleted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with Db.open() as db:
        scanned, removed = sweep_with_conn(db._conn)  # noqa: SLF001
    log.info("retention sweep: %d terminal rows, %d files removed", scanned, removed)


if __name__ == "__main__":
    main()
