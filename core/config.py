from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RedditCfg:
    mode: str
    subreddits: list[str]
    listing: str
    time_filter: str
    limit: int
    user_agent: str
    client_id: str | None
    client_secret: str | None


@dataclass(frozen=True)
class FilterCfg:
    min_words: int
    max_words: int
    min_score: int
    allow_nsfw: bool
    profanity_mode: str
    confusable_mode: str


@dataclass(frozen=True)
class TtsCfg:
    engine: str
    voice: str
    rate: str
    pause_between_sentences_ms: int


@dataclass(frozen=True)
class WhisperCfg:
    backend: str
    model: str
    word_level: bool


@dataclass(frozen=True)
class CaptionsCfg:
    font: str
    font_size: int
    primary_color: str
    highlight: str
    outline: int
    words_per_cue: int
    margin_v: int


@dataclass(frozen=True)
class BackgroundCfg:
    source_urls: list[str]
    cache_dir: str
    audio_volume: float


@dataclass(frozen=True)
class VideoCfg:
    width: int
    height: int
    fps: int
    video_bitrate: str
    audio_bitrate: str
    target_max_seconds: int


@dataclass(frozen=True)
class UploadCfg:
    platform: str
    review_gate: bool
    cookies_file: str
    caption_template: str
    schedule_minutes_apart: int


@dataclass(frozen=True)
class RunCfg:
    videos_per_run: int


@dataclass(frozen=True)
class Config:
    reddit: RedditCfg
    filter: FilterCfg
    tts: TtsCfg
    whisper: WhisperCfg
    captions: CaptionsCfg
    background: BackgroundCfg
    video: VideoCfg
    upload: UploadCfg
    run: RunCfg


_REDDIT_MODES = {"json", "praw", "rss"}
_LISTINGS = {"top", "hot", "new"}
_TIME_FILTERS = {"hour", "day", "week", "month", "year", "all"}
_PROFANITY_MODES = {"off", "soft", "strict"}
_CONFUSABLE_MODES = {"off", "sanitize", "strict"}
_TTS_ENGINES = {"edge", "kokoro"}
_WHISPER_BACKENDS = {"mlx", "faster"}


def _section(raw: dict, name: str) -> dict:
    if name not in raw:
        raise ConfigError(f"missing [{name}] section in config.toml")
    return raw[name]


def _require(d: dict, key: str, section: str):
    if key not in d:
        raise ConfigError(f"missing key '{key}' in [{section}]")
    return d[key]


def _load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def load_config(path: str | Path = "config.toml") -> Config:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with p.open("rb") as f:
        raw = tomllib.load(f)

    _load_dotenv()

    r = _section(raw, "reddit")
    user_agent = _require(r, "user_agent", "reddit")
    if "CHANGE_ME" in user_agent:
        raise ConfigError(
            "reddit.user_agent still contains 'CHANGE_ME' — set a real handle "
            "(e.g. 'Reddit-Story-Bot/1.0 (by /u/yourname)'). Reddit 403s generic UAs."
        )
    mode = r.get("mode", "json")
    if mode not in _REDDIT_MODES:
        raise ConfigError(f"reddit.mode must be one of {_REDDIT_MODES}, got {mode!r}")
    listing = _require(r, "listing", "reddit")
    if listing not in _LISTINGS:
        raise ConfigError(f"reddit.listing must be one of {_LISTINGS}, got {listing!r}")
    time_filter = _require(r, "time_filter", "reddit")
    if time_filter not in _TIME_FILTERS:
        raise ConfigError(f"reddit.time_filter must be one of {_TIME_FILTERS}, got {time_filter!r}")
    subs = _require(r, "subreddits", "reddit")
    if not isinstance(subs, list) or not subs:
        raise ConfigError("reddit.subreddits must be a non-empty list")
    limit = int(_require(r, "limit", "reddit"))
    if limit <= 0:
        raise ConfigError("reddit.limit must be > 0")

    client_id = os.environ.get("REDDIT_CLIENT_ID") or None
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET") or None
    if mode == "praw" and not (client_id and client_secret):
        raise ConfigError(
            "reddit.mode='praw' requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env. "
            "Create a 'script' app at https://www.reddit.com/prefs/apps."
        )
    reddit = RedditCfg(mode, subs, listing, time_filter, limit, user_agent, client_id, client_secret)

    f_ = _section(raw, "filter")
    profanity_mode = _require(f_, "profanity_mode", "filter")
    if profanity_mode not in _PROFANITY_MODES:
        raise ConfigError(f"filter.profanity_mode must be one of {_PROFANITY_MODES}")
    confusable_mode = f_.get("confusable_mode", "sanitize")
    if confusable_mode not in _CONFUSABLE_MODES:
        raise ConfigError(f"filter.confusable_mode must be one of {_CONFUSABLE_MODES}")
    flt = FilterCfg(
        min_words=int(_require(f_, "min_words", "filter")),
        max_words=int(_require(f_, "max_words", "filter")),
        min_score=int(_require(f_, "min_score", "filter")),
        allow_nsfw=bool(_require(f_, "allow_nsfw", "filter")),
        profanity_mode=profanity_mode,
        confusable_mode=confusable_mode,
    )
    if flt.min_words < 1 or flt.max_words < flt.min_words:
        raise ConfigError("filter: require 1 <= min_words <= max_words")

    t = _section(raw, "tts")
    engine = _require(t, "engine", "tts")
    if engine not in _TTS_ENGINES:
        raise ConfigError(f"tts.engine must be one of {_TTS_ENGINES}")
    tts = TtsCfg(
        engine=engine,
        voice=_require(t, "voice", "tts"),
        rate=_require(t, "rate", "tts"),
        pause_between_sentences_ms=int(_require(t, "pause_between_sentences_ms", "tts")),
    )

    w = _section(raw, "whisper")
    backend = _require(w, "backend", "whisper")
    if backend not in _WHISPER_BACKENDS:
        raise ConfigError(f"whisper.backend must be one of {_WHISPER_BACKENDS}")
    whisper = WhisperCfg(
        backend=backend,
        model=_require(w, "model", "whisper"),
        word_level=bool(_require(w, "word_level", "whisper")),
    )

    c = _section(raw, "captions")
    captions = CaptionsCfg(
        font=_require(c, "font", "captions"),
        font_size=int(_require(c, "font_size", "captions")),
        primary_color=_require(c, "primary_color", "captions"),
        highlight=_require(c, "highlight", "captions"),
        outline=int(_require(c, "outline", "captions")),
        words_per_cue=int(_require(c, "words_per_cue", "captions")),
        margin_v=int(_require(c, "margin_v", "captions")),
    )

    b = _section(raw, "background")
    bg = BackgroundCfg(
        source_urls=list(_require(b, "source_urls", "background")),
        cache_dir=_require(b, "cache_dir", "background"),
        audio_volume=float(_require(b, "audio_volume", "background")),
    )
    if not (0.0 <= bg.audio_volume <= 1.0):
        raise ConfigError("background.audio_volume must be in [0.0, 1.0]")

    v = _section(raw, "video")
    video = VideoCfg(
        width=int(_require(v, "width", "video")),
        height=int(_require(v, "height", "video")),
        fps=int(_require(v, "fps", "video")),
        video_bitrate=_require(v, "video_bitrate", "video"),
        audio_bitrate=_require(v, "audio_bitrate", "video"),
        target_max_seconds=int(_require(v, "target_max_seconds", "video")),
    )

    u = _section(raw, "upload")
    upload = UploadCfg(
        platform=_require(u, "platform", "upload"),
        review_gate=bool(_require(u, "review_gate", "upload")),
        cookies_file=_require(u, "cookies_file", "upload"),
        caption_template=_require(u, "caption_template", "upload"),
        schedule_minutes_apart=int(_require(u, "schedule_minutes_apart", "upload")),
    )

    rn = _section(raw, "run")
    run = RunCfg(videos_per_run=int(_require(rn, "videos_per_run", "run")))
    if run.videos_per_run < 1:
        raise ConfigError("run.videos_per_run must be >= 1")

    return Config(reddit, flt, tts, whisper, captions, bg, video, upload, run)
