"""HTTP security headers (P0.3, research run 7).

Cloudflare Zero Trust authenticates the user; it does not stop a
browser from doing something dangerous once an authenticated user's
page has loaded (XSS, clickjacking, MIME-sniffing, leaking the current
URL to a third party). These headers are the browser-side layer,
complementary to — not replaced by — Zero Trust.

CSP is the load-bearing header here. `script-src` is hash-based against
the SvelteKit static build's inline bootstrap script (computed at
import time from the actual built `index.html`, not hand-copied — a
frontend rebuild that changes the inline script naturally invalidates
and recomputes the hash rather than silently going stale).

`style-src` intentionally allows `'unsafe-inline'`. The build's only
inline style is a single always-identical `style="display: contents"`
from SvelteKit's own bootstrap wrapper (verified against the real
build output), but shadcn-svelte's `bits-ui` primitives (popovers,
dialogs, dropdowns — used throughout this app) rely on Floating UI's
positioning, which in some code paths still needs a permissive
style-src to render correctly. Hash/nonce-based style-src has
historically had cross-browser support gaps and would need real
browser verification across every interactive component to confirm
safe — script-src (the directive that actually stops XSS) is strict;
this is a deliberate, documented, common trade-off, not an oversight.

Deliberately NOT set: X-XSS-Protection (deprecated, some browsers had
buggy behavior), Feature-Policy (superseded by Permissions-Policy),
Expect-CT (Certificate Transparency is now enforced natively by
browsers) — all three are still recommended by generic security
scanners but are obsolete per current guidance.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path

from webapp.backend import settings

log = logging.getLogger("webapp")

_INLINE_SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.DOTALL)


def _inline_script_hashes(html_path: Path) -> list[str]:
    """CSP source-list entries for every inline <script> body found in
    the built index.html. External `<script src=...></script>` tags
    match the same regex but have an empty body, so they're skipped."""
    if not html_path.exists():
        return []
    html = html_path.read_text(encoding="utf-8")
    hashes = []
    for m in _INLINE_SCRIPT_RE.finditer(html):
        body = m.group(1)
        if not body.strip():
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return hashes


_SCRIPT_HASHES = _inline_script_hashes(settings.FRONTEND_BUILD_DIR / "index.html")
if not settings.DEV_MODE and not _SCRIPT_HASHES:
    log.warning(
        "no inline-script hashes found for CSP script-src — either the "
        "frontend build is missing/stale or index.html has no inline "
        "bootstrap script. SPA may fail to load under the CSP below."
    )

_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'" + ("".join(f" {h}" for h in _SCRIPT_HASHES)),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), usb=(), payment=(), "
        "fullscreen=(), accelerometer=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # Legacy fallback for browsers predating CSP frame-ancestors; the
    # real control is frame-ancestors above.
    "X-Frame-Options": "DENY",
}
if not settings.DEV_MODE:
    # Only meaningful over HTTPS (always true in prod, behind Cloudflare
    # Tunnel) — setting it in dev over plain http://localhost would be
    # actively wrong (browsers ignore it there, but no reason to ship it).
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
