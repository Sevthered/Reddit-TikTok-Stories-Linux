from __future__ import annotations

import shutil
from pathlib import Path

# ffmpeg-full is keg-only on Homebrew and ships with libass + many extra
# codecs the regular brew ffmpeg doesn't include. Prefer it when installed.
_FFMPEG_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
_FFPROBE_FULL_BIN = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")


def which_ffmpeg() -> str:
    if _FFMPEG_FULL_BIN.exists():
        return str(_FFMPEG_FULL_BIN)
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def which_ffprobe() -> str:
    if _FFPROBE_FULL_BIN.exists():
        return str(_FFPROBE_FULL_BIN)
    return shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
