"""Schedule tab endpoints.

- `GET  /api/schedule/slots` — every known slot with defaults, current
  overrides, and the merged effective view.
- `PUT  /api/schedule/slots/{instance}` — write / clear overrides. A
  null value clears an override (revert to default). Time changes
  (`render_time` / `upload_time`) additionally invoke the root-owned
  helper `/usr/local/sbin/tiktok-slot-time-write` under `sudo -n`, so
  systemd picks the new schedule up immediately.

The helper handles all filesystem writes under /etc/systemd/system/ +
`daemon-reload` + timer restart. The webapp itself never touches
systemd or /etc directly. If the helper is not yet installed (e.g.
the operator upgraded the webapp but not the deploy tree), time-edit
requests return 503 with a hint.

Docs: [[improvements/schedule-tab]] + [[decisions/upload-display-api-crosscheck]]
for the sibling philosophy of "keep dangerous ops behind a tiny
whitelisted surface".
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from core.db import Db
from core.schedule import (
    DEFAULT_SLOTS,
    OVERRIDABLE_FIELDS,
    EffectiveSlotCfg,
    clear_all_overrides,
    config_key,
    effective_slot_cfg,
    known_instances,
    set_override,
)
from webapp.backend.deps import get_db

log = logging.getLogger("webapp.schedule")


router = APIRouter(prefix="/schedule", tags=["schedule"])


HELPER_PATH = "/usr/local/sbin/tiktok-slot-time-write"

_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_INSTANCE_RE = re.compile(r"^[0-9]{4}$")

# Fields that trigger a helper invocation on save. Behaviour toggles are
# just DB writes.
_TIME_FIELDS: frozenset[str] = frozenset({"render_time", "upload_time"})


# ---- Wire types -----------------------------------------------------------


class SlotView(BaseModel):
    """One entry in `GET /api/schedule/slots`. All fields are namespaced
    so the frontend can render a per-slot card without special-casing
    keys."""
    instance: str
    defaults: dict[str, Any]
    overrides: dict[str, Any]           # sparse — only rows present in DB
    effective: dict[str, Any]           # merged; what the runtime sees


class SlotsOut(BaseModel):
    slots: list[SlotView]
    helper_available: bool = Field(
        ..., description="True iff /usr/local/sbin/tiktok-slot-time-write is "
                        "executable — required for time edits."
    )


class OverrideIn(BaseModel):
    """PUT body. `overrides` maps `OVERRIDABLE_FIELDS` names to their new
    value; a value of `null` clears the override (revert to default)."""
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _check_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in v if k not in OVERRIDABLE_FIELDS]
        if bad:
            raise ValueError(f"unknown override keys: {bad}")
        return v


class PutResult(BaseModel):
    slot: SlotView
    applied_time_changes: list[str] = Field(
        default_factory=list,
        description="Which time fields were actually pushed to the root "
                    "helper. Empty on behavior-only edits.",
    )
    warnings: list[str] = Field(default_factory=list)


# ---- Helpers --------------------------------------------------------------


def _helper_available() -> bool:
    """The helper is only present after `install_systemd.sh install-helper`
    ran on the server. In dev it's typically absent."""
    return shutil.which(HELPER_PATH) is not None


def _slot_view(instance: str, db: Db) -> SlotView:
    base = DEFAULT_SLOTS[instance]
    defaults = {name: getattr(base, name) for name in OVERRIDABLE_FIELDS}
    overrides: dict[str, Any] = {}
    for name in OVERRIDABLE_FIELDS:
        raw = db.get_config(config_key(instance, name), "") or ""
        if raw != "":
            overrides[name] = raw          # keep as string for round-trip
    eff = effective_slot_cfg(instance, db)
    effective = {name: getattr(eff, name) for name in OVERRIDABLE_FIELDS}
    effective["instance"] = instance
    effective["publish_hour"] = eff.publish_hour
    return SlotView(
        instance=instance,
        defaults=defaults,
        overrides=overrides,
        effective=effective,
    )


def _validate_value(field_name: str, value: Any) -> Any:
    """Coerce + validate a payload value against its field's shape."""
    if field_name in _TIME_FIELDS:
        if not isinstance(value, str) or not _HHMM_RE.match(value):
            raise HTTPException(400, detail=f"{field_name} must be HH:MM (00:00..23:59)")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    raise HTTPException(400, detail=f"{field_name}: expected bool, got {value!r}")


def _validate_time_pair(render_time: str, upload_time: str) -> None:
    """upload_time must be at least 15 minutes AFTER render_time.
    Cross-midnight aware: a slot with render at 23:30 and upload at
    00:00 the next day is a 30-min gap, not a -1410-min gap.
    Rejecting equality avoids racing systemd against the render
    pipeline finishing."""
    def _mins(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    render_m = _mins(render_time)
    upload_m = _mins(upload_time)
    if upload_m <= render_m:
        upload_m += 24 * 60  # next-day upload
    if upload_m - render_m < 15:
        raise HTTPException(
            400,
            detail=(
                f"upload_time {upload_time} must be at least 15 minutes "
                f"after render_time {render_time} (needs render pipeline "
                f"headroom)"
            ),
        )


def _push_time_via_helper(instance: str, kind: Literal["render", "upload"],
                          hhmm: str | None) -> None:
    """Invoke the root helper. `hhmm=None` clears the override."""
    arg = "clear" if hhmm is None else hhmm
    cmd = ["sudo", "-n", HELPER_PATH, "set-time", instance, kind, arg]
    log.info("schedule: %s", cmd)
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True,
                           timeout=30)
    except FileNotFoundError as e:
        raise HTTPException(503, detail=f"root helper missing: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(504, detail="helper timed out (30s)") from e
    if r.returncode != 0:
        raise HTTPException(
            500,
            detail=f"helper exit={r.returncode}: {r.stderr.strip() or r.stdout.strip()}",
        )


# ---- Endpoints ------------------------------------------------------------


@router.get("/slots", response_model=SlotsOut)
def get_slots(db: Db = Depends(get_db)) -> SlotsOut:
    return SlotsOut(
        slots=[_slot_view(inst, db) for inst in known_instances(db)],
        helper_available=_helper_available(),
    )


@router.put("/slots/{instance}", response_model=PutResult)
def put_slot(instance: str, payload: OverrideIn,
             db: Db = Depends(get_db)) -> PutResult:
    if not _INSTANCE_RE.match(instance):
        raise HTTPException(400, detail="instance must be 4 digits")
    if instance not in DEFAULT_SLOTS:
        raise HTTPException(404, detail=f"unknown slot {instance!r}")

    warnings: list[str] = []

    # Split behaviour toggles from time edits. Time edits need extra
    # validation (render < upload) and go through the root helper.
    time_changes: dict[str, Any] = {}
    bool_changes: dict[str, Any] = {}
    for name, value in payload.overrides.items():
        if value is None:
            # Clear — treat as "revert to default". For time fields we
            # also tell the helper to clear the drop-in.
            (time_changes if name in _TIME_FIELDS else bool_changes)[name] = None
            continue
        coerced = _validate_value(name, value)
        (time_changes if name in _TIME_FIELDS else bool_changes)[name] = coerced

    # If time changes are involved, evaluate the resulting (render, upload)
    # pair against effective view to enforce ordering. Never let a save
    # succeed that puts upload before render + 15min.
    if time_changes:
        current = effective_slot_cfg(instance, db)
        defaults = DEFAULT_SLOTS[instance]

        def _resolve(field_name: str, current_value: str, default_value: str) -> str:
            """Pick the value the ordering check should use: the incoming
            payload's HH:MM, or (on clear) the default, or (on no change)
            the current effective value."""
            if field_name not in time_changes:
                return current_value
            requested = time_changes[field_name]
            return default_value if requested is None else requested

        render_final = _resolve("render_time", current.render_time, defaults.render_time)
        upload_final = _resolve("upload_time", current.upload_time, defaults.upload_time)
        _validate_time_pair(render_final, upload_final)

        if not _helper_available():
            raise HTTPException(
                503,
                detail=(
                    f"time edits require {HELPER_PATH} (install with "
                    f"`sudo bash scripts/install_systemd.sh install-helper`)"
                ),
            )

    # Persist behaviour toggles first — DB-only, fast, reversible.
    for name, value in bool_changes.items():
        set_override(db, instance, name, value)

    # Then push time changes to systemd via the helper. Persist the DB
    # override AFTER the helper succeeds so a helper failure leaves the
    # DB in the previous state.
    applied: list[str] = []
    for name, value in time_changes.items():
        kind: Literal["render", "upload"] = (
            "render" if name == "render_time" else "upload"
        )
        _push_time_via_helper(instance, kind, value)
        set_override(db, instance, name, value)
        applied.append(name)

    return PutResult(
        slot=_slot_view(instance, db),
        applied_time_changes=applied,
        warnings=warnings,
    )


@router.post("/slots/{instance}/reset", response_model=PutResult)
def reset_slot(instance: str, db: Db = Depends(get_db)) -> PutResult:
    """Nuke every override for the slot AND clear both timer drop-ins so
    the base .timer files' OnCalendar= take over. Big-hammer 'revert to
    factory' for a single slot."""
    if instance not in DEFAULT_SLOTS:
        raise HTTPException(404, detail=f"unknown slot {instance!r}")

    applied: list[str] = []
    if _helper_available():
        _push_time_via_helper(instance, "render", None)
        _push_time_via_helper(instance, "upload", None)
        applied.extend(["render_time", "upload_time"])
    clear_all_overrides(db, instance)

    warnings: list[str] = []
    if not _helper_available():
        warnings.append(
            f"{HELPER_PATH} missing — cleared DB overrides but any existing "
            "systemd drop-ins were left in place"
        )
    return PutResult(
        slot=_slot_view(instance, db),
        applied_time_changes=applied,
        warnings=warnings,
    )
