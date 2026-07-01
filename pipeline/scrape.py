from __future__ import annotations

import html
import logging
import random
import re
import time
import xml.etree.ElementTree as ET
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
    author: str = ""


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
        author=d.get("author") or "",
    )


def _fetch_via_json(cfg: Config) -> list[Story]:
    stories: list[Story] = []
    rcfg = cfg.reddit
    for i, sub in enumerate(rcfg.subreddits):
        if i > 0:
            time.sleep(random.uniform(2.0, 4.0))
        url = _build_url(sub, rcfg.listing, rcfg.limit, rcfg.time_filter)
        log.info("scraping r/%s (%s, limit=%d) via json", sub, rcfg.listing, rcfg.limit)
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


def _submission_to_story(s) -> Story | None:
    selftext = (s.selftext or "")
    if not selftext.strip():
        return None
    wc = len(selftext.split())
    author = ""
    try:
        author = s.author.name if s.author else ""
    except Exception:
        author = ""
    return Story(
        id=s.id,
        title=s.title or "",
        selftext=selftext,
        score=int(s.score or 0),
        num_comments=int(s.num_comments or 0),
        over_18=bool(s.over_18),
        subreddit=str(s.subreddit),
        permalink=s.permalink or "",
        word_count=wc,
        author=author,
    )


def _fetch_via_praw(cfg: Config) -> list[Story]:
    import praw  # local import; only needed in praw mode

    rcfg = cfg.reddit
    reddit = praw.Reddit(
        client_id=rcfg.client_id,
        client_secret=rcfg.client_secret,
        user_agent=rcfg.user_agent,
    )
    reddit.read_only = True

    stories: list[Story] = []
    for i, sub in enumerate(rcfg.subreddits):
        if i > 0:
            time.sleep(random.uniform(1.0, 2.0))
        log.info("scraping r/%s (%s, limit=%d) via praw", sub, rcfg.listing, rcfg.limit)
        try:
            sr = reddit.subreddit(sub)
            if rcfg.listing == "top":
                gen = sr.top(time_filter=rcfg.time_filter, limit=rcfg.limit)
            elif rcfg.listing == "hot":
                gen = sr.hot(limit=rcfg.limit)
            else:
                gen = sr.new(limit=rcfg.limit)
            subs_out: list[Story] = []
            count = 0
            for s in gen:
                count += 1
                st = _submission_to_story(s)
                if st is not None:
                    subs_out.append(st)
        except Exception as e:
            log.error("r/%s praw fetch failed, skipping: %s", sub, e)
            continue
        log.info("r/%s -> %d posts (%d with selftext)", sub, count, len(subs_out))
        stories.extend(subs_out)
    return stories


_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(s: str) -> str:
    # drop the reddit footer ("submitted by ... [link] [comments]")
    cut = s.split("<!-- SC_ON -->", 1)[0]
    # paragraph breaks -> double newlines before stripping tags
    cut = re.sub(r"</p\s*>", "\n\n", cut, flags=re.IGNORECASE)
    cut = re.sub(r"<br\s*/?>", "\n", cut, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", cut)
    text = html.unescape(text)
    # collapse runs of spaces but keep newlines
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


def _build_rss_url(sub: str, listing: str, limit: int, time_filter: str) -> str:
    base = f"https://www.reddit.com/r/{sub}/{listing}.rss?limit={limit}"
    if listing == "top":
        base += f"&t={time_filter}"
    return base


def _parse_rss_entry(entry: ET.Element, subreddit: str) -> Story | None:
    raw_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
    post_id = raw_id.replace("t3_", "") if raw_id.startswith("t3_") else raw_id
    title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
    content_el = entry.find(f"{_ATOM_NS}content")
    content_html = content_el.text if content_el is not None and content_el.text else ""
    selftext = _html_to_text(content_html)
    if not selftext.strip():
        return None
    link_el = entry.find(f"{_ATOM_NS}link")
    href = link_el.get("href", "") if link_el is not None else ""
    permalink = href.replace("https://www.reddit.com", "") if href else ""
    author_el = entry.find(f"{_ATOM_NS}author/{_ATOM_NS}name")
    author = (author_el.text or "").strip() if author_el is not None else ""
    if author.startswith("/u/"):
        author = author[3:]
    elif author.startswith("u/"):
        author = author[2:]
    return Story(
        id=post_id,
        title=title,
        selftext=selftext,
        score=0,                # not in RSS
        num_comments=0,         # not in RSS
        over_18=False,          # not in RSS — RSS feed itself omits flagged content for unauth
        subreddit=subreddit,
        permalink=permalink,
        word_count=len(selftext.split()),
        author=author,
    )


def _fetch_via_rss(cfg: Config) -> list[Story]:
    rcfg = cfg.reddit
    stories: list[Story] = []
    for i, sub in enumerate(rcfg.subreddits):
        if i > 0:
            time.sleep(random.uniform(4.0, 6.0))
        url = _build_rss_url(sub, rcfg.listing, rcfg.limit, rcfg.time_filter)
        log.info("scraping r/%s (%s, limit=%d) via rss", sub, rcfg.listing, rcfg.limit)
        headers = {"User-Agent": rcfg.user_agent, "Accept": "application/atom+xml"}
        try:
            for attempt in range(1, _MAX_TRIES + 1):
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    break
                if resp.status_code in _RETRY_STATUS and attempt < _MAX_TRIES:
                    delay = _BASE_BACKOFF_S * (2 ** (attempt - 1)) + random.uniform(0, 1)
                    log.warning("reddit rss %s on attempt %d/%d for %s — backing off %.1fs",
                                resp.status_code, attempt, _MAX_TRIES, url, delay)
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
            else:
                raise RuntimeError(f"rss fetch failed after {_MAX_TRIES} tries: {url}")
            root = ET.fromstring(resp.content)
        except Exception as e:
            log.error("r/%s rss fetch failed, skipping: %s", sub, e)
            continue
        entries = root.findall(f"{_ATOM_NS}entry")
        sub_stories = [s for s in (_parse_rss_entry(e, sub) for e in entries) if s is not None]
        log.info("r/%s -> %d entries (%d with selftext)", sub, len(entries), len(sub_stories))
        stories.extend(sub_stories)
    return stories


def fetch_candidates(cfg: Config) -> list[Story]:
    if cfg.reddit.mode == "praw":
        return _fetch_via_praw(cfg)
    if cfg.reddit.mode == "rss":
        return _fetch_via_rss(cfg)
    return _fetch_via_json(cfg)
