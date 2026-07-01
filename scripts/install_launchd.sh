#!/usr/bin/env bash
# One-shot installer for the two Phase 6 LaunchAgents.
#
# Symlinks the repo's plists into ~/Library/LaunchAgents/ and loads them
# with launchctl. Idempotent: re-run after editing a plist to bump.
#
# Usage:
#   ./scripts/install_launchd.sh install   # symlink + load
#   ./scripts/install_launchd.sh uninstall # unload + unlink
#   ./scripts/install_launchd.sh status    # show current state
#   ./scripts/install_launchd.sh reload    # unload + reload (after plist edits)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LA_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$REPO_ROOT/data/logs"
AGENTS=(
  "com.sebastian.tiktok-upload"
  "com.sebastian.tiktok-bot"
  "com.sebastian.tiktok-confirm"
)

mkdir -p "$LA_DIR" "$LOG_DIR"

cmd="${1:-status}"

case "$cmd" in
  install)
    for a in "${AGENTS[@]}"; do
      src="$REPO_ROOT/launchd/${a}.plist"
      dst="$LA_DIR/${a}.plist"
      if [ ! -f "$src" ]; then
        echo "!! missing $src" >&2
        exit 1
      fi
      ln -sfv "$src" "$dst"
      launchctl unload "$dst" 2>/dev/null || true
      launchctl load "$dst"
      echo "loaded $a"
    done
    echo "---"
    echo "run 'launchctl list | grep sebastian' to verify"
    ;;
  uninstall)
    for a in "${AGENTS[@]}"; do
      dst="$LA_DIR/${a}.plist"
      launchctl unload "$dst" 2>/dev/null || true
      rm -f "$dst"
      echo "unloaded + unlinked $a"
    done
    ;;
  reload)
    for a in "${AGENTS[@]}"; do
      dst="$LA_DIR/${a}.plist"
      launchctl unload "$dst" 2>/dev/null || true
      launchctl load "$dst"
      echo "reloaded $a"
    done
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
    echo "usage: $0 {install|uninstall|reload|status}" >&2
    exit 2
    ;;
esac
