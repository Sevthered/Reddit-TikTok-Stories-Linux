from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

import requests

from core.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Story:
    id: str
    title: str
    selftext: str
    score: int
    num_comments: int
    over_18: bool
    subreddit: str
    permalink: str
    word_count: int


_MAX_TRIES = 4
_BASE_BACKOFF_S = 2.0
_RETRY_STATUS = {429, 403, 500, 502, 503, 504}


def _fetch_one(url: str, user_agent: str) -> dict:
    headers = {"User-Agent": user_agent}
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_TRIES + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in _RETRY_STATUS and attempt < _MAX_TRIES:
                delay = _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 1)
                log.warning(
                    "reddit %s on attempt %d/%d for %s — backing off %.1fs",
                    resp.status_code, attempt, _MAX_TRIES, url, delay,
                )
                time.sleep(delay)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_exc = e
            if attempt >= _MAX_TRIES:
                break
            delay = _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 1)
            log.warning("reddit request error attempt %d/%d: %s — backing off %.1fs",
                        attempt, _MAX_TRIES, e, delay)
            time.sleep(delay)
    raise RuntimeError(f"reddit fetch failed after {_MAX_TRIES} tries: {url}") from last_exc


def _build_url(sub: str, listing: str, limit: int, time_filter: str) -> str:
    base = f"https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}"
    if listing == "top":
        base += f"&t={time_filter}"
    return base


def _parse_story(child: dict) -> Story | None:
    d = child.get("data", {})
    selftext = d.get("selftext") or ""
    if not selftext.strip():
        return None
    wc = len(selftext.split())
    return Story(
        id=d.get("id", ""),
        title=d.get("title", ""),
        selftext=selftext,
        score=int(d.get("score", 0)),
        num_comments=int(d.get("num_comments", 0)),
        over_18=bool(d.get("over_18", False)),
        subreddit=d.get("subreddit", ""),
        permalink=d.get("permalink", ""),
        word_count=wc,
    )


def fetch_candidates(cfg: Config) -> list[Story]:
    stories: list[Story] = []
    rcfg = cfg.reddit
    for i, sub in enumerate(rcfg.subreddits):
        if i > 0:
            time.sleep(random.uniform(2.0, 4.0))
        url = _build_url(sub, rcfg.listing, rcfg.limit, rcfg.time_filter)
        log.info("scraping r/%s (%s, limit=%d)", sub, rcfg.listing, rcfg.limit)
        try:
            payload = _fetch_one(url, rcfg.user_agent)
        except Exception as e:
            log.error("r/%s fetch failed, skipping: %s", sub, e)
            continue
        children = payload.get("data", {}).get("children", []) or []
        sub_stories = [s for s in (_parse_story(c) for c in children) if s is not None]
        log.info("r/%s -> %d posts (%d with selftext)", sub, len(children), len(sub_stories))
        stories.extend(sub_stories)
    return stories
