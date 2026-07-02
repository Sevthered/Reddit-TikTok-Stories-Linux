"""Per-slot effective configuration.

`SlotDefaults` mirrors what was previously hard-coded in
`scripts/slot.py::SLOTS`. `EffectiveSlotCfg` is the merged view the
runtime consumes — defaults with any per-key `schedule.slot.<inst>.*`
override applied on top. Zero rows in the `config` table means the
effective view is byte-identical to the pre-schedule-tab behavior; a
delete of a key reverts it to the default.

Everything read here maps to a single DB config row so the webapp
Schedule tab can round-trip values without touching TOML.

Docs / trigger to write this: [[improvements/schedule-tab]].
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from core.db import Db

log = logging.getLogger("core.schedule")


# ---- Static defaults (previously scripts/slot.py::SLOTS) -----------------


@dataclass(frozen=True)
class SlotDefaults:
    """Hard-coded per-instance baseline. Preserved so the webapp can show
    'default' vs 'override' and so a slot with no DB rows behaves exactly
    as it did before the Schedule tab shipped."""
    publish_hour: int
    render_time: str          # "HH:MM" Madrid — used by phase 3+ time editor
    upload_time: str          # "HH:MM" Madrid
    auto_approve: bool

    # Notification defaults per event. Post-da5ac9a matrix:
    #  ✅ crash, ✅ approval_card (interactive only), ✅ force_approve
    #  (interactive only), ✅ success, ✅ failure
    #  🚫 empty (silenced by da5ac9a)
    #  🚫 gate_reject (opt-in)
    notify_render_pre: bool
    notify_render_crash: bool = True
    notify_render_empty: bool = False
    notify_upload_approval_card: bool = True
    notify_upload_force_approve: bool = True
    notify_upload_success: bool = True
    notify_upload_failure: bool = True
    notify_upload_gate_reject: bool = False

    render_enabled: bool = True
    upload_enabled: bool = True


# Mirrors the shape scripts/slot.py::SLOTS used before phase 1. The
# render_time / upload_time strings match deploy/systemd/tiktok-slot-*.timer
# `OnCalendar=` values so the eventual time editor round-trips cleanly.
DEFAULT_SLOTS: dict[str, SlotDefaults] = {
    "0000": SlotDefaults(
        publish_hour=0,
        render_time="23:30",
        upload_time="00:00",
        auto_approve=True,
        notify_render_pre=False,
        # 0000 is unattended: approval_card / force_approve never fire in
        # code anyway (auto_approve short-circuits), but explicitly default
        # them off so the webapp shows an intuitive picture.
        notify_upload_approval_card=False,
        notify_upload_force_approve=False,
    ),
    "1200": SlotDefaults(
        publish_hour=12,
        render_time="11:30",
        upload_time="12:00",
        auto_approve=False,
        notify_render_pre=True,
    ),
}


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


# All keys the Schedule tab can override. Kept in one place so the router
# and the merge helper agree on the wire format.
OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "render_time",
    "upload_time",
    "render_enabled",
    "upload_enabled",
    "auto_approve",
    "notify_render_pre",
    "notify_render_crash",
    "notify_render_empty",
    "notify_upload_approval_card",
    "notify_upload_force_approve",
    "notify_upload_success",
    "notify_upload_failure",
    "notify_upload_gate_reject",
)

_BOOL_FIELDS: frozenset[str] = frozenset({
    "render_enabled", "upload_enabled", "auto_approve",
    "notify_render_pre", "notify_render_crash", "notify_render_empty",
    "notify_upload_approval_card", "notify_upload_force_approve",
    "notify_upload_success", "notify_upload_failure",
    "notify_upload_gate_reject",
})


def config_key(instance: str, field_name: str) -> str:
    """DB key for a per-slot override. Kept as a single function so the
    router and the merge helper never disagree on the format."""
    return f"schedule.slot.{instance}.{field_name}"


def _coerce(field_name: str, raw: str) -> Any:
    """Parse a raw DB string into the right Python type for the field."""
    if field_name in _BOOL_FIELDS:
        return raw == "1"
    # render_time / upload_time stay as HH:MM strings — no coercion.
    return raw


def _serialize(field_name: str, value: Any) -> str:
    if field_name in _BOOL_FIELDS:
        return "1" if bool(value) else "0"
    return str(value)


def known_instances(db: Db) -> list[str]:
    """Which slots the runtime should schedule. Phase 1+2: static list from
    DEFAULT_SLOTS. Phase 5 will make this discovery-based (DB slot table)."""
    return sorted(DEFAULT_SLOTS.keys())


def effective_slot_cfg(instance: str, db: Db) -> EffectiveSlotCfg:
    """Merge `DEFAULT_SLOTS[instance]` with any DB overrides.

    Raises `KeyError` if the instance is unknown — matches the previous
    `SLOTS[instance]` lookup that would `KeyError` on typos.
    """
    if instance not in DEFAULT_SLOTS:
        raise KeyError(f"unknown slot instance {instance!r} — "
                       f"known: {sorted(DEFAULT_SLOTS)}")
    base = DEFAULT_SLOTS[instance]

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
    """Write or clear a single override. `value=None` clears the row (revert
    to default). Used by the webapp Schedule tab router."""
    if instance not in DEFAULT_SLOTS:
        raise KeyError(f"unknown slot {instance!r}")
    if field_name not in OVERRIDABLE_FIELDS:
        raise ValueError(f"field {field_name!r} is not overridable")
    key = config_key(instance, field_name)
    if value is None:
        db.set_config(key, "")
    else:
        db.set_config(key, _serialize(field_name, value))


def clear_all_overrides(db: Db, instance: str) -> None:
    """Reset a slot to its factory defaults. Iterates OVERRIDABLE_FIELDS
    rather than scanning the config table so we can't nuke unrelated keys."""
    for field_name in OVERRIDABLE_FIELDS:
        db.set_config(config_key(instance, field_name), "")


__all__ = [
    "SlotDefaults", "EffectiveSlotCfg",
    "DEFAULT_SLOTS", "OVERRIDABLE_FIELDS",
    "config_key", "known_instances",
    "effective_slot_cfg", "set_override", "clear_all_overrides",
]
