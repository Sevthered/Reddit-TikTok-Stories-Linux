from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from core.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioResult:
    path: Path
    duration_s: float
    too_long: bool


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
_MIN_SENTENCE_CHARS = 2
_EDGE_RETRIES = 3
_EDGE_INTER_CALL_SLEEP_S = 1.0


def _split_sentences(text: str) -> list[str]:
    # Cheap sentence splitter; edge-tts handles intra-sentence prosody itself.
    chunks: list[str] = []
    for paragraph in text.split("\n"):
        p = paragraph.strip()
        if not p:
            continue
        parts = _SENTENCE_SPLIT_RE.split(p)
        for part in parts:
            s = part.strip()
            if len(s) >= _MIN_SENTENCE_CHARS:
                chunks.append(s)
    return chunks


async def _synth_one(text: str, voice: str, rate: str, out_path: Path) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, _EDGE_RETRIES + 1):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate)
            await comm.save(str(out_path))
            return
        except Exception as e:
            last_exc = e
            delay = 2.0 * attempt
            log.warning("edge-tts attempt %d/%d failed (%s); backing off %.1fs",
                        attempt, _EDGE_RETRIES, e, delay)
            await asyncio.sleep(delay)
    raise RuntimeError(f"edge-tts failed after {_EDGE_RETRIES} tries") from last_exc


def _ffprobe_duration_s(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_entries", "format=duration", str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    return float(json.loads(out)["format"]["duration"])


def _make_silence(out_path: Path, ms: int) -> None:
    sec = max(0.001, ms / 1000.0)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", f"{sec:.3f}", "-q:a", "9", "-acodec", "libmp3lame", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat_mp3s(parts: list[Path], silence: Path, out_path: Path) -> None:
    # Build a concat demuxer list: part0, silence, part1, silence, ..., partN.
    list_path = out_path.with_suffix(".txt")
    lines: list[str] = []
    for i, p in enumerate(parts):
        if i > 0:
            lines.append(f"file '{silence.resolve()}'")
        lines.append(f"file '{p.resolve()}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "24000",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


async def _synth_all(sentences: list[str], voice: str, rate: str, work_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for i, s in enumerate(sentences):
        out = work_dir / f"seg_{i:04d}.mp3"
        log.info("tts: synthesizing sentence %d/%d (%d chars)", i + 1, len(sentences), len(s))
        await _synth_one(s, voice, rate, out)
        paths.append(out)
        if i < len(sentences) - 1:
            await asyncio.sleep(_EDGE_INTER_CALL_SLEEP_S)
    return paths


def synthesize(text: str, cfg: Config, out_dir: Path | str) -> AudioResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "tts_parts"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()

    sentences = _split_sentences(text)
    if not sentences:
        raise ValueError("tts.synthesize: no sentences after split")

    parts = asyncio.run(_synth_all(sentences, cfg.tts.voice, cfg.tts.rate, work))

    silence = work / "silence.mp3"
    _make_silence(silence, cfg.tts.pause_between_sentences_ms)

    final_path = out_dir / "voice.mp3"
    _concat_mp3s(parts, silence, final_path)

    duration_s = _ffprobe_duration_s(final_path)
    too_long = duration_s > float(cfg.video.target_max_seconds)
    log.info("tts: %d sentences -> %s, %.2fs (limit %ds, too_long=%s)",
             len(sentences), final_path, duration_s, cfg.video.target_max_seconds, too_long)
    return AudioResult(path=final_path, duration_s=duration_s, too_long=too_long)
