from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from core.config import Config
from core.ffmpeg import which_ffmpeg

log = logging.getLogger(__name__)


def _escape_filter_path(p: Path) -> str:
    """ffmpeg filter argument escaping: backslash, colon and single-quote
    are special inside a filtergraph. Use absolute paths so we don't depend
    on cwd."""
    s = str(p.resolve())
    return (
        s.replace("\\", "\\\\")
         .replace(":", "\\:")
         .replace("'", "\\'")
    )


def render(
    bg_clip: Path,
    voice_audio: Path,
    ass_file: Path,
    cfg: Config,
    out_path: Path,
) -> Path:
    """Single FFmpeg pass: burn ASS captions on bg_clip's video, loudnorm
    voice_audio onto the output, encode H.264 + AAC + faststart at the
    bitrates/fps from cfg.video. Output is written to `out_path`.

    Assumes bg_clip is silent (current make_clip outputs `-an`). If
    background.audio_volume > 0, future iteration should keep bg audio in
    make_clip and add an amix here.
    """
    for p in (bg_clip, voice_audio, ass_file):
        if not p.exists():
            raise FileNotFoundError(f"assemble input missing: {p}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_arg = _escape_filter_path(ass_file)

    filter_complex = (
        f"[0:v]ass='{ass_arg}'[v];"
        "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )

    cmd = [
        which_ffmpeg(), "-y",
        "-i", str(bg_clip),
        "-i", str(voice_audio),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-b:v", cfg.video.video_bitrate,
        "-pix_fmt", "yuv420p",
        "-r", str(cfg.video.fps),
        "-c:a", "aac",
        "-b:a", cfg.video.audio_bitrate,
        "-ar", "44100",
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]

    log.info("assemble: %s + %s + %s -> %s",
             bg_clip.name, voice_audio.name, ass_file.name, out_path)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
