from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from core.config import Config


# System HF cache (~/.cache/huggingface) is root-owned on this machine from a
# prior install. Redirect to a project-local cache before any HF import so
# we never touch /Users/sebastian/.cache.
_LOCAL_HF_CACHE = Path("data/hf_cache").resolve()
_LOCAL_HF_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_LOCAL_HF_CACHE))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_LOCAL_HF_CACHE / "hub"))

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WordTiming:
    text: str
    t_start: float
    t_end: float


def _mlx_model_path(model: str) -> str:
    # mlx-community hosts pre-converted MLX-format whisper models.
    return f"mlx-community/whisper-{model}-mlx"


def _transcribe_mlx(audio_path: Path, cfg: Config) -> list[WordTiming]:
    import mlx_whisper  # local import: heavy, optional dep

    repo = _mlx_model_path(cfg.whisper.model)
    log.info("mlx-whisper: %s (word_level=%s)", repo, cfg.whisper.word_level)

    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=repo,
        word_timestamps=cfg.whisper.word_level,
        language="en",
        verbose=False,
    )

    words: list[WordTiming] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            txt = w.get("word", "").strip()
            if not txt:
                continue
            words.append(WordTiming(
                text=txt,
                t_start=float(w["start"]),
                t_end=float(w["end"]),
            ))
    if not words:
        # Fallback: no word-level data → synthesize from segments at coarser granularity.
        log.warning("no word-level timestamps; falling back to segment-level")
        for seg in result.get("segments", []):
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            words.append(WordTiming(
                text=txt,
                t_start=float(seg["start"]),
                t_end=float(seg["end"]),
            ))
    return words


def transcribe(audio_path: Path, cfg: Config) -> list[WordTiming]:
    """Run word-level speech-to-text on `audio_path`.

    Returns a flat list of WordTiming ordered by start time. Empty list iff
    audio yielded no recognized speech (highly unlikely for our edge-tts VO,
    but kept as a graceful return for callers).
    """
    backend = cfg.whisper.backend
    if backend == "mlx":
        return _transcribe_mlx(audio_path, cfg)
    if backend == "faster":
        raise NotImplementedError("faster-whisper backend not wired yet")
    raise ValueError(f"unknown whisper backend: {backend!r}")
