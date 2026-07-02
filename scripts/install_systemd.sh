#!/usr/bin/env bash
# Install / uninstall the tiktok-* systemd system units + polkit rule.
#
# Usage:
#   sudo bash scripts/install_systemd.sh install
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
SYSTEMD_DIR="/etc/systemd/system"
POLKIT_DIR="/etc/polkit-1/rules.d"

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
  rm -f "$POLKIT_DIR"/50-tiktok.rules
  systemctl daemon-reload
  echo "uninstalled."
}

cmd_status() {
  systemctl --no-pager status "${PERSISTENT[@]/%/.service}" "${TIMERS[@]}" \
    || true
}

cmd_kickstart() {
  local unit="${1:-}"
  [[ -z "$unit" ]] && { echo "kickstart <unit>"; exit 2; }
  systemctl restart "$unit.service"
  systemctl --no-pager status "$unit.service" | head -8
}

case "${1:-help}" in
  install)   shift; cmd_install "$@";;
  uninstall) shift; cmd_uninstall "$@";;
  status)    shift; cmd_status "$@";;
  kickstart) shift; cmd_kickstart "$@";;
  help|*)
    grep -E '^# ' "$0" | sed 's/^# \?//'
    ;;
esac
