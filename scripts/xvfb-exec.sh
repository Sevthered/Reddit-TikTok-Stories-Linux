#!/bin/sh
# Race-free Xvfb launcher — drop-in replacement for `xvfb-run -a <cmd>`.
#
# Why not xvfb-run: its readiness handshake starts Xvfb with SIGUSR1 ignored,
# then `wait`s for Xvfb to signal SIGUSR1 "ready". If Xvfb becomes ready and
# fires the signal BEFORE the parent shell reaches `wait`, the early trap
# consumes it and `wait` blocks forever — the command after it never runs. That
# race hung both render and upload on 2026-07-17 (no post went out). See wiki
# bug 2026-07-18-xvfb-run-sigusr1-startup-race.
#
# `Xvfb -displayfd N` instead writes the chosen display number to fd N ONLY once
# the server is ready to accept connections. No signal, no race: we poll that
# fd's file, then exec the command with DISPLAY set. Chromium/Playwright read
# DISPLAY from the environment.
set -eu

if [ "$#" -eq 0 ]; then
  echo "xvfb-exec: usage: xvfb-exec.sh <command> [args...]" >&2
  exit 2
fi

WORKDIR="$(mktemp -d)"
DISPFILE="$WORKDIR/display"
XVFB_LOG="$WORKDIR/xvfb.log"

# -displayfd 3 → DISPFILE (server writes "<num>\n" when ready).
# 1920x1080x24 matches the proven systemd tiktok-xvfb.service geometry.
# -nolisten tcp: unix socket only, no network surface.
Xvfb -displayfd 3 -screen 0 1920x1080x24 -nolisten tcp 3>"$DISPFILE" >"$XVFB_LOG" 2>&1 &
XVFB_PID=$!

# Reap Xvfb + tmp on any pre-exec failure exit. (On the success path `exec`
# replaces this shell; the k8s container teardown then kills the Xvfb child.)
cleanup() {
  kill "$XVFB_PID" 2>/dev/null || true
  wait "$XVFB_PID" 2>/dev/null || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

# Wait (bounded, 20s) for Xvfb to report a display, or die trying.
DISPLAY_NUM=""
i=0
while [ "$i" -lt 200 ]; do
  if [ -s "$DISPFILE" ]; then
    DISPLAY_NUM="$(tr -d '[:space:]' < "$DISPFILE")"
    [ -n "$DISPLAY_NUM" ] && break
  fi
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "xvfb-exec: Xvfb exited before becoming ready" >&2
    cat "$XVFB_LOG" >&2 || true
    exit 1
  fi
  i=$((i + 1))
  sleep 0.1
done

if [ -z "$DISPLAY_NUM" ]; then
  echo "xvfb-exec: timed out after 20s waiting for Xvfb display" >&2
  cat "$XVFB_LOG" >&2 || true
  exit 1
fi

export DISPLAY=":$DISPLAY_NUM"
trap - EXIT INT TERM
exec "$@"
