from __future__ import annotations

import os
import shutil
from pathlib import Path

# Resolution order:
#   1. FFMPEG_BIN / FFPROBE_BIN env override (systemd units, per-run testing).
#   2. Homebrew keg-only ffmpeg-full (macOS dev machines).
#   3. PATH lookup (Linux servers via apt-installed ffmpeg).
_HOMEBREW_FFMPEG = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
_HOMEBREW_FFPROBE = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")


def which_ffmpeg() -> str:
    override = os.environ.get("FFMPEG_BIN")
    if override:
        return override
    if _HOMEBREW_FFMPEG.exists():
        return str(_HOMEBREW_FFMPEG)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError("ffmpeg not found: set FFMPEG_BIN or install ffmpeg on PATH")


def which_ffprobe() -> str:
    override = os.environ.get("FFPROBE_BIN")
    if override:
        return override
    if _HOMEBREW_FFPROBE.exists():
        return str(_HOMEBREW_FFPROBE)
    found = shutil.which("ffprobe")
    if found:
        return found
    raise RuntimeError("ffprobe not found: set FFPROBE_BIN or install ffmpeg on PATH")
