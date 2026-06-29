from __future__ import annotations

import json
import logging
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.config import Config

log = logging.getLogger(__name__)


# yt-dlp picks best MP4 ≤1080p — keeps cache small AND matches our render target.
_YTDLP_FORMAT = (
    "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/"
    "b[ext=mp4][height<=1080]/"
    "b[height<=1080]/b"
)

# URL → id regex; we don't trust yt-dlp's full extractor just to name a file.
_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})")


@dataclass(frozen=True)
class ClipResult:
    path: Path
    source: Path
    start_s: float
    duration_s: float


def _video_id(url: str) -> str:
    m = _YT_ID_RE.search(url)
    if not m:
        raise ValueError(f"could not extract YouTube id from URL: {url!r}")
    return m.group(1)


def _which_yt_dlp() -> str:
    return shutil.which("yt-dlp") or "yt-dlp"


def _which_ffmpeg() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def _which_ffprobe() -> str:
    return shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def _ffprobe_duration_s(path: Path) -> float:
    cmd = [
        _which_ffprobe(), "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration", str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    return float(json.loads(out)["format"]["duration"])


def ensure_cached(cfg: Config) -> list[Path]:
    """Download each background.source_urls entry into background.cache_dir
    if not already present. Returns list of cached MP4 paths in URL order."""
    cache_dir = Path(cfg.background.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cached: list[Path] = []
    for url in cfg.background.source_urls:
        vid = _video_id(url)
        target = cache_dir / f"{vid}.mp4"
        if target.exists() and target.stat().st_size > 0:
            log.info("bg cache hit: %s (%s)", target.name, _human_size(target.stat().st_size))
            cached.append(target)
            continue

        log.info("downloading background %s -> %s", url, target)
        cmd = [
            _which_yt_dlp(),
            "-f", _YTDLP_FORMAT,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--retries", "5",
            "--fragment-retries", "5",
            "-o", str(cache_dir / f"{vid}.%(ext)s"),
            url,
        ]
        subprocess.run(cmd, check=True)
        if not target.exists():
            # yt-dlp may have merged into a different extension; rescue by globbing.
            matches = list(cache_dir.glob(f"{vid}.*"))
            if not matches:
                raise RuntimeError(f"yt-dlp finished but no file at {target}")
            target = matches[0]
        cached.append(target)

    return cached


def pick_random_cached(cached: list[Path], rng: random.Random | None = None) -> Path:
    if not cached:
        raise ValueError("no cached backgrounds available")
    r = rng or random.Random()
    return r.choice(cached)


def make_clip(
    bg_path: Path,
    duration_s: float,
    cfg: Config,
    out_path: Path,
    rng: random.Random | None = None,
) -> ClipResult:
    """Trim a random window of `duration_s` from `bg_path`, crop-fill to
    `video.width × video.height`, output silent MP4 at `out_path`."""
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")

    src_dur = _ffprobe_duration_s(bg_path)
    # Leave a small tail so we don't read past EOF on imprecise seeks.
    tail_pad = 1.0
    if src_dur < duration_s + tail_pad:
        raise ValueError(
            f"source {bg_path.name} too short: {src_dur:.2f}s < required {duration_s + tail_pad:.2f}s"
        )

    r = rng or random.Random()
    start_s = r.uniform(0.0, src_dur - duration_s - tail_pad)

    w, h, fps, vbr = cfg.video.width, cfg.video.height, cfg.video.fps, cfg.video.video_bitrate
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _which_ffmpeg(), "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(bg_path),
        "-t", f"{duration_s:.3f}",
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264",
        "-b:v", vbr,
        "-pix_fmt", "yuv420p",
        "-an",  # silent: VO drives audio in assemble.py.
        "-movflags", "+faststart",
        str(out_path),
    ]
    log.info("clip: %s @ %.2fs +%.2fs -> %s", bg_path.name, start_s, duration_s, out_path)
    subprocess.run(cmd, check=True, capture_output=True)

    return ClipResult(path=out_path, source=bg_path, start_s=start_s, duration_s=duration_s)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
