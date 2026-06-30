from __future__ import annotations

import logging
import re

from core.config import Config
from pipeline.confusables import sanitize as sanitize_confusables
from pipeline.scrape import Story

log = logging.getLogger(__name__)


# Subordinating conjunctions / prepositions that already give the TIFU
# expansion a natural pause when they follow it (no comma needed).
# "TIFU by turning..." -> "Today I fucked up by turning..." reads fine.
# Anything NOT in this list (a verb stem, an interjection, etc.) gets a
# comma inserted so we don't end up with run-on hooks like
# "Today I fucked up Called the fire department".
_TIFU_NATURAL_FOLLOWERS = (
    "by", "while", "when", "after", "before", "because", "with",
    "from", "for", "since", "until", "during", "in", "on", "at",
    "as", "if",
)
_TIFU_PREP_FOLLOW = re.compile(
    r"\bTIFU(?=\s+(?:" + "|".join(_TIFU_NATURAL_FOLLOWERS) + r")\b)",
    re.IGNORECASE,
)
_TIFU_MID = re.compile(r"\bTIFU(?=\s+\S)", re.IGNORECASE)
_TIFU_END = re.compile(r"\bTIFU\b", re.IGNORECASE)


def _expand_tifu(text: str) -> str:
    """Expand TIFU with a comma when the follower would otherwise read as a
    run-on (verb-style continuation); keep it bare when followed by a natural
    connector like "by", "while", "when". The plain `_ABBREV` substitution
    table can't disambiguate the two — see [issue #7]."""
    text = _TIFU_PREP_FOLLOW.sub("Today I fucked up", text)
    text = _TIFU_MID.sub("Today I fucked up,", text)
    text = _TIFU_END.sub("Today I fucked up", text)
    return text


# Order matters where listed: longer keys before shorter prefixes.
# NOTE: TIFU is intentionally absent here; _expand_tifu handles it.
_ABBREV: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bWIBTA\b", re.IGNORECASE), "Would I be the asshole"),
    (re.compile(r"\bAITA\b", re.IGNORECASE), "Am I the asshole"),
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
    (re.compile(r"\bbtw\b", re.IGNORECASE), "by the way"),
    # IMHO must precede IMO — longer-prefix-first rule (IMHO contains IMO at start).
    (re.compile(r"\bimho\b", re.IGNORECASE), "in my honest opinion"),
    (re.compile(r"\bimo\b", re.IGNORECASE), "in my opinion"),
    (re.compile(r"\btbh\b", re.IGNORECASE), "to be honest"),
    (re.compile(r"\bnvm\b", re.IGNORECASE), "never mind"),
    (re.compile(r"\bidc\b", re.IGNORECASE), "I don't care"),
    (re.compile(r"\biykyk\b", re.IGNORECASE), "if you know you know"),
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

# Tail markers that fall after the story's punchline: UPDATE blocks, FINAL EDIT
# blocks, TL;DR summaries (with any of the colon/semicolon/space delimiters
# we've actually seen on r/tifu). Everything from the marker onward gets
# dropped — it's almost always anti-climax, "thank you to everyone who
# commented" etc., which inflates duration and breaks the narrative arc.
# Optional surrounding quote / markdown emphasis around the marker word.
# Real posts wrap their summary lines as `"TL;DR"`, `*UPDATE:*`, `**TL;DR**`,
# `_Edit Final_`, etc. The anchor must still be a line start, but anything
# inside this character class is allowed between the line start and the
# marker, and again between the marker and its trailing colon.
_TAIL_TRUNCATE_RE = re.compile(
    r"^\s*[\"'*_]*\s*(?:"
    r"update\s*\d*"           # UPDATE, UPDATE 2, UPDATES
    r"|final\s+edit\s*\d*"    # FINAL EDIT
    r"|edit\s+final"          # EDIT FINAL
    r"|tl[;:\s]*dr"           # TL;DR, TL:DR, TL DR, TLDR, TL:DR: (any extra punct trails)
    r")[\"'*_]*\s*[:.\-]?\s*",
    re.IGNORECASE | re.MULTILINE,
)

# Smart curly quotes / apostrophes passed through from Reddit's editor.
# edge-tts handles them, but downstream regex (apostrophe restore, possessive
# restore, profanity match) is ASCII-only, so normalize early.
_SMART_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
})

# Tiny typo whitelist — keys must be confidently wrong (no real-word collision).
_COMMON_TYPOS: dict[str, str] = {
    "snd": "and",
}
_TYPO_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _COMMON_TYPOS) + r")\b",
    re.IGNORECASE,
)

# "etc;" / "etc:" → "etc." — edge-tts reads the semicolon as a long pause and
# the colon as a clause break; both sound wrong inside a parenthetical list.
_ETC_PUNCT_RE = re.compile(r"\betc[;:]", re.IGNORECASE)

# Bare small ints in titles ("4 years old") read flatter than spelled-out
# forms ("four years old"). Body prose tolerates digits; titles are the hook
# so we want the spoken version.
_TITLE_INT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten",
}
_SMALL_INT_RE = re.compile(r"\b(\d{1,2})\b")

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
    return f"{age} year old {_GENDER_WORD[g]}"


def _normalize_smart_punct(text: str) -> str:
    return text.translate(_SMART_PUNCT)


def _fix_common_typos(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        w = m.group(1)
        sub = _COMMON_TYPOS.get(w.lower())
        return _preserve_case(w, sub) if sub else w
    return _TYPO_RE.sub(repl, text)


def _fix_etc_punct(text: str) -> str:
    return _ETC_PUNCT_RE.sub(lambda m: m.group(0)[:-1] + ".", text)


def _spell_small_ints(text: str) -> str:
    """Spell out small ints (0-10) so TTS reads `four` not `four` with the
    flatter digit prosody. Restricted to title use — body prose tolerates
    digits and rewriting "I was 30" would be wrong."""
    return _SMALL_INT_RE.sub(
        lambda m: _TITLE_INT_WORDS.get(m.group(1), m.group(1)),
        text,
    )


def _expand_abbreviations(text: str) -> str:
    text = _expand_tifu(text)
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


# Reddit posters routinely drop apostrophes ("dont", "ive", "im"), and edge-tts
# then mispronounces these as separate words ("ive" rhyming with "five",
# "im" as a syllable). Restore the apostrophe before any other lookup runs.
# Skipped intentionally: "its" (ambiguous with possessive) and "id" (often the
# noun "ID").
_RESTORE_APOSTROPHE: dict[str, str] = {
    "dont": "don't", "didnt": "didn't", "wasnt": "wasn't", "isnt": "isn't",
    "arent": "aren't", "werent": "weren't", "wont": "won't",
    "shouldnt": "shouldn't", "couldnt": "couldn't", "wouldnt": "wouldn't",
    "hasnt": "hasn't", "havent": "haven't", "hadnt": "hadn't",
    "doesnt": "doesn't",
    "im": "I'm", "ive": "I've", "ill": "I'll",
    "youre": "you're", "youve": "you've", "youll": "you'll", "youd": "you'd",
    "hes": "he's", "shes": "she's",
    "theyre": "they're", "theyve": "they've", "theyll": "they'll", "theyd": "they'd",
    "thats": "that's", "whats": "what's", "wheres": "where's",
    "whos": "who's", "hows": "how's",
    "couldve": "could've", "wouldve": "would've", "shouldve": "should've",
}


# Possessives on kinship/relational nouns drop apostrophes in Reddit prose
# ("my parents house", "best friends sister"). edge-tts then reads the plural
# instead of the possessive, which changes the meaning. Restore the apostrophe
# when the kinship noun is followed by a common possessed noun. The possessed-
# noun whitelist constrains the rule so real plurals followed by a verb
# ("his parents traveled") aren't rewritten.
_KINSHIP_NOUNS = (
    "parent", "friend", "sister", "brother", "mom", "dad", "mother", "father",
    "kid", "child", "son", "daughter", "cousin", "aunt", "uncle",
    "grandma", "grandpa", "grandmother", "grandfather",
    "wife", "husband", "boyfriend", "girlfriend", "roommate", "neighbor",
)
_POSSESSED_NOUNS = frozenset({
    "house", "home", "place", "room", "apartment", "car", "truck",
    "family", "dog", "cat", "phone", "computer", "laptop",
    "kid", "kids", "baby", "child", "son", "daughter",
    "sister", "brother", "mom", "dad", "mother", "father", "parents",
    "friend", "friends", "wife", "husband", "boyfriend", "girlfriend",
    "life", "story", "name", "fault", "idea", "opinion", "decision",
    "advice", "response", "reaction", "birthday", "wedding", "death",
    "funeral", "divorce", "money", "account", "business", "job", "work",
    "office", "school", "class", "party", "dinner", "lunch", "breakfast",
    "side", "face", "hand", "hands", "head", "arm", "arms", "leg", "legs",
    "eye", "eyes", "mouth", "voice", "heart", "mind", "feelings", "feeling",
    "stuff", "things", "thing", "problem", "problems", "issue", "issues",
    "secret", "secrets", "plan", "plans", "room", "stuff",
})
_POSSESSIVE_KINSHIP_RE = re.compile(
    r"\b(" + "|".join(_KINSHIP_NOUNS) + r")s(\s+)(\w+)",
    re.IGNORECASE,
)


def _apply_possessive_restore(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        head, sep, nxt = m.group(1), m.group(2), m.group(3)
        if nxt.lower() not in _POSSESSED_NOUNS:
            return m.group(0)
        return f"{head}'s{sep}{nxt}"
    return _POSSESSIVE_KINSHIP_RE.sub(repl, text)


# Edge-tts Guy Neural mangles a small set of words by dropping or merging
# phonemes. Force the longer / less-ambiguous form before synthesis.
_TTS_HOMOGRAPHS: dict[str, str] = {
    "butt": "buttocks",     # otherwise reads as "but" (conjunction)
    "butts": "buttocks",
}


# Compound modifiers like "60-hour" / "2-year" / "30-day" are pronounced
# awkwardly by edge-tts (the hyphen reads as "dash" or fuses the tokens).
# Splitting on the hyphen lets the TTS speak the number and word naturally
# and keeps the on-screen caption readable. Restricted to digits + letters
# so we don't break model numbers like "iPhone-15" (rare in body prose) or
# negative ranges.
_NUMERIC_HYPHEN_WORD_RE = re.compile(r"\b(\d+)-([a-zA-Z]+)\b")


def _split_numeric_hyphen(text: str) -> str:
    return _NUMERIC_HYPHEN_WORD_RE.sub(r"\1 \2", text)


# Blood-type tokens (AB-, O+, A+, B-) — edge-tts garbles the sign, so expand
# to "AB negative" / "O positive" before synthesis. Alternation is longest-first
# (AB before A/B) so "AB-" matches as "AB", not "A" + leftover "B-".
_BLOOD_TYPE_RE = re.compile(r"\b(AB|O|A|B)([+-])(?=\b|\s|[^\w+-])")
_BLOOD_SIGN = {"+": "positive", "-": "negative"}


def _expand_blood_types(text: str) -> str:
    return _BLOOD_TYPE_RE.sub(
        lambda m: f"{m.group(1)} {_BLOOD_SIGN[m.group(2)]}",
        text,
    )


# edge-tts gives lowercase pronoun `i` flatter prosody than `I` — uppercase
# before synthesis. Pattern covers both standalone `i` and apostrophe-led
# `i'm` / `i've` / `i'll`.
_LOWER_I_RE = re.compile(r"\bi(?=\b|')")


def _fix_lowercase_i(text: str) -> str:
    return _LOWER_I_RE.sub("I", text)


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


def _apply_apostrophe_restore(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        sub = _RESTORE_APOSTROPHE.get(w.lower())
        return _preserve_case(w, sub) if sub else w
    return re.sub(r"\b[a-zA-Z']+\b", repl, text)


def _apply_tts_homographs(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        sub = _TTS_HOMOGRAPHS.get(w.lower())
        return _preserve_case(w, sub) if sub else w
    return re.sub(r"\b[a-zA-Z']+\b", repl, text)


def _truncate_tail(text: str) -> str:
    """Drop everything from the first UPDATE: / FINAL EDIT: / TL;DR onward.
    These markers indicate post-punchline content (later edits, summaries,
    thank-you tails) that hurt the video's narrative arc and inflate duration.
    Must run BEFORE _expand_abbreviations so TL;DR variants are still
    matchable (the abbrev pass rewrites TL;DR → "To summarize")."""
    m = _TAIL_TRUNCATE_RE.search(text)
    if m:
        return text[:m.start()].rstrip()
    return text


def _collapse_whitespace(text: str) -> str:
    lines = [_WS_COLLAPSE_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def normalize(story: Story, cfg: Config) -> str:
    body = story.selftext
    if cfg.filter.confusable_mode != "off":
        body = sanitize_confusables(body)
    body = _normalize_smart_punct(body)
    body = _strip_markdown(body)
    body = _truncate_tail(body)
    body = _expand_abbreviations(body)

    title_raw = story.title
    if cfg.filter.confusable_mode != "off":
        title_raw = sanitize_confusables(title_raw)
    title_raw = _normalize_smart_punct(title_raw)
    title = _expand_abbreviations(_strip_markdown(title_raw))
    title = _spell_small_ints(title)

    title_stripped = title.rstrip()
    sep = "" if title_stripped.endswith((".", "!", "?")) else "."
    text = f"{title_stripped}{sep}\n\n{body}"

    text = _fix_lowercase_i(text)

    if cfg.filter.profanity_mode == "soft":
        text = _soft_replace_profanity(text)

    text = _apply_apostrophe_restore(text)
    text = _apply_possessive_restore(text)
    text = _apply_tts_homographs(text)
    text = _split_numeric_hyphen(text)
    text = _expand_blood_types(text)
    text = _fix_etc_punct(text)
    text = _fix_common_typos(text)

    text = _collapse_whitespace(text)
    return text
