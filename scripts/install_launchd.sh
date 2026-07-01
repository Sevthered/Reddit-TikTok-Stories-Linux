#!/usr/bin/env bash
# LaunchAgent installer for the pipeline + control-plane webapp.
#
# Uses the modern launchctl domain API (`bootstrap`/`bootout`/`kickstart`)
# under `gui/$(id -u)` — required for Playwright headful runs and macOS
# keychain prompts to route to the Aqua session. Legacy `load`/`unload`
# calls silently drop the GUI context and left Playwright orphaned when
# a headful browser opened.
#
# Usage:
#   ./scripts/install_launchd.sh install     # build SPA + symlink + bootstrap
#   ./scripts/install_launchd.sh reload      # bootout + bootstrap all
#   ./scripts/install_launchd.sh uninstall   # bootout + unlink
#   ./scripts/install_launchd.sh status      # show current state
#   ./scripts/install_launchd.sh kickstart <label>   # force-restart one
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$REPO_ROOT/data/logs"
FRONT_DIR="$REPO_ROOT/webapp/frontend"
DOMAIN="gui/$(id -u)"

AGENTS=(
  "com.sebastian.tiktok-upload"
  "com.sebastian.tiktok-bot"
  "com.sebastian.tiktok-confirm"
  "com.sebastian.tiktok-webapp"
)

mkdir -p "$LA_DIR" "$LOG_DIR"

build_frontend() {
  if [ ! -d "$FRONT_DIR" ]; then
    echo "!! $FRONT_DIR missing — skip SPA build" >&2
    return
  fi
  echo "→ building SvelteKit SPA (adapter-static)"
  ( cd "$FRONT_DIR" && pnpm install --frozen-lockfile && pnpm build )
}

bootstrap_one() {
  local a="$1"
  local dst="$LA_DIR/${a}.plist"
  launchctl bootout "$DOMAIN/${a}" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$dst"
  echo "bootstrapped $a"
}

bootout_one() {
  local a="$1"
  launchctl bootout "$DOMAIN/${a}" 2>/dev/null && echo "booted out $a" || echo "$a not loaded"
}

cmd="${1:-status}"

case "$cmd" in
  install)
    build_frontend
    for a in "${AGENTS[@]}"; do
      src="$REPO_ROOT/launchd/${a}.plist"
      dst="$LA_DIR/${a}.plist"
      if [ ! -f "$src" ]; then
        echo "!! missing $src" >&2
        exit 1
      fi
      ln -sfv "$src" "$dst"
      bootstrap_one "$a"
    done
    echo "---"
    launchctl list | grep -E "com\.sebastian\.tiktok" || true
    ;;
  reload)
    build_frontend
    for a in "${AGENTS[@]}"; do
      dst="$LA_DIR/${a}.plist"
      [ -f "$dst" ] || { echo "$a not installed; skip"; continue; }
      launchctl bootout "$DOMAIN/${a}" 2>/dev/null || true
      launchctl bootstrap "$DOMAIN" "$dst"
      echo "reloaded $a"
    done
    ;;
  uninstall)
    for a in "${AGENTS[@]}"; do
      dst="$LA_DIR/${a}.plist"
      bootout_one "$a"
      rm -f "$dst"
    done
    ;;
  kickstart)
    label="${2:?usage: kickstart <label>}"
    launchctl kickstart -k "$DOMAIN/${label}"
    ;;
  status)
    launchctl list | grep -E "com\.sebastian\.tiktok" || echo "(no agents loaded)"
    echo "---"
    for a in "${AGENTS[@]}"; do
      dst="$LA_DIR/${a}.plist"
      if [ -L "$dst" ]; then
        printf '  %s -> %s\n' "$dst" "$(readlink "$dst")"
      elif [ -f "$dst" ]; then
        printf '  %s (regular file, not our symlink)\n' "$dst"
      else
        printf '  %s (not installed)\n' "$dst"
      fi
    done
    ;;
  *)
    echo "usage: $0 {install|reload|uninstall|kickstart <label>|status}" >&2
    exit 2
    ;;
esac
