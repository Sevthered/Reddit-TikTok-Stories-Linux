from __future__ import annotations

# Minimal Unicode-confusable map: high-signal Cyrillic / Greek / fullwidth
# look-alikes mapped to their Latin equivalents. Source: UTS #39 (skeleton).
# Goal: kill spam tricks like "Ѕtake" (U+0405) without pulling a dep.

_CONFUSABLES: dict[str, str] = {
    # Cyrillic capitals (U+0400-04FF) that look Latin
    "Ѕ": "S",  # Ѕ DZE
    "І": "I",  # І BYELORUSSIAN-UKRAINIAN I
    "Ј": "J",  # Ј JE
    "А": "A",  # А A
    "В": "B",  # В VE
    "Е": "E",  # Е IE
    "К": "K",  # К KA
    "М": "M",  # М EM
    "Н": "H",  # Н EN
    "О": "O",  # О O
    "Р": "P",  # Р ER
    "С": "C",  # С ES
    "Т": "T",  # Т TE
    "Х": "X",  # Х KHA
    "Ч": "Y",  # Ч (rough) — visually 4/Y; conservatively keep "Y" off
    # Cyrillic lowercase
    "а": "a",  # а
    "е": "e",  # е
    "и": "u",  # и (italic looks like u in some fonts) — skip risk
    "к": "k",  # к
    "о": "o",  # о
    "р": "p",  # р
    "с": "c",  # с
    "у": "y",  # у
    "х": "x",  # х
    "ѕ": "s",  # ѕ
    "і": "i",  # і
    "ј": "j",  # ј
    # Greek capitals
    "Α": "A",  # Α ALPHA
    "Β": "B",  # Β BETA
    "Ε": "E",  # Ε EPSILON
    "Ζ": "Z",  # Ζ ZETA
    "Η": "H",  # Η ETA
    "Ι": "I",  # Ι IOTA
    "Κ": "K",  # Κ KAPPA
    "Μ": "M",  # Μ MU
    "Ν": "N",  # Ν NU
    "Ο": "O",  # Ο OMICRON
    "Ρ": "P",  # Ρ RHO
    "Τ": "T",  # Τ TAU
    "Υ": "Y",  # Υ UPSILON
    "Χ": "X",  # Χ CHI
    # Greek lowercase
    "ο": "o",  # ο OMICRON
    "ρ": "p",  # ρ RHO
    "υ": "u",  # υ UPSILON
    "χ": "x",  # χ CHI
    # Conservative: do NOT map non-look-alikes; leave them so TTS can still
    # pronounce legit foreign words.
}

_TABLE = str.maketrans(_CONFUSABLES)

# Suspicious ranges used to flag strict-mode rejects (after sanitize, leftover
# non-Latin-letter Cyrillic/Greek/fullwidth suggests deliberate obfuscation).
_SUSPECT_RANGES = (
    (0x0400, 0x04FF),  # Cyrillic
    (0x0370, 0x03FF),  # Greek
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms
)


def sanitize(text: str) -> str:
    """Map known confusable code points to Latin look-alikes."""
    return text.translate(_TABLE)


def has_confusable(text: str) -> bool:
    """True if any char falls in a suspect range. Use AFTER sanitize() to
    catch deliberate spam still trying to hide brand names."""
    for ch in text:
        cp = ord(ch)
        for lo, hi in _SUSPECT_RANGES:
            if lo <= cp <= hi:
                return True
    return False
