#!/usr/bin/env bash
# Install / uninstall the tiktok-* systemd system units + polkit rule.
#
# Usage:
#   sudo bash scripts/install_systemd.sh install
#   sudo bash scripts/install_systemd.sh install-helper   # schedule helper + sudoers only
#   sudo bash scripts/install_systemd.sh uninstall
#   bash scripts/install_systemd.sh kickstart tiktok-webapp
#   bash scripts/install_systemd.sh status
#
# The install target copies units under /etc/systemd/system/, the polkit
# rule under /etc/polkit-1/rules.d/, runs `systemctl daemon-reload`, and
# enables + starts the always-on units. Timers are enabled but scheduled
# ticks fire on their own.
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
# tiktok-upload.timer intentionally omitted — replaced by the four
# tiktok-slot-{render,upload}@HHMM.timer pairs below.
TIMERS=(
  tiktok-confirm.timer
  tiktok-retention.timer
  tiktok-slot-render@0000.timer  tiktok-slot-upload@0000.timer
  tiktok-slot-render@1200.timer  tiktok-slot-upload@1200.timer
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
  # slot timers when the webapp changes fire times. Invoked via a
  # narrowly-scoped sudoers entry, so both must land together.
  install -m 0755 -o root -g root \
    "$HELPER_SRC/tiktok-slot-time-write" \
    "$HELPER_DIR/tiktok-slot-time-write"
  # Validate the sudoers snippet with visudo before dropping it into
  # /etc/sudoers.d so a syntax error here can't lock everyone out.
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
  echo "installed. status:"
  cmd_status
}

cmd_uninstall() {
  need_root uninstall
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
  systemctl --no-pager status "${PERSISTENT[@]/%/.service}" "${TIMERS[@]}" \
    || true
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

cmd_kickstart() {
  local unit="${1:-}"
  [[ -z "$unit" ]] && { echo "kickstart <unit>"; exit 2; }
  systemctl restart "$unit.service"
  systemctl --no-pager status "$unit.service" | head -8
}

case "${1:-help}" in
  install)         shift; cmd_install "$@";;
  install-helper)  shift; cmd_install_helper "$@";;
  uninstall)       shift; cmd_uninstall "$@";;
  status)          shift; cmd_status "$@";;
  kickstart)       shift; cmd_kickstart "$@";;
  help|*)
    grep -E '^# ' "$0" | sed 's/^# \?//'
    ;;
esac
