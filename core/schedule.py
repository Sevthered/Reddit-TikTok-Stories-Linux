"""Per-slot effective configuration.

Runtime consumers (`scripts/slot.py`, `pipeline/upload_worker.py`,
`webapp/backend/routers/schedule.py`) call `effective_slot_cfg(inst, db)`
to get the merged view a slot's render / upload paths act on.

Model:
- `slots` table (`core/db.py::SlotRow`) is the source of truth for the
  set of slots plus their `render_time` / `upload_time` / `auto_approve`.
- Behavior + notification defaults are computed from a shared profile
  keyed off `auto_approve` (see `defaults_for`) so an operator can add
  a new interactive or auto-approve slot without having to seed a full
  row of DB overrides.
- `config` table k/v under `schedule.slot.<inst>.<field>` holds per-slot
  overrides on top of those defaults; behavior toggles + notification
  toggles round-trip through the Schedule tab this way.

Zero DB overrides + a slot's default profile = byte-identical to the
pre-Schedule-tab behavior. Deleting a config row reverts a single field
to the profile default.

Docs / trigger for the redesign: [[improvements/schedule-tab]] +
[[decisions/instrument-console-redesign]].
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from core.db import Db, SlotRow

log = logging.getLogger("core.schedule")


# ---- Static defaults ----------------------------------------------------


@dataclass(frozen=True)
class SlotDefaults:
    """The "factory defaults" for a slot with a given auto_approve profile.
    Not per-instance any more; computed on demand from `defaults_for`."""
    publish_hour: int
    render_time: str          # HH:MM Madrid — mirrors slots.render_time
    upload_time: str          # HH:MM Madrid — mirrors slots.upload_time
    auto_approve: bool

    render_enabled: bool = True
    upload_enabled: bool = True

    # Post-da5ac9a matrix. `notify_render_empty` + `notify_upload_gate_reject`
    # default off; the rest depend on `auto_approve` (see defaults_for).
    notify_render_pre: bool = True
    notify_render_crash: bool = True
    notify_render_empty: bool = False
    notify_upload_approval_card: bool = True
    notify_upload_force_approve: bool = True
    notify_upload_success: bool = True
    notify_upload_failure: bool = True
    notify_upload_gate_reject: bool = False


def defaults_for(
    instance: str,
    render_time: str,
    upload_time: str,
    auto_approve: bool,
) -> SlotDefaults:
    """Compute the SlotDefaults for a slot. Auto-approve slots suppress
    pre-render + approval-card + force-approve DMs (they never fire in
    code anyway when auto_approve is on); interactive slots keep them.
    `publish_hour` is derived from `upload_time`."""
    publish_hour = int(upload_time.split(":")[0])
    if auto_approve:
        return SlotDefaults(
            publish_hour=publish_hour,
            render_time=render_time,
            upload_time=upload_time,
            auto_approve=True,
            notify_render_pre=False,
            notify_upload_approval_card=False,
            notify_upload_force_approve=False,
        )
    return SlotDefaults(
        publish_hour=publish_hour,
        render_time=render_time,
        upload_time=upload_time,
        auto_approve=False,
        notify_render_pre=True,
        notify_upload_approval_card=True,
        notify_upload_force_approve=True,
    )


# ---- Effective view (defaults + DB overrides) ---------------------------


@dataclass(frozen=True)
class EffectiveSlotCfg:
    """What the runtime actually acts on."""
    instance: str
    publish_hour: int
    render_time: str
    upload_time: str

    render_enabled: bool
    upload_enabled: bool
    auto_approve: bool

    notify_render_pre: bool
    notify_render_crash: bool
    notify_render_empty: bool
    notify_upload_approval_card: bool
    notify_upload_force_approve: bool
    notify_upload_success: bool
    notify_upload_failure: bool
    notify_upload_gate_reject: bool


# Overridable behavior + notification fields (times + auto_approve now live
# in the slots table itself, not as config overrides). Order matches the
# Schedule tab wire format.
OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "render_enabled",
    "upload_enabled",
    "notify_render_pre",
    "notify_render_crash",
    "notify_render_empty",
    "notify_upload_approval_card",
    "notify_upload_force_approve",
    "notify_upload_success",
    "notify_upload_failure",
    "notify_upload_gate_reject",
)

_BOOL_FIELDS: frozenset[str] = frozenset(OVERRIDABLE_FIELDS)


# Fields exposed through the Schedule tab that live on the slot row itself
# (updated via `core.db.Db.update_slot_*` rather than the config table).
SLOT_ROW_FIELDS: tuple[str, ...] = (
    "render_time",
    "upload_time",
    "auto_approve",
)


def config_key(instance: str, field_name: str) -> str:
    """DB key for a per-slot override. Kept as a single function so the
    router and the merge helper never disagree on the format."""
    return f"schedule.slot.{instance}.{field_name}"


def _coerce(field_name: str, raw: str) -> Any:
    if field_name in _BOOL_FIELDS:
        return raw == "1"
    return raw


def _serialize(field_name: str, value: Any) -> str:
    if field_name in _BOOL_FIELDS:
        return "1" if bool(value) else "0"
    return str(value)


def known_instances(db: Db) -> list[str]:
    """Which slots the runtime should schedule. Reads from `slots` table."""
    return [s.instance for s in db.list_slots()]


def effective_slot_cfg(instance: str, db: Db) -> EffectiveSlotCfg:
    """Merge the slot row + defaults profile with per-slot DB overrides.

    Raises `KeyError` if the instance is not in the slots table so the
    argparse / router callers surface a clear error rather than a silent
    fall-through to a fictitious default."""
    row: SlotRow | None = db.get_slot(instance)
    if row is None:
        raise KeyError(f"unknown slot instance {instance!r} — "
                       f"known: {known_instances(db)}")

    base = defaults_for(
        instance=instance,
        render_time=row.render_time,
        upload_time=row.upload_time,
        auto_approve=row.auto_approve,
    )

    merged: dict[str, Any] = asdict(base)
    for field_name in OVERRIDABLE_FIELDS:
        raw = db.get_config(config_key(instance, field_name), "") or ""
        if raw == "":
            continue
        try:
            merged[field_name] = _coerce(field_name, raw)
        except Exception as exc:
            log.warning("bad override for %s.%s: %r (%s) — falling back to default",
                        instance, field_name, raw, exc)

    return EffectiveSlotCfg(
        instance=instance,
        publish_hour=merged["publish_hour"],
        render_time=merged["render_time"],
        upload_time=merged["upload_time"],
        render_enabled=merged["render_enabled"],
        upload_enabled=merged["upload_enabled"],
        auto_approve=merged["auto_approve"],
        notify_render_pre=merged["notify_render_pre"],
        notify_render_crash=merged["notify_render_crash"],
        notify_render_empty=merged["notify_render_empty"],
        notify_upload_approval_card=merged["notify_upload_approval_card"],
        notify_upload_force_approve=merged["notify_upload_force_approve"],
        notify_upload_success=merged["notify_upload_success"],
        notify_upload_failure=merged["notify_upload_failure"],
        notify_upload_gate_reject=merged["notify_upload_gate_reject"],
    )


def set_override(db: Db, instance: str, field_name: str, value: Any | None) -> None:
    """Write or clear a single behavior / notification override. `value=None`
    clears the row (revert to default profile). Time + auto_approve edits
    go through `core.db.Db.update_slot_*` instead — see the router."""
    if db.get_slot(instance) is None:
        raise KeyError(f"unknown slot {instance!r}")
    if field_name not in OVERRIDABLE_FIELDS:
        raise ValueError(f"field {field_name!r} is not overridable via "
                         "config (times + auto_approve live on the slots "
                         "row itself)")
    key = config_key(instance, field_name)
    if value is None:
        db.set_config(key, "")
    else:
        db.set_config(key, _serialize(field_name, value))


def clear_all_overrides(db: Db, instance: str) -> None:
    """Reset a slot's behavior + notification config to defaults. Iterates
    OVERRIDABLE_FIELDS rather than scanning the config table so we can't
    accidentally nuke unrelated keys."""
    for field_name in OVERRIDABLE_FIELDS:
        db.set_config(config_key(instance, field_name), "")


def delete_slot(db: Db, instance: str) -> None:
    """Purge a slot: remove the slots row AND every `schedule.slot.<inst>.*`
    config key. Does NOT touch systemd — the router owns the helper call
    order (systemd cleanup happens before this, so if it fails the DB row
    stays intact and the operator can retry)."""
    clear_all_overrides(db, instance)
    db.delete_slot(instance)


__all__ = [
    "SlotDefaults", "EffectiveSlotCfg",
    "OVERRIDABLE_FIELDS", "SLOT_ROW_FIELDS",
    "config_key", "defaults_for", "known_instances",
    "effective_slot_cfg", "set_override", "clear_all_overrides",
    "delete_slot",
]
