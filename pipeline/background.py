from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.config import Config
from core.ffmpeg import which_ffmpeg, which_ffprobe

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


def _yt_dlp_cmd() -> list[str]:
    """Invoke yt-dlp via the current interpreter so we don't depend on PATH
    containing ./venv/bin (which isn't activated when running via
    `./venv/bin/python main.py`)."""
    return [sys.executable, "-m", "yt_dlp"]


def _ffprobe_duration_s(path: Path) -> float:
    cmd = [
        which_ffprobe(), "-v", "error", "-print_format", "json",
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
        cmd = _yt_dlp_cmd() + [
            "-f", _YTDLP_FORMAT,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--retries", "5",
            "--fragment-retries", "5",
            "-o", str(cache_dir / f"{vid}.%(ext)s"),
            url,
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            log.warning("yt-dlp failed for %s (id=%s): %s — skipping", url, vid, e)
            # Leave .part files for resume on next run.
            continue

        if not target.exists():
            matches = list(cache_dir.glob(f"{vid}.mp4"))
            if not matches:
                log.warning("yt-dlp finished but no mp4 at %s — skipping", target)
                continue
            target = matches[0]
        cached.append(target)

    if not cached:
        raise RuntimeError(
            "ensure_cached: no backgrounds downloaded successfully. "
            "Check yt-dlp / network / source URLs."
        )
    return cached


def pick_random_cached(cached: list[Path], rng: random.Random | None = None) -> Path:
    if not cached:
        raise ValueError("no cached backgrounds available")
    r = rng or random.Random()
    return r.choice(cached)


def pick_window(
    bg_path: Path,
    duration_s: float,
    cfg: Config,  # noqa: ARG001 — kept for signature symmetry with legacy make_clip
    rng: random.Random | None = None,
) -> ClipResult:
    """Pick a random `duration_s` window from `bg_path` without encoding.

    The final assemble pass consumes the bg source directly via `-ss/-t`
    on its own input + scale/crop inside filter_complex, so we no longer
    pre-encode a bg.mp4. This eliminates ~4 min of libx264 work per
    render on the OptiPlex 7040. Returns a ClipResult whose `.path` is
    the untouched source file — callers that expect an encoded clip
    should switch to the new `render()` signature that accepts
    (bg_source, start_s, duration_s).
    """
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")

    src_dur = _ffprobe_duration_s(bg_path)
    tail_pad = 1.0
    if src_dur < duration_s + tail_pad:
        raise ValueError(
            f"source {bg_path.name} too short: {src_dur:.2f}s < required {duration_s + tail_pad:.2f}s"
        )

    r = rng or random.Random()
    start_s = r.uniform(0.0, src_dur - duration_s - tail_pad)
    log.info("clip window: %s @ %.2fs +%.2fs (no pre-encode)", bg_path.name, start_s, duration_s)
    return ClipResult(path=bg_path, source=bg_path, start_s=start_s, duration_s=duration_s)


def make_clip(  # noqa: D401 — retained for callers that pre-encode a bg mp4
    bg_path: Path,
    duration_s: float,
    cfg: Config,
    out_path: Path,
    rng: random.Random | None = None,
) -> ClipResult:
    """Legacy: encode a silent scaled/cropped window to `out_path`.

    Superseded by `pick_window()` + the fused assemble pass. Kept for
    any external caller / debug workflow that still wants a standalone
    silent bg clip on disk. Not used by main.py anymore.
    """
    if duration_s <= 0:
        raise ValueError(f"duration_s must be > 0, got {duration_s}")

    src_dur = _ffprobe_duration_s(bg_path)
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
        which_ffmpeg(), "-y",
        "-ss", f"{start_s:.3f}",
        "-i", str(bg_path),
        "-t", f"{duration_s:.3f}",
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-b:v", vbr,
        "-pix_fmt", "yuv420p",
        "-an",
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
