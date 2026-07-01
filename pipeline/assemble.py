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


# Card overlay window (seconds).
_CARD_SOLID_S = 4.0    # fully opaque
_CARD_FADE_S = 1.0     # linear fade to 0
# Card vertical position on the 1920-tall canvas.
# Card is ~500px tall; captions render around y=880-1080 (margin_v=860 from
# bottom). y=330 puts card bottom edge near y=830, above caption band.
_CARD_Y = 330


def render(
    bg_clip: Path,
    voice_audio: Path,
    ass_file: Path,
    cfg: Config,
    out_path: Path,
    card_image: Path | None = None,
) -> Path:
    """Single FFmpeg pass: burn ASS captions on bg_clip's video, optionally
    overlay a Reddit-card image for the first ~5 seconds (solid then fade),
    loudnorm voice_audio onto the output, encode H.264 + AAC + faststart at
    the bitrates/fps from cfg.video. Output is written to `out_path`.

    Assumes bg_clip is silent (current make_clip outputs `-an`). If
    background.audio_volume > 0, future iteration should keep bg audio in
    make_clip and add an amix here.
    """
    for p in (bg_clip, voice_audio, ass_file):
        if not p.exists():
            raise FileNotFoundError(f"assemble input missing: {p}")
    if card_image is not None and not card_image.exists():
        raise FileNotFoundError(f"card image missing: {card_image}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_arg = _escape_filter_path(ass_file)

    if card_image is None:
        filter_complex = (
            f"[0:v]ass='{ass_arg}'[v];"
            "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        )
        input_args: list[str] = [
            "-i", str(bg_clip),
            "-i", str(voice_audio),
        ]
    else:
        overlay_end = _CARD_SOLID_S + _CARD_FADE_S
        # Feed the card as a looped image with duration = overlay_end + tiny
        # slack. Format to rgba so fade w/ alpha works. Fade alpha from 1.0
        # to 0.0 over _CARD_FADE_S starting at _CARD_SOLID_S.
        filter_complex = (
            f"[0:v]ass='{ass_arg}'[vsub];"
            f"[2:v]format=rgba,fade=t=out:st={_CARD_SOLID_S}:d={_CARD_FADE_S}:alpha=1[card];"
            f"[vsub][card]overlay=x=(W-w)/2:y={_CARD_Y}:"
            f"enable='between(t,0,{overlay_end})':format=auto[v];"
            "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        )
        input_args = [
            "-i", str(bg_clip),
            "-i", str(voice_audio),
            "-loop", "1", "-t", f"{overlay_end + 0.1:.2f}", "-i", str(card_image),
        ]

    cmd = [
        which_ffmpeg(), "-y",
        *input_args,
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

    log.info("assemble: %s + %s + %s%s -> %s",
             bg_clip.name, voice_audio.name, ass_file.name,
             f" + {card_image.name}" if card_image else "",
             out_path)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
