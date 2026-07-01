"""Scripted DOM recon for TikTok Studio upload flow.

Fully autonomous — launches Playwright Chromium (headful), injects
tiktok cookies, uploads a test video, waits for the form to appear,
and dumps every `data-e2e` / `data-tt` / `<input type=file>` /
`<textarea>` / `[role=switch]` / relevant `<button>` in every phase.

Never clicks Post. Never publishes.

Usage:
    ./venv/bin/python -u scripts/dom_recon.py <video.mp4> [cover.png]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout


_ROOT = Path(__file__).resolve().parent.parent
_COOKIES = _ROOT / "data" / "cookies" / "tiktok_cookies.txt"
_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def _parse_netscape_cookies(path: Path) -> list[dict]:
    cookies: list[dict] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include_sub, cpath, secure, expires, name, value = parts[:7]
        if "tiktok.com" not in domain:
            continue
        try:
            exp = int(expires)
        except ValueError:
            exp = -1
        cookies.append({
            "name": name, "value": value, "domain": domain,
            "path": cpath or "/", "expires": exp, "httpOnly": False,
            "secure": secure.upper() == "TRUE", "sameSite": "Lax",
        })
    return cookies


def _hr(title: str) -> None:
    bar = "=" * 8
    print(f"\n{bar} {title} {bar}")


def _dump_file_inputs(page: Page) -> None:
    _hr("<input type=file>")
    print(json.dumps(page.evaluate("""
    () => Array.from(document.querySelectorAll('input[type="file"]')).map((el, i) => ({
        idx: i, accept: el.getAttribute('accept'), name: el.name,
        id: el.id, class: el.className,
        parentClass: el.parentElement?.className,
        parentText: (el.parentElement?.innerText || '').slice(0, 100).trim(),
    }))
    """), indent=2, ensure_ascii=False))


def _dump_hooks(page: Page) -> None:
    """Print unique (attr, value) pairs for data-e2e / data-testid / data-tt."""
    _hr("data-e2e / data-testid / data-tt (unique)")
    hooks = page.evaluate("""
    () => {
        const attrs = ['data-e2e', 'data-testid', 'data-tt'];
        const out = [];
        attrs.forEach(a => {
            document.querySelectorAll('[' + a + ']').forEach(el => {
                out.push({
                    attr: a,
                    value: el.getAttribute(a),
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    aria: el.getAttribute('aria-label'),
                    text: (el.innerText || '').slice(0, 80).trim().replace(/\\n/g, ' '),
                });
            });
        });
        return out;
    }
    """)
    seen = set()
    for h in hooks:
        key = (h["attr"], h["value"])
        if key in seen:
            continue
        seen.add(key)
        text = h["text"]
        if len(text) > 60:
            text = text[:57] + "..."
        role = f" role={h['role']!r}" if h["role"] else ""
        aria = f" aria={h['aria']!r}" if h["aria"] else ""
        print(f"  [{h['attr']}={h['value']!r}] {h['tag']}{role}{aria} :: {text!r}")


def _dump_textareas(page: Page) -> None:
    _hr("<textarea> + [contenteditable]")
    print(json.dumps(page.evaluate("""
    () => {
        const out = [];
        document.querySelectorAll('textarea').forEach(el => out.push({
            kind: 'textarea', name: el.name, id: el.id,
            class: el.className.slice(0, 200),
            placeholder: el.placeholder, ariaLabel: el.getAttribute('aria-label'),
        }));
        document.querySelectorAll('[contenteditable="true"]').forEach(el => out.push({
            kind: 'contenteditable', tag: el.tagName,
            class: el.className.slice(0, 200),
            ariaLabel: el.getAttribute('aria-label'),
            role: el.getAttribute('role'),
            dataE2E: el.getAttribute('data-e2e'),
            innerText: (el.innerText || '').slice(0, 60).trim(),
        }));
        return out;
    }
    """), indent=2, ensure_ascii=False))


def _dump_switches(page: Page) -> None:
    _hr("[role=switch] + input[type=checkbox]")
    print(json.dumps(page.evaluate("""
    () => Array.from(document.querySelectorAll('[role="switch"], input[type="checkbox"]')).map(el => {
        // Walk up to a labeled container for context.
        let ctx = el;
        for (let i = 0; i < 4 && ctx.parentElement; i++) ctx = ctx.parentElement;
        return {
            role: el.getAttribute('role'),
            type: el.type,
            checked: el.getAttribute('aria-checked') ?? el.checked,
            ariaLabel: el.getAttribute('aria-label'),
            e2e: el.getAttribute('data-e2e'),
            tt: el.getAttribute('data-tt'),
            ctxText: (ctx.innerText || '').slice(0, 150).trim().replace(/\\n/g, ' | '),
        };
    })
    """), indent=2, ensure_ascii=False))


def _dump_matching_buttons(page: Page, needles: list[str]) -> None:
    _hr(f"buttons/links matching {needles}")
    hits = page.evaluate("""
    (needles) => {
        const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        const out = [];
        document.querySelectorAll('button, [role="button"], a').forEach(el => {
            const t = norm(el.innerText);
            for (const n of needles) {
                if (t.includes(n.toLowerCase())) {
                    out.push({
                        tag: el.tagName, role: el.getAttribute('role'),
                        text: (el.innerText || '').slice(0, 80).trim(),
                        e2e: el.getAttribute('data-e2e'),
                        tt: el.getAttribute('data-tt'),
                        aria: el.getAttribute('aria-label'),
                    });
                    break;
                }
            }
        });
        return out;
    }
    """, needles)
    print(json.dumps(hits, indent=2, ensure_ascii=False))


def _dump_privacy_dropdowns(page: Page) -> None:
    _hr("select / [role=combobox|listbox] / [role=radio] near 'quién puede ver'")
    print(json.dumps(page.evaluate("""
    () => {
        const out = [];
        document.querySelectorAll('select, [role="combobox"], [role="listbox"], [role="radiogroup"], [role="radio"]').forEach(el => {
            out.push({
                tag: el.tagName, role: el.getAttribute('role'),
                e2e: el.getAttribute('data-e2e'), tt: el.getAttribute('data-tt'),
                ariaLabel: el.getAttribute('aria-label'),
                text: (el.innerText || '').slice(0, 120).trim(),
            });
        });
        return out;
    }
    """), indent=2, ensure_ascii=False))


# ---------------- Flow ----------------

def main() -> None:
    if len(sys.argv) < 2:
        print("usage: dom_recon.py <video.mp4> [cover.png]")
        sys.exit(1)
    video = Path(sys.argv[1]).resolve()
    cover = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else None
    if not video.exists():
        raise SystemExit(f"video missing: {video}")

    cookies = _parse_netscape_cookies(_COOKIES)
    print(f"loaded {len(cookies)} tiktok cookies")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
        )
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
        print(f"landed on: {page.url}  title={page.title()!r}")
        time.sleep(3)

        # -------- Phase 1 --------
        print("\n########## PHASE 1: initial upload page ##########")
        _dump_file_inputs(page)
        _dump_matching_buttons(page, ["cargar", "upload", "seleccionar"])
        _dump_hooks(page)

        # Upload the video.
        print(f"\n>> uploading {video.name} ...")
        try:
            page.set_input_files('input[type="file"]', str(video))
        except Exception as e:
            print(f"set_input_files FAILED: {e}")
            raise
        print(">> set_input_files OK; waiting for form ...")

        # -------- Phase 2: wait for description textarea to appear --------
        # Try several plausible selectors for the caption input to detect
        # "form is ready" state. Any one hitting means we can dump.
        selectors = [
            '[data-e2e="upload-title-input"]',
            '[contenteditable="true"]',
            'textarea',
        ]
        form_ready = False
        deadline = time.time() + 90
        while time.time() < deadline:
            for sel in selectors:
                try:
                    el = page.wait_for_selector(sel, timeout=2000)
                    if el:
                        print(f">> form detected via selector: {sel!r}")
                        form_ready = True
                        break
                except PWTimeout:
                    continue
            if form_ready:
                break

        if not form_ready:
            print("!! form never appeared within 90s — dumping anyway")

        # Extra settle time so all form fields render.
        time.sleep(6)

        print("\n########## PHASE 2: form fields visible ##########")
        _dump_textareas(page)
        _dump_switches(page)
        _dump_privacy_dropdowns(page)
        _dump_matching_buttons(page, [
            "publicar", "post", "programar", "cubierta", "cover", "portada",
            "borrador", "draft", "editar", "cambiar", "ia", "ai",
            "comentar", "duo", "duet", "stitch", "todos", "sólo yo", "amigos",
        ])
        _dump_hooks(page)

        # -------- Phase 3: attempt to open cover editor --------
        # Only if we have a cover to test with.
        if cover:
            print("\n########## PHASE 3: cover editor probe ##########")
            # Common labels for the "edit cover" affordance.
            cover_labels = ["Portada", "Cubierta", "Editar portada", "Editar cubierta",
                            "Cover", "Edit cover"]
            found_cover_trigger = False
            for label in cover_labels:
                try:
                    btn = page.get_by_text(label, exact=False).first
                    if btn.is_visible(timeout=1000):
                        print(f">> found cover trigger: {label!r}")
                        btn.click()
                        found_cover_trigger = True
                        break
                except Exception:
                    continue
            if not found_cover_trigger:
                print("!! no obvious cover trigger found")
            time.sleep(3)
            _dump_file_inputs(page)
            _dump_matching_buttons(page, ["subir", "upload", "seleccionar", "cargar", "guardar", "confirm"])
            _dump_hooks(page)

        print("\n>> recon done. keeping browser open 30s for manual look.")
        time.sleep(30)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
