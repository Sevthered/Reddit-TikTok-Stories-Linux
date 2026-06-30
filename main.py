from __future__ import annotations

import argparse
import logging
import sys

from pathlib import Path

from core.config import ConfigError, load_config
from core.db import Db
from core.logging_setup import setup_logging
from pipeline.assemble import render
from pipeline.background import ensure_cached, make_clip, pick_random_cached
from pipeline.captions import build_ass
from pipeline.clean import normalize
from pipeline.filter import keep
from pipeline.scrape import fetch_candidates
from pipeline.transcribe import transcribe
from pipeline.tts import synthesize

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

        bgs = ensure_cached(cfg)
        log.info("background cache: %d source(s) ready", len(bgs))

        candidates = fetch_candidates(cfg)
        log.info("fetched %d candidate stories total", len(candidates))

        picked = 0
        for story in candidates:
            if picked >= target:
                break
            if not keep(story, cfg, db):
                continue
            print("=" * 72)
            print(f"CANDIDATE: r/{story.subreddit}  id={story.id}  score={story.score}  words={story.word_count}")
            print(f"TITLE : {story.title}")
            print(f"URL   : https://reddit.com{story.permalink}")
            print(f"PREVIEW: {_preview(story.selftext)}")
            print("=" * 72)

            work_dir = Path("data/temp") / story.id
            work_dir.mkdir(parents=True, exist_ok=True)
            spoken = normalize(story, cfg)
            (work_dir / "spoken.txt").write_text(spoken, encoding="utf-8")
            log.info("clean: %d chars / %d words ready for tts",
                     len(spoken), len(spoken.split()))

            audio = synthesize(spoken, cfg, work_dir)
            print(f"AUDIO : {audio.path}  duration={audio.duration_s:.2f}s  too_long={audio.too_long}")
            if audio.too_long:
                log.warning("skip %s: audio %.2fs exceeds target_max_seconds=%d",
                            story.id, audio.duration_s, cfg.video.target_max_seconds)
                db.mark_used(story.id, title=story.title, platform="skipped:too_long")
                continue
            if audio.duration_s < cfg.video.target_min_seconds:
                log.warning("skip %s: audio %.2fs below target_min_seconds=%d (monetization floor)",
                            story.id, audio.duration_s, cfg.video.target_min_seconds)
                db.mark_used(story.id, title=story.title, platform="skipped:too_short")
                continue

            bg_path = pick_random_cached(bgs)
            clip = make_clip(bg_path, audio.duration_s, cfg, work_dir / "bg.mp4")
            print(f"BG    : {clip.source.name} @ {clip.start_s:.2f}s -> {clip.path}")

            words = transcribe(audio.path, cfg)
            ass_path = build_ass(words, cfg, work_dir / "captions.ass",
                                 voice_duration_s=audio.duration_s)
            print(f"CAPS  : {len(words)} words -> {ass_path}")

            final = render(clip.path, audio.path, ass_path, cfg,
                           Path("data/output") / f"{story.id}.mp4")
            print(f"FINAL : {final}")

            db.mark_used(story.id, title=story.title, platform="rendered")
            picked += 1
            print(f"PICKED #{picked}: {story.id}")

        if picked == 0:
            log.warning("no candidates passed filter (all used / out-of-range / NSFW / low-score / profane)")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
