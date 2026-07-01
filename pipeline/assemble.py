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
_CARD_Y = 330


def render(
    bg_source: Path,
    voice_audio: Path,
    ass_file: Path,
    cfg: Config,
    out_path: Path,
    card_image: Path | None = None,
    bg_start_s: float = 0.0,
    bg_duration_s: float | None = None,
) -> Path:
    """Single FFmpeg pass: seek+trim the bg source, scale/crop to render
    resolution, burn ASS captions, optionally overlay a Reddit-card image
    for the first ~5 seconds (solid then fade), loudnorm voice_audio,
    encode H.264 + AAC + faststart at the bitrates/fps from cfg.video.

    `bg_source` is the untouched YouTube-cached background. The old flow
    pre-encoded a windowed silent bg.mp4 via background.make_clip; that
    step is dropped so the video is encoded exactly once instead of twice.
    `bg_start_s` + `bg_duration_s` come from background.pick_window().
    Passing `bg_duration_s=None` keeps the input un-trimmed (assemble
    still stops at `-shortest`).
    """
    for p in (bg_source, voice_audio, ass_file):
        if not p.exists():
            raise FileNotFoundError(f"assemble input missing: {p}")
    if card_image is not None and not card_image.exists():
        raise FileNotFoundError(f"card image missing: {card_image}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_arg = _escape_filter_path(ass_file)

    w, h = cfg.video.width, cfg.video.height
    # Scale + crop chain applied inline so we don't need a pre-encoded bg clip.
    scale_crop = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1"
    )

    # -ss BEFORE -i is fast (container-level seek), -ss AFTER -i is precise
    # (frame-accurate but slow). Container seek is fine for background
    # footage — we just need to start somewhere reasonable, not a specific
    # frame. Combined with `-shortest` and the voice track duration this
    # gives us a well-defined output length.
    bg_input: list[str] = ["-ss", f"{bg_start_s:.3f}"]
    if bg_duration_s is not None:
        bg_input += ["-t", f"{bg_duration_s:.3f}"]
    bg_input += ["-i", str(bg_source)]

    if card_image is None:
        filter_complex = (
            f"[0:v]{scale_crop},ass='{ass_arg}'[v];"
            "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        )
        input_args: list[str] = [
            *bg_input,
            "-i", str(voice_audio),
        ]
    else:
        overlay_end = _CARD_SOLID_S + _CARD_FADE_S
        filter_complex = (
            f"[0:v]{scale_crop},ass='{ass_arg}'[vsub];"
            f"[2:v]format=rgba,fade=t=out:st={_CARD_SOLID_S}:d={_CARD_FADE_S}:alpha=1[card];"
            f"[vsub][card]overlay=x=(W-w)/2:y={_CARD_Y}:"
            f"enable='between(t,0,{overlay_end})':format=auto[v];"
            "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        )
        input_args = [
            *bg_input,
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
        "-preset", "veryfast",
        "-crf", "23",
        # -b:v acts as a soft upper hint alongside CRF; ffmpeg ignores it
        # for pure CRF mode but keeps compatibility with any external
        # tooling that expects the target bitrate to appear in the muxer.
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

    log.info("assemble: %s @ %.2fs +%s + %s%s -> %s",
             bg_source.name,
             bg_start_s,
             f"{bg_duration_s:.2f}s" if bg_duration_s is not None else "full",
             voice_audio.name,
             f" + {card_image.name}" if card_image else "",
             out_path)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
