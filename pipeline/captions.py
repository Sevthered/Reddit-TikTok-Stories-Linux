from __future__ import annotations

import logging
import re
from pathlib import Path

from core.config import Config
from pipeline.transcribe import WordTiming

log = logging.getLogger(__name__)


# Strip leading punctuation that whisper attaches to words ("," "." etc.)
# We keep trailing punctuation because it carries spoken cadence.
_LEADING_PUNCT = re.compile(r"^[^\w]+", re.UNICODE)


def _apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    return text


def _ass_time(t: float) -> str:
    """ASS format: H:MM:SS.cc (centiseconds, two digits)."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs >= 100:  # rounding overflow
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _inline_color(ass_color: str) -> str:
    """Convert an `&H00BBGGRR` style color (with optional alpha) into the
    inline-override form `&HBBGGRR&` used inside Dialogue overrides."""
    c = ass_color.strip()
    if c.startswith("&H"):
        c = c[2:]
    if c.endswith("&"):
        c = c[:-1]
    if len(c) == 8:  # AABBGGRR -> drop AA
        c = c[2:]
    return f"&H{c.upper()}&"


def _group_words(words: list[WordTiming], per_cue: int) -> list[list[WordTiming]]:
    if per_cue < 1:
        raise ValueError("words_per_cue must be >= 1")
    return [words[i:i + per_cue] for i in range(0, len(words), per_cue)]


def _clean_word(text: str) -> str:
    return _LEADING_PUNCT.sub("", text).strip()


def _style_block(cfg: Config) -> str:
    """ASS [V4+ Styles] block. Alignment=2 (bottom-center) + MarginV controls
    vertical position. Bold=-1 enables bold; ScaleX/Y = 100; outline +
    drop shadow on."""
    cap = cfg.captions
    # Format reference (V4+ Style):
    # Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,
    # BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing,
    # Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR,
    # MarginV, Encoding
    shadow = 2
    bord = cap.outline
    fields = [
        "Default", cap.font, str(cap.font_size),
        cap.primary_color,     # Primary  (visible fill)
        cap.primary_color,     # Secondary (unused — we drive highlight via inline override)
        "&H00000000",          # Outline color: black
        "&H00000000",          # Back / shadow color: black
        "-1", "0", "0", "0",   # Bold, Italic, Underline, StrikeOut
        "100", "100", "0", "0",
        "1",                   # BorderStyle 1 = outline + drop shadow
        str(bord), str(shadow),
        "2",                   # Alignment 2 = bottom-center; MarginV pushes up
        "60", "60", str(cap.margin_v),
        "1",
    ]
    return (
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: " + ",".join(fields) + "\n"
    )


def _script_info(cfg: Config) -> str:
    return (
        "[Script Info]\n"
        "Title: AutomatedTikTokBot captions\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {cfg.video.width}\n"
        f"PlayResY: {cfg.video.height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n"
    )


def _events_header() -> str:
    return (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )


def _build_dialogue_lines(
    cue: list[WordTiming],
    cfg: Config,
    highlight_inline: str,
) -> list[str]:
    """For each word in the cue, emit one Dialogue line covering that word's
    timing. The line shows the full cue text but with only the active word
    wrapped in a color override. Result: as VO speaks, the active word pulses
    in the highlight color while neighbors stay white."""
    words_cased = [_apply_case(_clean_word(w.text), cfg.captions.case) for w in cue]
    lines: list[str] = []
    for i, w in enumerate(cue):
        parts: list[str] = []
        for j, text in enumerate(words_cased):
            if not text:
                continue
            if j == i and cfg.captions.highlight_mode == "color":
                parts.append(f"{{\\c{highlight_inline}}}{text}{{\\c}}")
            else:
                parts.append(text)
        cue_text = " ".join(parts)
        lines.append(
            f"Dialogue: 0,{_ass_time(w.t_start)},{_ass_time(w.t_end)},Default,,0,0,0,,{cue_text}\n"
        )
    return lines


def build_ass(words: list[WordTiming], cfg: Config, out_path: Path) -> Path:
    """Render an ASS subtitle file at `out_path` for the supplied word timings.
    Words are grouped into cues of `captions.words_per_cue`. Each cue emits
    one Dialogue line per word (full cue text with active word highlighted)."""
    if not words:
        log.warning("build_ass: no words provided; writing empty ASS")
    if cfg.captions.highlight_mode != "color":
        log.warning("highlight_mode=%r not implemented; falling back to color",
                    cfg.captions.highlight_mode)

    highlight_inline = _inline_color(cfg.captions.highlight)
    cues = _group_words(words, cfg.captions.words_per_cue)

    body_parts: list[str] = [_script_info(cfg), _style_block(cfg), "\n", _events_header()]
    for cue in cues:
        body_parts.extend(_build_dialogue_lines(cue, cfg, highlight_inline))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(body_parts), encoding="utf-8")
    log.info("captions: %d cues / %d words -> %s", len(cues), len(words), out_path)
    return out_path
