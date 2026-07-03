"""Shared slowapi Limiter instance (P0.2, research runs 3 + 7).

Lives in its own module — not in app.py — so routers can import
`limiter` for `@limiter.limit(...)` decorators without a circular
import (app.py imports the routers it mounts).

NOTE: `slowapi.middleware.SlowAPIMiddleware` is deliberately NOT used.
Its route auto-detection (`_find_route_handler`) walks `app.routes`
looking for a plain object with a `.endpoint` attribute, but this
FastAPI version wraps included routers in `_IncludedRouter` /
`_EffectiveRouteContext` objects that don't expose `.endpoint` at the
top level — the lookup always misses, `_should_exempt` treats the miss
as "exempt", and every request silently skips rate limiting. Verified
directly against the installed fastapi==0.139.0 before shipping.
Per-route `@limiter.limit()` decorators sidestep this entirely: the
decorator wraps the endpoint function directly and enforces via
`_route_limits` keyed by function identity, never touching
`_find_route_handler`. Verified working locally before deploy.
"""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from webapp.backend import settings


def rate_limit_key(request: Request) -> str:
    """Behind Cloudflare Tunnel, `request.client.host` is always
    cloudflared's own loopback address — identical for every visitor —
    so keying on it would rate-limit the whole app as one client.
    `CF-Connecting-IP` is the header Cloudflare sets to the real client
    IP. Trusted here because the origin is reachable ONLY through the
    Tunnel; if that stopped being true this header would become
    attacker-controlled."""
    cf_ip = request.headers.get("CF-Connecting-IP")
    return cf_ip if cf_ip else get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=[settings.RATE_LIMIT_DEFAULT])
