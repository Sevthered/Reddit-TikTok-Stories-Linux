from __future__ import annotations

import base64
import logging
import os
import random
import re
import subprocess
from pathlib import Path

from core.ffmpeg import which_ffmpeg
from pipeline.scrape import Story

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"
_TEMPLATE = _ASSETS / "card_template.html"
_STYLE = _ASSETS / "card_style.css"
_AVATAR = _ASSETS / "avatar.png"
_FONTS = _ASSETS / "fonts"
_FONT_BOLD = _FONTS / "IBMPlexSans-Bold.woff2"
_FONT_REGULAR = _FONTS / "IBMPlexSans-Regular.woff2"

_HANDLE_DEFAULT = "RealRedditStories"
_BADGES = "🤖 🥇 💬 ⬆ 📌"

_NEG_AUX = frozenset({
    "didn't", "didnt", "wasn't", "wasnt", "won't", "wont",
    "can't", "cant", "couldn't", "couldnt", "shouldn't", "shouldnt",
    "wouldn't", "wouldnt", "hasn't", "hasnt", "haven't", "havent",
    "hadn't", "hadnt", "isn't", "isnt", "aren't", "arent",
    "doesn't", "doesnt", "don't", "dont", "mustn't", "mustnt",
    "shan't", "shant", "weren't", "werent",
})

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
    return _nlp


def _pick_highlights(title: str) -> tuple[str | None, str | None]:
    """Return (red_span, green_span) as raw text substrings of title (verbatim
    tokens; caller reinserts them). Red priority: first negation-aux (didn't,
    won't, ...); fallback first VERB. Green: last NOUN."""
    doc = _get_nlp()(title)
    red: str | None = None
    for t in doc:
        if t.text.lower() in _NEG_AUX:
            red = t.text
            break
    if red is None:
        for t in doc:
            if t.pos_ == "VERB":
                red = t.text
                break
    green: str | None = None
    for t in reversed(list(doc)):
        if t.pos_ == "NOUN":
            green = t.text
            break
    return red, green


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _highlight_title_html(title: str) -> str:
    """Wrap red-token in <span class="hi-red">, green-token in <span class="hi-green">.
    HTML-escapes everything else."""
    red, green = _pick_highlights(title)
    esc_title = _escape_html(title)

    if red:
        red_esc = _escape_html(red)
        pattern = re.compile(rf"(?<![\w'’])({re.escape(red_esc)})(?![\w'’])")
        esc_title = pattern.sub(r'<span class="hi-red">\1</span>', esc_title, count=1)

    if green:
        green_esc = _escape_html(green)
        idx = esc_title.rfind(green_esc)
        if idx != -1:
            before = esc_title[:idx]
            char_before = before[-1] if before else ""
            after = esc_title[idx + len(green_esc):]
            char_after = after[0] if after else ""
            word_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_'’")
            if char_before not in word_chars and char_after not in word_chars:
                esc_title = (
                    before
                    + f'<span class="hi-green">{green_esc}</span>'
                    + after
                )

    return esc_title


def _format_count(n: int) -> str:
    if n < 1000:
        return str(n)
    k = n / 1000
    if k < 10:
        return f"{k:.1f}K"
    return f"{int(k)}K"


def _fake_engagement(rng: random.Random) -> tuple[str, str]:
    """Log-distributed likes 1.2K-99K + comments 100-9K at 3-12% of likes."""
    likes = int(1200 * (10 ** (rng.random() * 1.917)))
    likes = min(99000, max(1200, likes))
    ratio = rng.uniform(0.03, 0.12)
    comments = int(likes * ratio)
    comments = min(9000, max(100, comments))
    return _format_count(likes), _format_count(comments)


def _data_uri(path: Path, mime: str) -> str:
    """Return a base64 data URI for the file at `path`. Guarantees the browser
    has the bytes at parse time — no file:// race with networkidle."""
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_html(story: Story, likes: str, comments: str, handle: str) -> str:
    if not _AVATAR.exists():
        raise FileNotFoundError(f"avatar missing: {_AVATAR}")
    if not _FONT_BOLD.exists() or not _FONT_REGULAR.exists():
        raise FileNotFoundError(f"fonts missing under {_FONTS}")

    avatar_uri = _data_uri(_AVATAR, "image/png")
    font_bold_uri = _data_uri(_FONT_BOLD, "font/woff2")
    font_regular_uri = _data_uri(_FONT_REGULAR, "font/woff2")

    css = _STYLE.read_text(encoding="utf-8")
    css = (css
           .replace("__FONT_REGULAR_URI__", font_regular_uri)
           .replace("__FONT_BOLD_URI__", font_bold_uri))

    tmpl = _TEMPLATE.read_text(encoding="utf-8")
    html = (tmpl
            .replace("__CSS__", css)
            .replace("__AVATAR_URI__", avatar_uri)
            .replace("__HANDLE__", _escape_html(handle))
            .replace("__SUBREDDIT__", _escape_html(story.subreddit))
            .replace("__BADGES__", _BADGES)
            .replace("__TITLE_HTML__", _highlight_title_html(story.title.strip()))
            .replace("__LIKES__", likes)
            .replace("__COMMENTS__", comments))
    return html


def make_card(story: Story, out_path: Path, *, seed: int | str | None = None,
              handle: str | None = None) -> Path:
    """Render Reddit-card overlay for `story` as a transparent-bg PNG.

    seed: RNG seed for fake engagement counts. Defaults to story.id so re-renders
    of the same post produce identical numbers.
    """
    from playwright.sync_api import sync_playwright

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    h = handle or os.environ.get("TIKTOK_HANDLE") or _HANDLE_DEFAULT

    seed_val = seed if seed is not None else story.id
    rng = random.Random(hash(seed_val))
    likes, comments = _fake_engagement(rng)

    html = _build_html(story, likes, comments, h)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1080, "height": 1000},
                                      device_scale_factor=1)
        page = context.new_page()
        page.set_content(html, wait_until="networkidle")
        card = page.locator(".card")
        card.screenshot(path=str(out_path), omit_background=True)
        context.close()
        browser.close()

    log.info("card -> %s", out_path)
    return out_path


def extract_cover(video_path: Path, out_path: Path,
                  *, frame_index: int = 0,
                  crop_size: int = 1080,
                  y_offset: int = 200) -> Path:
    """Grab frame `frame_index` from the assembled 1080x1920 video and
    center-crop it to a `crop_size` square, biased to keep the card region
    visible (y_offset from top). Card sits at y=330-830 on 1920 frame; with
    y_offset=200 the crop covers y=200-1280 and the whole card survives.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"select=eq(n\\,{frame_index}),"
        f"crop={crop_size}:{crop_size}:0:{y_offset},"
        f"scale={crop_size}:{crop_size}"
    )
    cmd = [
        which_ffmpeg(), "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-vsync", "vfr",
        "-frames:v", "1",
        str(out_path),
    ]
    log.info("cover extract: frame %d of %s -> %s",
             frame_index, video_path.name, out_path)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
