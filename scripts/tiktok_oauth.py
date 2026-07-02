#!/usr/bin/env python3
"""One-shot TikTok Login Kit OAuth flow.

Prints an authorization URL, waits for the operator to paste the `code`
back after TikTok redirects the browser to the static callback page on
GH Pages, then exchanges the code for an access_token + refresh_token
pair and prints them for the operator to copy into `.env`.

Reads TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REDIRECT_URI
from the environment (or a sibling `.env` file).

Usage:
    python scripts/tiktok_oauth.py

Refresh tokens returned by TikTok Login Kit live ~365 days; access tokens
~24 hours. Store the refresh_token in .env as TIKTOK_REFRESH_TOKEN and
let the pipeline mint fresh access tokens on demand via
pipeline.tiktok_api.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import _load_dotenv  # noqa: E402


AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "")
    if required and not v:
        raise SystemExit(f"missing env var: {name}")
    return v


def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) per RFC 7636, S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_key: str, redirect_uri: str, scopes: list[str], state: str,
                   code_challenge: str) -> str:
    params = {
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(client_key: str, client_secret: str, code: str, redirect_uri: str,
                  code_verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        import json
        return json.loads(resp.read())


def main() -> int:
    _load_dotenv(_ROOT / ".env")

    client_key = _env("TIKTOK_CLIENT_KEY")
    client_secret = _env("TIKTOK_CLIENT_SECRET")
    redirect_uri = _env("TIKTOK_REDIRECT_URI")
    scopes = os.environ.get("TIKTOK_SCOPES", "video.list").split(",")

    state = secrets.token_urlsafe(16)
    code_verifier, code_challenge = _pkce_pair()
    url = build_auth_url(client_key, redirect_uri, scopes, state, code_challenge)

    print("\n=== TikTok Login Kit OAuth ===")
    print(f"scopes: {scopes}")
    print(f"redirect_uri: {redirect_uri}")
    print(f"state (verify this on the callback page): {state}\n")
    print("Opening this URL in your browser (or copy it manually):")
    print(f"  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass

    print("After authorising with the @RealRedditStories sandbox account, the")
    print("browser lands on the Pages callback page which displays a `code`.\n")
    code = input("Paste the code here: ").strip()
    if not code:
        print("no code entered — aborting", file=sys.stderr)
        return 1

    print("\nexchanging code for tokens...")
    resp = exchange_code(client_key, client_secret, code, redirect_uri, code_verifier)
    if "access_token" not in resp:
        print("token exchange failed:")
        import json
        print(json.dumps(resp, indent=2))
        return 2

    print("\n=== SUCCESS ===")
    print("Add these lines to /srv/tiktok/app/.env on the server:\n")
    print(f"TIKTOK_ACCESS_TOKEN={resp['access_token']}")
    print(f"TIKTOK_REFRESH_TOKEN={resp['refresh_token']}")
    print(f"TIKTOK_OPEN_ID={resp.get('open_id', '')}")
    print(f"# access_token TTL: {resp.get('expires_in','?')}s; refresh: {resp.get('refresh_expires_in','?')}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
