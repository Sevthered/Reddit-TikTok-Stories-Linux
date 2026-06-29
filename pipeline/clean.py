from __future__ import annotations

import logging
import re

from core.config import Config
from pipeline.confusables import sanitize as sanitize_confusables
from pipeline.filter import PROFANITY
from pipeline.scrape import Story

log = logging.getLogger(__name__)


# Order matters where listed: longer keys before shorter prefixes.
_ABBREV: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bWIBTA\b", re.IGNORECASE), "Would I be the asshole"),
    (re.compile(r"\bAITA\b", re.IGNORECASE), "Am I the asshole"),
    (re.compile(r"\bTIFU\b", re.IGNORECASE), "Today I fucked up"),
    (re.compile(r"\bYTA\b", re.IGNORECASE), "You're the asshole"),
    (re.compile(r"\bNTA\b", re.IGNORECASE), "Not the asshole"),
    (re.compile(r"\bESH\b", re.IGNORECASE), "Everyone sucks here"),
    (re.compile(r"\bNAH\b"), "No assholes here"),
    (re.compile(r"\bSO\b"), "significant other"),
    (re.compile(r"\bIRL\b", re.IGNORECASE), "in real life"),
    (re.compile(r"\bDM(?:s)?\b"), "direct messages"),
    (re.compile(r"\bTL;?DR\b", re.IGNORECASE), "To summarize"),
    (re.compile(r"\bIDK\b", re.IGNORECASE), "I don't know"),
    (re.compile(r"\bIIRC\b", re.IGNORECASE), "if I remember correctly"),
    (re.compile(r"\bFWIW\b", re.IGNORECASE), "for what it's worth"),
    (re.compile(r"\bafaik\b", re.IGNORECASE), "as far as I know"),
    (re.compile(r"\bbf\b", re.IGNORECASE), "boyfriend"),
    (re.compile(r"\bgf\b", re.IGNORECASE), "girlfriend"),
]

# Reddit-style age/gender tag — REQUIRE brackets to avoid eating "about 5 minutes".
# Matches (28F), [35 M], (16f), (22M). Bare "28F" intentionally not handled.
_AGE_TAG_RE = re.compile(r"[\[(]\s*(\d{1,2})\s*([MmFf])\s*[\])]")
_GENDER_WORD = {"M": "male", "F": "female"}

# Markdown noise.
_MD_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1", re.DOTALL)
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RAW_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_ORDLIST_RE = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_CODEBLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Reddit "Edit:" / "EDIT 2:" — keep the body, drop the label so TTS doesn't say it.
_EDIT_LABEL_RE = re.compile(r"^\s*edit\s*\d*\s*:\s*", re.IGNORECASE | re.MULTILINE)

_WS_COLLAPSE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _strip_markdown(text: str) -> str:
    text = _MD_CODEBLOCK_RE.sub(" ", text)
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _RAW_URL_RE.sub("", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _MD_ORDLIST_RE.sub("", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    text = _MD_BOLD_ITALIC_RE.sub(r"\2", text)
    return text


def _expand_age_tag(m: re.Match[str]) -> str:
    age, g = m.group(1), m.group(2).upper()
    return f"{age} {_GENDER_WORD[g]}"


def _expand_abbreviations(text: str) -> str:
    for pat, repl in _ABBREV:
        text = pat.sub(repl, text)
    text = _AGE_TAG_RE.sub(_expand_age_tag, text)
    text = _EDIT_LABEL_RE.sub("", text)
    return text


# Euphemism swap for profanity in `soft` mode. Asterisk masking (the previous
# behavior) failed in practice: edge-tts read "f*****" as the letter F followed
# by silence, sounding broken on the final video. Mapping to TTS-safe synonyms
# preserves cadence and intent without tripping TikTok content filters.
_EUPHEMISMS: dict[str, str] = {
    "fuck": "freak", "fucking": "freaking", "fucked": "freaked", "fucker": "jerk",
    "shit": "crap", "shitty": "lousy", "bullshit": "nonsense",
    "bitch": "jerk", "bitches": "jerks",
    "cunt": "jerk",
    "asshole": "jerk", "assholes": "jerks",
    "ass": "behind",
    "dick": "jerk", "dicks": "jerks",
    "pussy": "wimp",
    "cock": "jerk", "cocks": "jerks",
    "whore": "creep", "whores": "creeps",
    "slut": "creep", "sluts": "creeps",
    "bastard": "jerk",
    "retard": "fool", "retarded": "foolish",
    # Slurs swapped to neutral nouns; strict mode is the correct way to reject
    # these posts entirely.
    "nigger": "person", "nigga": "person", "faggot": "person", "fag": "person",
}


# Edge-tts Guy Neural mangles a small set of words by dropping or merging
# phonemes. Force the longer / less-ambiguous form before synthesis.
_TTS_HOMOGRAPHS: dict[str, str] = {
    "butt": "buttocks",     # otherwise reads as "but" (conjunction)
    "butts": "buttocks",
}


def _preserve_case(orig: str, repl: str) -> str:
    if not orig or not repl:
        return repl
    if orig.isupper():
        return repl.upper()
    if orig[0].isupper():
        return repl[0].upper() + repl[1:]
    return repl


def _soft_replace_profanity(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        sub = _EUPHEMISMS.get(w.lower())
        return _preserve_case(w, sub) if sub else w
    return re.sub(r"\b[a-zA-Z']+\b", repl, text)


def _apply_tts_homographs(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        sub = _TTS_HOMOGRAPHS.get(w.lower())
        return _preserve_case(w, sub) if sub else w
    return re.sub(r"\b[a-zA-Z']+\b", repl, text)


def _collapse_whitespace(text: str) -> str:
    lines = [_WS_COLLAPSE_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def normalize(story: Story, cfg: Config) -> str:
    body = story.selftext
    if cfg.filter.confusable_mode != "off":
        body = sanitize_confusables(body)
    body = _strip_markdown(body)
    body = _expand_abbreviations(body)

    title_raw = story.title
    if cfg.filter.confusable_mode != "off":
        title_raw = sanitize_confusables(title_raw)
    title = _expand_abbreviations(_strip_markdown(title_raw))

    text = f"{title}.\n\n{body}"

    if cfg.filter.profanity_mode == "soft":
        text = _soft_replace_profanity(text)

    text = _apply_tts_homographs(text)

    text = _collapse_whitespace(text)
    return text
