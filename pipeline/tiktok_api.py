"""Minimal TikTok Login Kit + Display API client.

Handles refresh_token -> access_token exchange (with a small on-disk
cache so back-to-back invocations don't burn the refresh) and exposes
`video_list()` for the confirm-live worker.

Reads TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REFRESH_TOKEN
from the environment. Writes the current access_token +
expires_at + refresh_token back to `data/tiktok_tokens.json` so a rolled
refresh_token isn't lost on next call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pipeline.tiktok_api")

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
DEFAULT_FIELDS = "id,title,video_description,share_url,cover_image_url,create_time"

_TOKEN_CACHE = Path("data/tiktok_tokens.json")
_ACCESS_TTL_SAFETY_MARGIN_S = 300  # refresh 5 min before expiry

# Sandbox video URLs come back with a numeric suffix on the handle
# (e.g. `@realredditstories8464`) and utm_* tracking params. Rewrite both
# so the URL stored in the DB / sent to Telegram is the canonical
# public one. TIKTOK_HANDLE holds the real handle, cased however TikTok
# renders it (e.g. `RealRedditStories`).
_HANDLE_URL_RE = re.compile(r"^(https://www\.tiktok\.com/)@[^/]+(/.*)$")


@dataclass
class Video:
    id: str
    title: str
    share_url: str
    cover_image_url: str
    create_time: int   # unix seconds
    description: str


class TikTokApiError(RuntimeError):
    pass


def _post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode()
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise TikTokApiError(f"POST {url} failed: {e}") from e


def _post_json(url: str, payload: dict, access_token: str) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise TikTokApiError(f"POST {url} failed: {e}") from e


def _load_cache() -> dict:
    if not _TOKEN_CACHE.exists():
        return {}
    try:
        return json.loads(_TOKEN_CACHE.read_text())
    except json.JSONDecodeError:
        return {}


def _save_cache(cache: dict) -> None:
    _TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOKEN_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    tmp.replace(_TOKEN_CACHE)
    try:
        _TOKEN_CACHE.chmod(0o600)
    except OSError:
        pass


def get_access_token() -> str:
    """Return a fresh access_token, refreshing if needed.

    Precedence:
      1. Cached access_token from data/tiktok_tokens.json if not expired.
      2. Refresh via refresh_token from cache OR TIKTOK_REFRESH_TOKEN env.
    """
    cache = _load_cache()
    now = int(time.time())

    at = cache.get("access_token")
    exp = cache.get("access_expires_at", 0)
    if at and now < exp - _ACCESS_TTL_SAFETY_MARGIN_S:
        return at

    client_key = os.environ.get("TIKTOK_CLIENT_KEY")
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET")
    refresh_token = cache.get("refresh_token") or os.environ.get("TIKTOK_REFRESH_TOKEN")
    if not (client_key and client_secret and refresh_token):
        raise TikTokApiError(
            "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REFRESH_TOKEN must be set"
        )

    log.info("tiktok_api: refreshing access token")
    resp = _post_form(TOKEN_URL, {
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    if "access_token" not in resp:
        raise TikTokApiError(f"token refresh failed: {resp}")

    new_at = resp["access_token"]
    new_rt = resp.get("refresh_token", refresh_token)
    ttl = int(resp.get("expires_in", 3600))
    _save_cache({
        "access_token": new_at,
        "refresh_token": new_rt,
        "access_expires_at": now + ttl,
        "open_id": resp.get("open_id", cache.get("open_id", "")),
        "refresh_saved_at": now,
    })
    return new_at


def _canonical_share_url(url: str) -> str:
    """Drop utm_* + tracking params and rewrite the sandbox handle prefix
    to the canonical `TIKTOK_HANDLE` if set. Leaves other URLs untouched."""
    if not url:
        return url
    parsed = urllib.parse.urlsplit(url)
    stripped = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    handle = (os.environ.get("TIKTOK_HANDLE") or "").strip().lstrip("@")
    if handle:
        m = _HANDLE_URL_RE.match(stripped)
        if m:
            stripped = f"{m.group(1)}@{handle}{m.group(2)}"
    return stripped


def video_list(max_count: int = 20, cursor: int | None = None,
               fields: str = DEFAULT_FIELDS) -> tuple[list[Video], int | None, bool]:
    """Fetch the authenticated user's videos, newest first.

    Returns (videos, next_cursor, has_more). `cursor` is a unix-ms
    timestamp per the Display API spec; pass None on the first page.
    """
    at = get_access_token()
    payload: dict = {"max_count": max_count}
    if cursor is not None:
        payload["cursor"] = cursor

    url = f"{VIDEO_LIST_URL}?fields={urllib.parse.quote(fields)}"
    resp = _post_json(url, payload, at)
    # Defend the confirm-live worker against a malformed API response: a
    # non-dict body, non-dict `error`, or non-numeric `create_time` must not
    # crash the loop — surface it as TikTokApiError / skip the bad row.
    if not isinstance(resp, dict):
        raise TikTokApiError(f"video.list: unexpected response type {type(resp).__name__}")
    err = resp.get("error")
    if isinstance(err, dict) and err.get("code") not in ("", "ok", None):
        raise TikTokApiError(f"video.list failed: {err}")

    data = resp.get("data") or {}
    videos: list[Video] = []
    for v in (data.get("videos") or []):
        if not isinstance(v, dict):
            continue
        try:
            create_time = int(v.get("create_time", 0) or 0)
        except (TypeError, ValueError):
            create_time = 0
        videos.append(Video(
            id=str(v.get("id", "")),
            title=v.get("title", "") or "",
            share_url=_canonical_share_url(v.get("share_url", "") or ""),
            cover_image_url=v.get("cover_image_url", "") or "",
            create_time=create_time,
            description=v.get("video_description", "") or "",
        ))
    return videos, data.get("cursor"), bool(data.get("has_more", False))
