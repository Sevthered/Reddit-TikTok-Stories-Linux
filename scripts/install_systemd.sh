#!/usr/bin/env bash
# Install / uninstall the tiktok-* systemd system units + polkit rule.
#
# Usage:
#   sudo bash scripts/install_systemd.sh install
#   sudo bash scripts/install_systemd.sh install-helper   # schedule helper + sudoers only
#   sudo bash scripts/install_systemd.sh migrate-slots    # convert concrete per-instance .timer files to templated @.timer + drop-ins
#   sudo bash scripts/install_systemd.sh uninstall
#   bash scripts/install_systemd.sh kickstart tiktok-webapp
#   bash scripts/install_systemd.sh status
#   bash scripts/install_systemd.sh security      # systemd-analyze security, all units (R3.2)
#
# The install target copies units under /etc/systemd/system/, the polkit
# rule under /etc/polkit-1/rules.d/, runs `systemctl daemon-reload`, and
# enables + starts the always-on units. Slot timers are templated
# (`tiktok-slot-{render,upload}@.timer`); each active instance is armed
# by a drop-in written by the root helper — see
# [[improvements/schedule-tab]].
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNITS_SRC="$REPO_ROOT/deploy/systemd"
POLKIT_SRC="$REPO_ROOT/deploy/polkit"
HELPER_SRC="$REPO_ROOT/deploy/root-helper"
SUDOERS_SRC="$REPO_ROOT/deploy/sudoers.d"
SYSTEMD_DIR="/etc/systemd/system"
POLKIT_DIR="/etc/polkit-1/rules.d"
HELPER_DIR="/usr/local/sbin"
SUDOERS_DIR="/etc/sudoers.d"

# tiktok-webapp deliberately omitted — kept installed but not enabled by
# default. Start on demand with `make web-up` (see Makefile).
PERSISTENT=(tiktok-xvfb tiktok-bot)
# Non-slot timers only. Slot timers are templated + enabled per-instance
# by the root helper (`add-slot`), not by this script.
TIMERS=(
  tiktok-confirm.timer
  tiktok-retention.timer
  tiktok-secrets-backup.timer
  tiktok-lynis-audit.timer
  tiktok-rkhunter-scan.timer
  tiktok-aide-check.timer
)

# Default seed slots — mirrors core/db.py::_SEED_SLOTS. Only used by
# `migrate-slots` when converting from concrete `.timer` files, or when
# an operator wants to re-seed after a wipe. Format: "instance:render_time:upload_time".
SEED_SLOTS=(
  "0000:23:30:00:00"
  "1200:11:30:12:00"
)

# All 10 service units, for `systemd-analyze security` (R3.2). Templated
# units need a concrete instance name — 0000 is always seeded; `x` is an
# arbitrary notify@ instance since it takes an argument, not a time.
UNITS_SECURITY=(
  tiktok-webapp.service
  tiktok-bot.service
  tiktok-confirm.service
  tiktok-retention.service
  tiktok-xvfb.service
  "tiktok-notify@x.service"
  "tiktok-slot-render@0000.service"
  "tiktok-slot-upload@0000.service"
  tiktok-upload.service
  tiktok-secrets-backup.service
)

need_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "This action needs root. Re-run with: sudo $0 $*" >&2
    exit 1
  fi
}

cmd_install() {
  need_root install
  install -m 0644 "$UNITS_SRC"/*.service "$SYSTEMD_DIR/"
  install -m 0644 "$UNITS_SRC"/*.timer   "$SYSTEMD_DIR/"
  install -m 0644 "$POLKIT_SRC"/50-tiktok.rules "$POLKIT_DIR/"
  # Schedule-tab root helper: writes systemd drop-in overrides for the
  # slot timers when the webapp changes fire times, creates + deletes
  # slots. Invoked via a narrowly-scoped sudoers entry, so both must
  # land together.
  install -m 0755 -o root -g root \
    "$HELPER_SRC/tiktok-slot-time-write" \
    "$HELPER_DIR/tiktok-slot-time-write"
  local sudoers_src="$SUDOERS_SRC/tiktok-slot-time-write"
  visudo -cf "$sudoers_src" >/dev/null
  install -m 0440 -o root -g root "$sudoers_src" \
    "$SUDOERS_DIR/tiktok-slot-time-write"
  systemctl daemon-reload
  for u in "${PERSISTENT[@]}"; do
    systemctl enable --now "$u.service"
  done
  for t in "${TIMERS[@]}"; do
    systemctl enable --now "$t"
  done
  echo "installed non-slot units + helper. Seed slots via:"
  echo "  sudo bash $0 migrate-slots"
  echo "or add them individually via the Schedule tab / helper."
  cmd_status
}

cmd_uninstall() {
  need_root uninstall
  # Any per-instance slot timers first (find any tiktok-slot-{render,upload}@X.timer symlinks).
  local sym
  while IFS= read -r sym; do
    local unit
    unit="$(basename "$sym")"
    systemctl disable --now "$unit" 2>/dev/null || true
  done < <(find "$SYSTEMD_DIR"/timers.target.wants -maxdepth 1 -name 'tiktok-slot-*.timer' 2>/dev/null || true)
  for t in "${TIMERS[@]}"; do
    systemctl disable --now "$t" 2>/dev/null || true
  done
  for u in "${PERSISTENT[@]}"; do
    systemctl disable --now "$u.service" 2>/dev/null || true
  done
  rm -f "$SYSTEMD_DIR"/tiktok-*.service "$SYSTEMD_DIR"/tiktok-*.timer
  rm -rf "$SYSTEMD_DIR"/tiktok-slot-*.timer.d
  rm -f "$POLKIT_DIR"/50-tiktok.rules
  rm -f "$HELPER_DIR/tiktok-slot-time-write"
  rm -f "$SUDOERS_DIR/tiktok-slot-time-write"
  systemctl daemon-reload
  echo "uninstalled."
}

cmd_status() {
  # Slot timers are dynamic — enumerate whatever's enabled today.
  local slot_timers
  slot_timers=$(find "$SYSTEMD_DIR"/timers.target.wants -maxdepth 1 -name 'tiktok-slot-*.timer' -exec basename {} \; 2>/dev/null | sort || true)
  systemctl --no-pager status "${PERSISTENT[@]/%/.service}" "${TIMERS[@]}" $slot_timers || true
}

cmd_install_helper() {
  need_root install-helper
  install -m 0755 -o root -g root \
    "$HELPER_SRC/tiktok-slot-time-write" \
    "$HELPER_DIR/tiktok-slot-time-write"
  local sudoers_src="$SUDOERS_SRC/tiktok-slot-time-write"
  visudo -cf "$sudoers_src" >/dev/null
  install -m 0440 -o root -g root "$sudoers_src" \
    "$SUDOERS_DIR/tiktok-slot-time-write"
  echo "installed schedule helper + sudoers snippet."
  echo "smoke test: sudo -n /usr/local/sbin/tiktok-slot-time-write"
}

# Convert an install that has concrete `tiktok-slot-{render,upload}@HHMM.timer`
# files under /etc/systemd/system/ into the templated + drop-in shape.
# Idempotent: rerunnable safely; concrete files that are already gone are
# skipped, and existing drop-ins are preserved.
#
# Safety ordering (revised 2026-07-02 after P5R.7 QA):
#   1. install/refresh the templated `@.timer` base files first
#   2. daemon-reload
#   3. write drop-ins for the seed slots (skip existing)
#   4. daemon-reload
#   5. **remove concrete per-instance .timer files BEFORE enable**
#      (systemctl enable would otherwise resolve to the concrete file
#      and create a symlink that dangles after step 5 removes the target)
#   6. daemon-reload (clears symlink refs to removed files)
#   7. enable the templated instances via `systemctl enable --now`
#      (with concrete gone, enable resolves to the templated base)
#   8. final daemon-reload
#
# Missing seed = SILENTLY SKIP a drop-in write for an instance whose
# concrete file doesn't exist. Existing drop-in for an instance = SKIPPED
# (operator may have edited the time). Blast radius: worst case an
# instance ends up with no drop-in and its timer never fires — the
# `list-timers` output surfaces this immediately.
cmd_migrate_slots() {
  need_root migrate-slots
  echo "== migrate-slots: convert concrete .timer files -> templated + drop-ins =="

  echo "1. install/refresh templated @.timer base files"
  install -m 0644 "$UNITS_SRC/tiktok-slot-render@.timer" "$SYSTEMD_DIR/"
  install -m 0644 "$UNITS_SRC/tiktok-slot-upload@.timer" "$SYSTEMD_DIR/"

  echo "2. daemon-reload"
  systemctl daemon-reload

  echo "3. write drop-ins for seed slots (skip existing)"
  local seed inst render_h render_m upload_h upload_m
  for seed in "${SEED_SLOTS[@]}"; do
    # seed format: "0000:23:30:00:00" -> inst=0000 render=23:30 upload=00:00
    IFS=":" read -r inst render_h render_m upload_h upload_m <<< "$seed"
    _ensure_dropin "$inst" "render" "${render_h}:${render_m}"
    _ensure_dropin "$inst" "upload" "${upload_h}:${upload_m}"
  done

  echo "4. daemon-reload"
  systemctl daemon-reload

  echo "5. remove concrete per-instance .timer files BEFORE enable"
  local concrete
  for concrete in "$SYSTEMD_DIR"/tiktok-slot-render@[0-9]*.timer \
                  "$SYSTEMD_DIR"/tiktok-slot-upload@[0-9]*.timer; do
    [[ -e "$concrete" ]] || continue
    # Guard rail: only match instance-N files, never the base template.
    case "$(basename "$concrete")" in
      *"@.timer") continue ;;
    esac
    local unit
    unit="$(basename "$concrete")"
    systemctl disable --now "$unit" 2>&1 | tail -1 || true
    rm -f "$concrete"
    echo "   removed $unit"
  done

  echo "6. daemon-reload after concrete removal"
  systemctl daemon-reload

  echo "7. enable templated instances (concrete gone → symlink resolves to base)"
  for seed in "${SEED_SLOTS[@]}"; do
    IFS=":" read -r inst render_h render_m upload_h upload_m <<< "$seed"
    # `enable --now` on a fresh templated timer with Persistent=false does
    # NOT fire the service even with past-time OnCalendar (P5R.0 research).
    # Safe to run this in the middle of the day.
    systemctl enable --now "tiktok-slot-render@${inst}.timer"
    systemctl enable --now "tiktok-slot-upload@${inst}.timer"
  done

  echo "8. final daemon-reload"
  systemctl daemon-reload

  echo "== done. Verify with: systemctl list-timers 'tiktok-slot-*' =="
}

# Internal: write a drop-in for <inst>/<kind> only if one doesn't exist.
# Kept side-effect-safe so migrate-slots can be re-run.
_ensure_dropin() {
  local inst="$1" kind="$2" hhmm="$3"
  local dir="$SYSTEMD_DIR/tiktok-slot-${kind}@${inst}.timer.d"
  local file="$dir/override.conf"
  if [[ -f "$file" ]]; then
    echo "   skip: $file already exists"
    return 0
  fi
  install -d -m 0755 -o root -g root "$dir"
  cat >"$file" <<EOF
# Written by scripts/install_systemd.sh migrate-slots at $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC
[Timer]
OnCalendar=
OnCalendar=*-*-* ${hhmm}:00 Europe/Madrid
EOF
  chmod 0644 "$file"
  echo "   wrote $file (OnCalendar=*-*-* ${hhmm}:00 Europe/Madrid)"
}

# Read-only. Standing R3.2 discipline: run after every systemd change,
# before considering it done — see [[decisions/2026-07-03-systemd-analyze-security-discipline]].
cmd_security() {
  local u line
  for u in "${UNITS_SECURITY[@]}"; do
    printf '%-42s ' "$u"
    line=$(systemd-analyze security "$u" 2>/dev/null | tail -1)
    echo "${line:-n/a (unit not installed?)}"
  done
}

cmd_kickstart() {
  local unit="${1:-}"
  [[ -z "$unit" ]] && { echo "kickstart <unit>"; exit 2; }
  systemctl restart "$unit.service"
  systemctl --no-pager status "$unit.service" | head -8
}

case "${1:-help}" in
  install)         shift; cmd_install "$@";;
  install-helper)  shift; cmd_install_helper "$@";;
  migrate-slots)   shift; cmd_migrate_slots "$@";;
  uninstall)       shift; cmd_uninstall "$@";;
  status)          shift; cmd_status "$@";;
  security)        shift; cmd_security "$@";;
  kickstart)       shift; cmd_kickstart "$@";;
  help|*)
    grep -E '^# ' "$0" | sed 's/^# \?//'
    ;;
esac
