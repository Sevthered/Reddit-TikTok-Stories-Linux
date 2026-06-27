from __future__ import annotations

import logging
import re

from core.config import Config
from core.db import Db
from pipeline.scrape import Story

log = logging.getLogger(__name__)


PROFANITY: frozenset[str] = frozenset({
    "fuck", "fucking", "fucked", "fucker",
    "shit", "shitty", "bullshit",
    "bitch", "bitches",
    "cunt",
    "asshole", "assholes",
    "dick", "dicks",
    "pussy",
    "cock", "cocks",
    "whore", "whores",
    "slut", "sluts",
    "bastard",
    "nigger", "nigga", "faggot", "fag", "retard", "retarded",
})

_WORD_RE = re.compile(r"\b[a-zA-Z']+\b")


def _has_profanity(text: str) -> bool:
    for m in _WORD_RE.finditer(text):
        if m.group(0).lower() in PROFANITY:
            return True
    return False


def keep(story: Story, cfg: Config, db: Db) -> bool:
    if db.is_used(story.id):
        log.debug("reject %s: already used", story.id)
        return False
    if story.over_18 and not cfg.filter.allow_nsfw:
        log.debug("reject %s: nsfw", story.id)
        return False
    if not (cfg.filter.min_words <= story.word_count <= cfg.filter.max_words):
        log.debug("reject %s: word_count=%d outside [%d,%d]",
                  story.id, story.word_count, cfg.filter.min_words, cfg.filter.max_words)
        return False
    if cfg.reddit.mode == "rss":
        # RSS feed lacks score; rely on Reddit's top-sorted ordering instead.
        pass
    elif story.score < cfg.filter.min_score:
        log.debug("reject %s: score=%d < %d", story.id, story.score, cfg.filter.min_score)
        return False
    if cfg.filter.profanity_mode == "strict":
        body = f"{story.title}\n{story.selftext}"
        if _has_profanity(body):
            log.debug("reject %s: profanity (strict mode)", story.id)
            return False
    return True
