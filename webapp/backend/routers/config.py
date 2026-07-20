"""Config editor endpoints.

- `config.toml` is edited as raw text. Validation goes through
  `core.config.load_config` against a temp file before we atomically
  `os.replace` the real file, so a bad edit never poisons the pipeline.

- `.env` is exposed as a masked line list (secrets → `***<last4>`) and a
  single-key PUT that rewrites only that key, preserving order + comments.
  This keeps secret values out of the UI while still letting the user
  rotate non-secret knobs.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import ConfigError, load_config
from webapp.backend import settings
from webapp.backend.rate_limit import limiter

log = logging.getLogger("webapp.routers.config")

router = APIRouter(prefix="/config", tags=["config"])

# Mask by default (allowlist), not blacklist: a new/unknown env key is treated
# as secret and masked in GET /api/config/env unless its name clearly marks it
# non-sensitive. Inverting the old substring-blacklist closes the gap where an
# unmatched secret key leaked its value in full ([[Excessive-Data-Exposure]]).
_NON_SECRET_MARKERS: tuple[str, ...] = (
    "HOST", "PORT", "MODE", "LEVEL", "ENABLED", "TIMEOUT",
    "WINDOW", "MARGIN", "TZ", "OFFSET", "LIMIT",
)


def _is_secret(key: str) -> bool:
    up = key.upper()
    return not any(m in up for m in _NON_SECRET_MARKERS)


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "***"
    return f"***{value[-4:]}"


# ---- TOML ---------------------------------------------------------------


class TomlOut(BaseModel):
    path: str
    content: str


class TomlIn(BaseModel):
    content: str = Field(..., description="Full config.toml payload")


@router.get("/toml", response_model=TomlOut)
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def get_toml(request: Request) -> TomlOut:
    path = settings.CONFIG_PATH
    if not path.exists():
        raise HTTPException(500, detail=f"{path} missing")
    return TomlOut(path=str(path), content=path.read_text(encoding="utf-8"))


@router.put("/toml", response_model=TomlOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def put_toml(request: Request, payload: TomlIn) -> TomlOut:
    path = settings.CONFIG_PATH
    # Write to a temp file in the same directory so the atomic os.replace
    # stays on-device. Validate with load_config before swap.
    tmp_fd, tmp_path_s = tempfile.mkstemp(
        prefix=".config-", suffix=".toml.tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_s)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(payload.content)
        try:
            load_config(tmp_path)
        except ConfigError as e:
            raise HTTPException(422, detail=f"config invalid: {e}") from e
        except Exception as e:  # noqa: BLE001  tomllib.TOMLDecodeError etc.
            raise HTTPException(422, detail=f"toml parse error: {e}") from e
        os.replace(tmp_path, path)
        log.info("config.toml updated (%d bytes)", len(payload.content))
    finally:
        # os.replace consumed tmp_path on success; on failure clean up.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return TomlOut(path=str(path), content=path.read_text(encoding="utf-8"))


# ---- Structured section patch (comment-preserving) ---------------------


class SectionPatchIn(BaseModel):
    section: str = Field(..., description="TOML table name, e.g. 'reddit'")
    fields: dict[str, object] = Field(..., description="key → new value, patched in place")


@router.put("/toml/section", response_model=TomlOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def put_toml_section(request: Request, payload: SectionPatchIn) -> TomlOut:
    """Patch one `[section]` in config.toml while preserving comments,
    key ordering, and whitespace. tomlkit rewrites only the touched keys
    and re-emits the rest byte-for-byte, so hand-authored comments (the
    `# json | praw | rss` legend etc.) stay intact.

    Validation still goes through core.config.load_config against a
    temp file before atomic os.replace, so a bad edit can't poison the
    pipeline."""
    path = settings.CONFIG_PATH
    if not path.exists():
        raise HTTPException(500, detail=f"{path} missing")

    text = path.read_text(encoding="utf-8")
    doc = tomlkit.parse(text)

    section = payload.section
    if section not in doc:
        raise HTTPException(404, detail=f"section [{section}] not found")

    table = doc[section]
    for k, v in payload.fields.items():
        # tomlkit.item() converts native Python → the correct TOML node
        # kind (string/int/array/etc.) and preserves the surrounding
        # trivia when we overwrite an existing key.
        table[k] = v

    new_content = tomlkit.dumps(doc)

    tmp_fd, tmp_path_s = tempfile.mkstemp(
        prefix=".config-", suffix=".toml.tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_s)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        try:
            load_config(tmp_path)
        except ConfigError as e:
            raise HTTPException(422, detail=f"config invalid: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, detail=f"toml parse error: {e}") from e
        os.replace(tmp_path, path)
        log.info("config.toml [%s] patched (%d fields)", section, len(payload.fields))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return TomlOut(path=str(path), content=path.read_text(encoding="utf-8"))


# ---- .env ---------------------------------------------------------------


@dataclass
class _EnvLine:
    """One physical line of `.env`. `key` is None for comments/blanks so
    we can round-trip formatting on PUT."""
    key: str | None
    value: str
    raw: str


class EnvEntry(BaseModel):
    key: str
    value_masked: str
    is_secret: bool


class EnvOut(BaseModel):
    path: str
    entries: list[EnvEntry]


class EnvPut(BaseModel):
    value: str = Field(..., description="Plain-text value; will be written verbatim")


def _parse_env(text: str) -> list[_EnvLine]:
    out: list[_EnvLine] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(_EnvLine(key=None, value="", raw=raw))
            continue
        k, _, v = raw.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out.append(_EnvLine(key=k, value=v, raw=raw))
    return out


@router.get("/env", response_model=EnvOut)
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def get_env(request: Request) -> EnvOut:
    path = settings.ENV_PATH
    if not path.exists():
        return EnvOut(path=str(path), entries=[])
    parsed = _parse_env(path.read_text(encoding="utf-8"))
    entries: list[EnvEntry] = []
    for ln in parsed:
        if ln.key is None:
            continue
        secret = _is_secret(ln.key)
        entries.append(
            EnvEntry(
                key=ln.key,
                value_masked=_mask(ln.value) if secret else ln.value,
                is_secret=secret,
            )
        )
    return EnvOut(path=str(path), entries=entries)


@router.put("/env/{key}", response_model=EnvEntry)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def put_env(request: Request, key: str, payload: EnvPut) -> EnvEntry:
    # Guard against line injection into /etc/tiktok/environment: a newline in
    # the value (or a newline/`=` in the key) would forge extra KEY=VALUE lines.
    if "\n" in key or "\r" in key or "=" in key:
        raise HTTPException(422, detail="key may not contain '=', newline, or CR")
    if "\n" in payload.value or "\r" in payload.value:
        raise HTTPException(422, detail="value may not contain newlines")
    path = settings.ENV_PATH
    if not path.exists():
        raise HTTPException(404, detail=f"{path} missing")
    parsed = _parse_env(path.read_text(encoding="utf-8"))
    hit = False
    for i, ln in enumerate(parsed):
        if ln.key == key:
            parsed[i] = _EnvLine(key=key, value=payload.value, raw=f"{key}={payload.value}")
            hit = True
            break
    if not hit:
        # Append at end, keeping trailing newline hygiene.
        parsed.append(_EnvLine(key=key, value=payload.value, raw=f"{key}={payload.value}"))
    tmp_fd, tmp_path_s = tempfile.mkstemp(
        prefix=".env-", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_s)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            for ln in parsed:
                f.write(ln.raw + "\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    log.info(".env key %r updated", key)
    secret = _is_secret(key)
    return EnvEntry(
        key=key,
        value_masked=_mask(payload.value) if secret else payload.value,
        is_secret=secret,
    )
