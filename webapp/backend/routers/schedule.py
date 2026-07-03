"""Schedule tab endpoints.

- `GET    /api/schedule/slots`             — every known slot with defaults,
  current overrides, and the merged effective view.
- `POST   /api/schedule/slots`             — create a new slot
  (dynamic; invokes helper `add-slot`).
- `PUT    /api/schedule/slots/{instance}`  — edit an existing slot's times,
  auto_approve, or behavior / notification overrides. Time edits go through
  the root helper.
- `POST   /api/schedule/slots/{instance}/reset` — clear every behavior /
  notification override (times + auto_approve stay on the slot row).
- `DELETE /api/schedule/slots/{instance}`  — remove the slot entirely
  (invokes helper `delete-slot`, wipes config keys + manifest, DMs
  Telegram about any orphaned pending posts).

The helper handles all filesystem writes under `/etc/systemd/system/` +
`daemon-reload` + timer enable/disable. The webapp itself never touches
systemd or `/etc` directly. If the helper is not yet installed, time
edits + create + delete return 503 with a hint.

Behavior toggles + notification toggles live in the `config` k/v table
under `schedule.slot.<inst>.<field>`. Times + auto_approve live on the
slot row itself (`slots` table). See `core.schedule.effective_slot_cfg`.

Docs: [[improvements/schedule-tab]] + [[decisions/schedule-tab-ui]] +
[[decisions/upload-display-api-crosscheck]] for the sibling philosophy
of "keep dangerous ops behind a tiny whitelisted surface".
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from core.db import Db, SlotRow
from core.schedule import (
    OVERRIDABLE_FIELDS,
    SLOT_ROW_FIELDS,
    clear_all_overrides,
    config_key,
    defaults_for,
    delete_slot as _core_delete_slot,
    effective_slot_cfg,
    known_instances,
    set_override,
)
from core.notify import Notifier, NotifierError
from webapp.backend import settings
from webapp.backend.deps import get_db
from webapp.backend.rate_limit import limiter

log = logging.getLogger("webapp.schedule")


router = APIRouter(prefix="/schedule", tags=["schedule"])


HELPER_PATH = "/usr/local/sbin/tiktok-slot-time-write"

_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_INSTANCE_RE = re.compile(r"^[0-9]{4}$")

# Fields the PUT body can carry. Times + auto_approve update the slots
# row; everything else in `OVERRIDABLE_FIELDS` updates the config table.
PUT_FIELDS: frozenset[str] = frozenset(SLOT_ROW_FIELDS) | frozenset(OVERRIDABLE_FIELDS)
_TIME_FIELDS: frozenset[str] = frozenset({"render_time", "upload_time"})

_MANIFEST_DIR = Path("data/slots")


# ---- Wire types -----------------------------------------------------------


class SlotView(BaseModel):
    """One entry in `GET /api/schedule/slots`."""
    instance: str
    defaults: dict[str, Any]            # derived from slots row + defaults_for
    overrides: dict[str, Any]           # sparse — only config rows present
    effective: dict[str, Any]           # merged; what the runtime sees


class SlotsOut(BaseModel):
    slots: list[SlotView]
    helper_available: bool = Field(
        ..., description="True iff /usr/local/sbin/tiktok-slot-time-write is "
                        "executable — required for time edits + slot "
                        "creation + deletion."
    )


class CreateSlotIn(BaseModel):
    """POST body."""
    instance: str
    render_time: str
    upload_time: str
    auto_approve: bool = False


class UpdateSlotIn(BaseModel):
    """PUT body. `overrides` maps field names to their new value; a value
    of `null` clears a behavior / notification override (revert to
    default). Times + `auto_approve` do NOT accept null — the slot row
    itself is the source of truth."""
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _check_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = [k for k in v if k not in PUT_FIELDS]
        if bad:
            raise ValueError(f"unknown update keys: {bad}")
        return v


class PutResult(BaseModel):
    slot: SlotView
    applied_time_changes: list[str] = Field(
        default_factory=list,
        description="Which time fields were actually pushed to the root "
                    "helper. Empty on behavior-only edits.",
    )
    warnings: list[str] = Field(default_factory=list)


class DeleteResult(BaseModel):
    instance: str
    manifest_wiped: bool
    orphan_post_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---- Helpers --------------------------------------------------------------


def _helper_available() -> bool:
    return shutil.which(HELPER_PATH) is not None


def _slot_view(instance: str, db: Db) -> SlotView:
    row = db.get_slot(instance)
    if row is None:
        # Should never happen if callers gate on known_instances, but the
        # response shape needs *something* deterministic.
        raise HTTPException(404, detail=f"unknown slot {instance!r}")

    base = defaults_for(
        instance=instance,
        render_time=row.render_time,
        upload_time=row.upload_time,
        auto_approve=row.auto_approve,
    )
    defaults: dict[str, Any] = {}
    for name in SLOT_ROW_FIELDS:
        defaults[name] = getattr(base, name)
    for name in OVERRIDABLE_FIELDS:
        defaults[name] = getattr(base, name)

    overrides: dict[str, Any] = {}
    for name in OVERRIDABLE_FIELDS:
        raw = db.get_config(config_key(instance, name), "") or ""
        if raw != "":
            overrides[name] = raw

    eff = effective_slot_cfg(instance, db)
    effective: dict[str, Any] = {
        "instance": instance,
        "publish_hour": eff.publish_hour,
    }
    for name in SLOT_ROW_FIELDS:
        effective[name] = getattr(eff, name)
    for name in OVERRIDABLE_FIELDS:
        effective[name] = getattr(eff, name)

    return SlotView(
        instance=instance,
        defaults=defaults,
        overrides=overrides,
        effective=effective,
    )


def _coerce_bool(field_name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    raise HTTPException(400, detail=f"{field_name}: expected bool, got {value!r}")


def _validate_hhmm(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise HTTPException(400, detail=f"{field_name} must be HH:MM (00:00..23:59)")
    return value


def _validate_time_pair(render_time: str, upload_time: str) -> None:
    """upload_time must be at least 15 minutes after render_time.
    Cross-midnight aware: render 23:30 + upload 00:00 next day = 30min."""
    def _mins(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    render_m = _mins(render_time)
    upload_m = _mins(upload_time)
    if upload_m <= render_m:
        upload_m += 24 * 60
    if upload_m - render_m < 15:
        raise HTTPException(
            400,
            detail=(
                f"upload_time {upload_time} must be at least 15 minutes "
                f"after render_time {render_time} (needs render pipeline "
                f"headroom)"
            ),
        )


def _push_via_helper(argv_tail: list[str]) -> None:
    """Invoke the root helper with the given tail (already validated by
    the caller — the helper re-validates too). Raises HTTPException on
    failure."""
    cmd = ["sudo", "-n", HELPER_PATH] + argv_tail
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


def _push_set_time(instance: str, kind: Literal["render", "upload"],
                   hhmm: str) -> None:
    _push_via_helper(["set-time", instance, kind, hhmm])


def _push_add_slot(instance: str, render_time: str, upload_time: str) -> None:
    _push_via_helper(["add-slot", instance, render_time, upload_time])


def _push_delete_slot(instance: str) -> None:
    _push_via_helper(["delete-slot", instance])


def _wipe_manifest(instance: str) -> bool:
    """Best-effort delete of `data/slots/<inst>.json`. Returns True if a
    manifest was found + removed."""
    path = _MANIFEST_DIR / f"{instance}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError as e:
        log.warning("could not remove manifest %s: %s", path, e)
        return False


def _notify_orphans(instance: str, orphan_post_ids: list[str]) -> None:
    """Fire a single Telegram DM listing post_ids that were pending for a
    slot that just got deleted. Best-effort; logged on failure."""
    if not orphan_post_ids:
        return
    try:
        notifier = Notifier.from_env()
    except NotifierError:
        return
    body = (
        f"🗑 <b>Slot {instance} deleted</b>\n"
        f"{len(orphan_post_ids)} pending post"
        f"{'s' if len(orphan_post_ids) != 1 else ''} orphaned:\n"
        + "\n".join(f"<code>{pid}</code>" for pid in orphan_post_ids)
        + "\nApprove or reject via /queue."
    )
    try:
        notifier.send_text(body, parse_mode="HTML")
    except NotifierError as e:
        log.warning("orphan-post notify failed: %s", e)


# ---- Endpoints ------------------------------------------------------------


@router.get("/slots", response_model=SlotsOut)
@limiter.limit(settings.RATE_LIMIT_READ_DEFAULT)
def get_slots(request: Request, db: Db = Depends(get_db)) -> SlotsOut:
    return SlotsOut(
        slots=[_slot_view(inst, db) for inst in known_instances(db)],
        helper_available=_helper_available(),
    )


@router.post("/slots", response_model=PutResult, status_code=201)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def create_slot(request: Request, payload: CreateSlotIn, db: Db = Depends(get_db)) -> PutResult:
    inst = payload.instance
    if not _INSTANCE_RE.match(inst):
        raise HTTPException(400, detail="instance must be 4 digits")
    if db.get_slot(inst) is not None:
        raise HTTPException(409, detail=f"slot {inst!r} already exists")

    render_time = _validate_hhmm("render_time", payload.render_time)
    upload_time = _validate_hhmm("upload_time", payload.upload_time)
    _validate_time_pair(render_time, upload_time)

    if not _helper_available():
        raise HTTPException(
            503,
            detail=(
                f"slot creation requires {HELPER_PATH} (install with "
                f"`sudo bash scripts/install_systemd.sh install-helper`)"
            ),
        )

    # Order matters: systemd side FIRST so a rollback is DB-only (no orphan
    # drop-in / enabled timer). If the helper fails the row is never
    # written and the operator retries.
    _push_add_slot(inst, render_time, upload_time)
    try:
        db.add_slot(inst, render_time, upload_time, payload.auto_approve)
    except Exception as e:
        # Roll back the systemd side so we don't leave a dangling timer.
        log.warning("db.add_slot failed for %s (%s); rolling back helper", inst, e)
        try:
            _push_delete_slot(inst)
        except Exception as roll_e:
            log.error("rollback delete-slot failed for %s: %s", inst, roll_e)
        raise HTTPException(500, detail=f"add_slot failed: {e}") from e

    return PutResult(
        slot=_slot_view(inst, db),
        applied_time_changes=["render_time", "upload_time"],
        warnings=[],
    )


@router.put("/slots/{instance}", response_model=PutResult)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def put_slot(request: Request, instance: str, payload: UpdateSlotIn,
             db: Db = Depends(get_db)) -> PutResult:
    if not _INSTANCE_RE.match(instance):
        raise HTTPException(400, detail="instance must be 4 digits")
    if db.get_slot(instance) is None:
        raise HTTPException(404, detail=f"unknown slot {instance!r}")

    # Split the update by target: slots row (times / auto_approve) vs
    # config table (behavior / notifs).
    row_time_changes: dict[str, str] = {}
    row_auto_change: bool | None = None
    bool_changes: dict[str, Any] = {}

    for name, value in payload.overrides.items():
        if name in _TIME_FIELDS:
            if value is None:
                raise HTTPException(
                    400,
                    detail=(
                        f"{name} cannot be null on a slot row — send an "
                        "HH:MM value or use POST /slots to recreate"
                    ),
                )
            row_time_changes[name] = _validate_hhmm(name, value)
        elif name == "auto_approve":
            if value is None:
                raise HTTPException(
                    400,
                    detail="auto_approve cannot be null on a slot row",
                )
            row_auto_change = _coerce_bool(name, value)
        elif name in OVERRIDABLE_FIELDS:
            if value is None:
                bool_changes[name] = None
            else:
                bool_changes[name] = _coerce_bool(name, value)

    # Enforce the 15-min gap on the resulting (render, upload) pair.
    if row_time_changes:
        current = effective_slot_cfg(instance, db)
        render_final = row_time_changes.get("render_time", current.render_time)
        upload_final = row_time_changes.get("upload_time", current.upload_time)
        _validate_time_pair(render_final, upload_final)
        if not _helper_available():
            raise HTTPException(
                503,
                detail=(
                    f"time edits require {HELPER_PATH} (install with "
                    f"`sudo bash scripts/install_systemd.sh install-helper`)"
                ),
            )

    # Config-table edits first (cheap, reversible).
    for name, value in bool_changes.items():
        set_override(db, instance, name, value)

    # auto_approve: DB row only (no systemd side effect — behavior toggle
    # under our control).
    if row_auto_change is not None:
        db.update_slot_auto_approve(instance, row_auto_change)

    # Time edits: helper first, then persist. If helper fails the DB row
    # stays intact and the operator retries.
    applied: list[str] = []
    if row_time_changes:
        # Push each kind that actually changed vs. the current DB row so
        # we don't restart a timer whose OnCalendar is already correct.
        current_row = db.get_slot(instance)
        assert current_row is not None
        for name, new_value in row_time_changes.items():
            kind: Literal["render", "upload"] = (
                "render" if name == "render_time" else "upload"
            )
            existing = (current_row.render_time if kind == "render"
                        else current_row.upload_time)
            if existing == new_value:
                continue
            _push_set_time(instance, kind, new_value)
            applied.append(name)
        # Persist the new times to the slots row after both helper calls
        # succeeded so we never write a row that disagrees with the
        # installed drop-in.
        new_render = row_time_changes.get("render_time", current_row.render_time)
        new_upload = row_time_changes.get("upload_time", current_row.upload_time)
        db.update_slot_times(instance, new_render, new_upload)

    return PutResult(
        slot=_slot_view(instance, db),
        applied_time_changes=applied,
        warnings=[],
    )


@router.post("/slots/{instance}/reset", response_model=PutResult)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def reset_slot(request: Request, instance: str, db: Db = Depends(get_db)) -> PutResult:
    """Clear every behavior / notification override for the slot. Times +
    auto_approve stay on the slot row (they ARE the defaults in the new
    model; to reset those, DELETE + POST-recreate)."""
    if db.get_slot(instance) is None:
        raise HTTPException(404, detail=f"unknown slot {instance!r}")
    clear_all_overrides(db, instance)
    return PutResult(
        slot=_slot_view(instance, db),
        applied_time_changes=[],
        warnings=[],
    )


@router.delete("/slots/{instance}", response_model=DeleteResult)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def delete_slot(request: Request, instance: str, db: Db = Depends(get_db)) -> DeleteResult:
    """Remove a slot entirely. Helper stops+disables both timers and
    wipes drop-in dirs. Config keys + slots row deleted. Any pending
    manifest is removed and a Telegram DM lists orphaned post_ids so
    the operator can approve/reject them via /queue."""
    if not _INSTANCE_RE.match(instance):
        raise HTTPException(400, detail="instance must be 4 digits")
    if db.get_slot(instance) is None:
        raise HTTPException(404, detail=f"unknown slot {instance!r}")
    if not _helper_available():
        raise HTTPException(
            503,
            detail=(
                f"slot deletion requires {HELPER_PATH} (install with "
                f"`sudo bash scripts/install_systemd.sh install-helper`)"
            ),
        )

    warnings: list[str] = []

    # Read the manifest before wiping so we can DM the orphan post_ids.
    orphan_ids: list[str] = []
    manifest_path = _MANIFEST_DIR / f"{instance}.json"
    if manifest_path.exists():
        try:
            import json
            payload = json.loads(manifest_path.read_text())
            post_id = payload.get("post_id")
            if isinstance(post_id, str) and post_id:
                orphan_ids.append(post_id)
        except (OSError, ValueError) as e:
            warnings.append(f"could not read manifest: {e}")

    # Systemd side first: if it fails the DB row + config keys are
    # preserved and the operator retries.
    _push_delete_slot(instance)

    # DB side.
    _core_delete_slot(db, instance)

    # Manifest + orphan DM (best-effort).
    manifest_wiped = _wipe_manifest(instance)
    _notify_orphans(instance, orphan_ids)

    return DeleteResult(
        instance=instance,
        manifest_wiped=manifest_wiped,
        orphan_post_ids=orphan_ids,
        warnings=warnings,
    )
