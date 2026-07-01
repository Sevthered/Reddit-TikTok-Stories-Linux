from __future__ import annotations

import argparse
import logging
import sys

from pathlib import Path

from core.config import ConfigError, load_config
from core.db import Db
from core.logging_setup import setup_logging
from core.notify import Notifier, NotifierError
from core.render_progress import RenderProgress
from pipeline.assemble import render
from pipeline.background import ensure_cached, make_clip, pick_random_cached
from pipeline.captions import build_ass
from pipeline.clean import normalize
from pipeline.cover import extract_cover, make_card
from pipeline.filter import keep
from pipeline.scrape import Story, fetch_candidates
from pipeline.transcribe import transcribe
from pipeline.tts import TTSContentRefused, synthesize

log = logging.getLogger("main")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reddit-Story -> TikTok bot")
    p.add_argument("--dry-run", action="store_true",
                   help="render only; skip the Phase 6 review-gate + Telegram notify "
                        "(row is marked rendered for dedup but not queued for upload)")
    p.add_argument("--approve", metavar="POST_ID", help="approve a reviewed video for upload (not implemented yet)")
    p.add_argument("--limit", type=int, help="override run.videos_per_run")
    p.add_argument("--mark-used", metavar="POST_ID",
                   help="admin: mark a post id as used so it's skipped on next run")
    p.add_argument("--progress-chat-id", type=int, default=None,
                   help="Telegram chat_id to send stage-by-stage progress edits to. "
                        "Requires --progress-message-id.")
    p.add_argument("--progress-message-id", type=int, default=None,
                   help="Telegram message_id (pre-created by the bot) that main.py "
                        "will edit into a checklist as each stage completes.")
    return p.parse_args()


def _preview(text: str, n: int = 200) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[:n].rstrip() + "..."


def _build_caption(story: Story) -> str:
    """Phase 6 caption template (Q12 + Q22): subreddit tag + title,
    original author attribution, small hashtag set."""
    author_tag = f"u/{story.author}" if story.author else "u/?"
    sub = story.subreddit or ""
    hashtags = " ".join(filter(None, ["#reddit", f"#{sub}" if sub else "",
                                     "#storytime", "#fyp"]))
    return (
        f"r/{sub} — {story.title}\n\n"
        f"Story by {author_tag}\n\n"
        f"{hashtags}"
    )


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

            # Optional live progress in Telegram. When both --progress-*
            # flags are set, the bot pre-created the message; we edit it
            # in place after each stage. Silently no-op if the Telegram
            # env is missing so the CLI still runs offline.
            progress: RenderProgress | None = None
            if args.progress_chat_id is not None and args.progress_message_id is not None:
                try:
                    _pn = Notifier.from_env()
                    progress = RenderProgress(
                        _pn, args.progress_chat_id, args.progress_message_id,
                        story.id, story.title,
                    )
                except NotifierError as e:
                    log.info("skip progress reporter (no notifier env): %s", e)

            spoken = normalize(story, cfg)
            (work_dir / "spoken.txt").write_text(spoken, encoding="utf-8")
            log.info("clean: %d chars / %d words ready for tts",
                     len(spoken), len(spoken.split()))

            try:
                audio = synthesize(spoken, cfg, work_dir)
            except TTSContentRefused as e:
                log.warning("skip %s: edge-tts content-refused (%s)", story.id, e)
                db.mark_used(story.id, title=story.title, platform="skipped:tts_refused")
                if progress:
                    progress.done(ok=False, note="TTS refused content")
                continue
            print(f"AUDIO : {audio.path}  duration={audio.duration_s:.2f}s  too_long={audio.too_long}")
            if audio.too_long:
                log.warning("skip %s: audio %.2fs exceeds target_max_seconds=%d",
                            story.id, audio.duration_s, cfg.video.target_max_seconds)
                db.mark_used(story.id, title=story.title, platform="skipped:too_long")
                if progress:
                    progress.done(ok=False, note="audio too long")
                continue
            if audio.duration_s < cfg.video.target_min_seconds:
                log.warning("skip %s: audio %.2fs below target_min_seconds=%d (monetization floor)",
                            story.id, audio.duration_s, cfg.video.target_min_seconds)
                db.mark_used(story.id, title=story.title, platform="skipped:too_short")
                if progress:
                    progress.done(ok=False, note="audio too short")
                continue
            if progress:
                progress.mark("tts")

            bg_path = pick_random_cached(bgs)
            clip = make_clip(bg_path, audio.duration_s, cfg, work_dir / "bg.mp4")
            print(f"BG    : {clip.source.name} @ {clip.start_s:.2f}s -> {clip.path}")
            if progress:
                progress.mark("bg")

            words = transcribe(audio.path, cfg)
            if progress:
                progress.mark("wsp")
            ass_path = build_ass(words, cfg, work_dir / "captions.ass",
                                 voice_duration_s=audio.duration_s)
            print(f"CAPS  : {len(words)} words -> {ass_path}")
            if progress:
                progress.mark("cap")

            card_path = make_card(story, work_dir / "card.png")
            print(f"CARD  : {card_path}")
            if progress:
                progress.mark("cov")

            final = render(clip.path, audio.path, ass_path, cfg,
                           Path("data/output") / f"{story.id}.mp4",
                           card_image=card_path)
            print(f"FINAL : {final}")

            cover_path = extract_cover(final, Path("data/output") / f"{story.id}_cover.png")
            print(f"COVER : {cover_path}")
            if progress:
                progress.mark("asm")
                progress.done(ok=True)

            caption = _build_caption(story)
            if args.dry_run:
                db.mark_used(story.id, title=story.title, platform="rendered")
                print(f"DRY-RUN: skipped review-gate notify for {story.id}")
            else:
                db.mark_rendered(
                    story.id,
                    title=story.title,
                    subreddit=story.subreddit,
                    author=story.author,
                    caption=caption,
                    video_path=str(final),
                    cover_path=str(cover_path),
                )
                try:
                    notifier = Notifier.from_env()
                    row = db.get_render(story.id)
                    if row is None:
                        raise RuntimeError(f"render row missing after mark_rendered({story.id})")
                    msg_id = notifier.send_review_request(row)
                    db.set_telegram_msg_id(story.id, msg_id)
                    print(f"NOTIFY: telegram msg_id={msg_id}")
                except NotifierError as e:
                    log.warning("skip review-request for %s: %s", story.id, e)
                    print(f"NOTIFY: skipped ({e})")
                except Exception as e:
                    log.exception("review-request crashed for %s", story.id)
                    print(f"NOTIFY: error ({e})")

            picked += 1
            print(f"PICKED #{picked}: {story.id}")

        if picked == 0:
            log.warning("no candidates passed filter (all used / out-of-range / NSFW / low-score / profane)")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
