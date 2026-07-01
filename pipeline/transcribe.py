from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from core.config import Config


# Project-local HF cache so we never touch a system-owned ~/.cache/huggingface.
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
    return f"mlx-community/whisper-{model}-mlx"


def _transcribe_mlx(audio_path: Path, cfg: Config) -> list[WordTiming]:
    import mlx_whisper  # heavy Apple-Silicon-only dep, imported lazily

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
            words.append(WordTiming(text=txt, t_start=float(w["start"]), t_end=float(w["end"])))
    if not words:
        log.warning("no word-level timestamps; falling back to segment-level")
        for seg in result.get("segments", []):
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            words.append(WordTiming(text=txt, t_start=float(seg["start"]), t_end=float(seg["end"])))
    return words


_FASTER_MODEL = None  # module-level singleton — loading `small` int8 takes ~2s and 400MB RAM


def _get_faster_model(model_name: str):
    global _FASTER_MODEL
    if _FASTER_MODEL is not None:
        return _FASTER_MODEL
    from faster_whisper import WhisperModel

    threads = int(os.environ.get("FASTER_WHISPER_THREADS", "4"))
    log.info("faster-whisper: loading %s (int8, cpu, threads=%d)", model_name, threads)
    _FASTER_MODEL = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
    )
    return _FASTER_MODEL


def _transcribe_faster(audio_path: Path, cfg: Config) -> list[WordTiming]:
    model = _get_faster_model(cfg.whisper.model)
    log.info("faster-whisper: transcribing %s (word_level=%s)", audio_path, cfg.whisper.word_level)

    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        word_timestamps=cfg.whisper.word_level,
        beam_size=1,
        vad_filter=True,
    )

    words: list[WordTiming] = []
    for seg in segments:  # generator: nothing runs until iterated
        if cfg.whisper.word_level and seg.words:
            for w in seg.words:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                words.append(WordTiming(text=txt, t_start=float(w.start), t_end=float(w.end)))
        else:
            txt = (seg.text or "").strip()
            if not txt:
                continue
            words.append(WordTiming(text=txt, t_start=float(seg.start), t_end=float(seg.end)))

    if cfg.whisper.word_level and not words:
        log.warning("faster-whisper: no word-level output, callers may see empty transcript")
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
        return _transcribe_faster(audio_path, cfg)
    raise ValueError(f"unknown whisper backend: {backend!r}")
