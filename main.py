from __future__ import annotations

import argparse
import logging
import sys

from core.config import ConfigError, load_config
from core.db import Db
from core.logging_setup import setup_logging
from pipeline.filter import keep
from pipeline.scrape import fetch_candidates

log = logging.getLogger("main")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reddit-Story -> TikTok bot")
    p.add_argument("--dry-run", action="store_true", help="skip upload step")
    p.add_argument("--approve", metavar="POST_ID", help="approve a reviewed video for upload (not implemented yet)")
    p.add_argument("--limit", type=int, help="override run.videos_per_run")
    p.add_argument("--mark-used", metavar="POST_ID",
                   help="admin: mark a post id as used so it's skipped on next run")
    return p.parse_args()


def _preview(text: str, n: int = 200) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[:n].rstrip() + "..."


def main() -> int:
    args = _parse_args()
    setup_logging()

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    with Db.open() as db:
        if args.mark_used:
            db.mark_used(args.mark_used, title="(manual mark)", platform="manual")
            log.info("marked %s as used", args.mark_used)
            return 0

        if args.approve:
            print("--approve not implemented yet (Phase 6).", file=sys.stderr)
            return 1

        target = args.limit if args.limit is not None else cfg.run.videos_per_run
        log.info("target videos this run: %d", target)

        candidates = fetch_candidates(cfg)
        log.info("fetched %d candidate stories total", len(candidates))

        picked = 0
        for story in candidates:
            if picked >= target:
                break
            if not keep(story, cfg, db):
                continue
            picked += 1
            print("=" * 72)
            print(f"PICKED: r/{story.subreddit}  id={story.id}  score={story.score}  words={story.word_count}")
            print(f"TITLE : {story.title}")
            print(f"URL   : https://reddit.com{story.permalink}")
            print(f"PREVIEW: {_preview(story.selftext)}")
            print("=" * 72)

        if picked == 0:
            log.warning("no candidates passed filter (all used / out-of-range / NSFW / low-score / profane)")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
