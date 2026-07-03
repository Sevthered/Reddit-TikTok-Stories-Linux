"""Server-side verification of Cloudflare Access's `Cf-Access-Jwt-Assertion`.

Zero Trust authenticates the user at the edge and injects this header on
every request that passes its policy check. The app trusted its mere
*presence* implicitly until now — this module actually verifies the RS256
signature + claims, so a Tunnel/firewall misconfiguration that lets a
request reach the origin without going through Access (see
wiki/bugs/2026-07-03-webapp-lan-bypass-firewall.md for a real instance of
that class of bug) doesn't silently grant access (R2.4, research runs 3 + 7).
"""
from __future__ import annotations

import logging

import jwt
from jwt import PyJWKClient

from webapp.backend import settings

log = logging.getLogger("webapp.cf_access")

# PyJWKClient handles the JWKS fetch, in-process caching, and re-fetch on a
# kid-cache-miss internally — no need to hand-roll a TTL cache. Cloudflare
# rotates Access signing keys infrequently, so a 1h lifespan is generous
# without leaving a stale keyset around long after a real rotation.
_jwks_client = PyJWKClient(
    f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs",
    cache_keys=True,
    lifespan=3600,
)


class CfAccessError(Exception):
    """Raised for any missing/malformed/invalid Cf-Access-Jwt-Assertion."""


def verify_access_jwt(token: str) -> dict:
    """Verify signature + aud + iss + exp. Returns the decoded claims on
    success; raises CfAccessError (never the underlying PyJWT exception
    type, so callers don't need to import jwt.exceptions) on any failure."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.CF_ACCESS_AUD,
            issuer=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        )
    except jwt.PyJWTError as exc:
        raise CfAccessError(str(exc)) from exc
    return claims
