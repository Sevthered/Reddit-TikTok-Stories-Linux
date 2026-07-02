# Automated-TikTok-Upload — Linux (systemd) control surface.
#
# Prod: systemd system units under /etc/systemd/system/, installed via
# scripts/install_systemd.sh. Dev: foreground processes for iteration.
#
# Quick start:
#   sudo make install    # copy units + polkit rule, enable + start
#   make up              # start persistent units (webapp, bot, xvfb)
#   make down            # stop persistent units
#   make status          # unit + timer state
#   make dev             # foreground webapp + bot + SPA dev (Ctrl-C stops all)
#   make logs            # journalctl -f -u tiktok-*

SHELL      := /bin/bash
REPO       := $(CURDIR)
PY         := $(REPO)/.venv/bin/python
PIP        := $(REPO)/.venv/bin/pip
FRONT      := $(REPO)/webapp/frontend
INSTALL_SH := $(REPO)/scripts/install_systemd.sh

PERSISTENT := tiktok-xvfb tiktok-bot
TIMERS     := tiktok-confirm.timer tiktok-retention.timer \
              tiktok-slot-render@0000.timer tiktok-slot-upload@0000.timer \
              tiktok-slot-render@0600.timer tiktok-slot-upload@0600.timer \
              tiktok-slot-render@1200.timer tiktok-slot-upload@1200.timer \
              tiktok-slot-render@1800.timer tiktok-slot-upload@1800.timer
ALL_UNITS  := $(addsuffix .service,$(PERSISTENT)) tiktok-webapp.service $(TIMERS)

.DEFAULT_GOAL := help
.PHONY: help install uninstall up down reload status build-spa \
        web-up web-down web-status \
        dev dev-webapp dev-bot dev-frontend \
        kickstart-webapp kickstart-bot kickstart-upload kickstart-confirm \
        logs logs-webapp logs-bot logs-upload logs-confirm logs-xvfb \
        deps deps-py deps-node clean-logs doctor

help:
	@echo "Automated-TikTok-Upload — make targets"
	@echo ""
	@echo "  One-time install (needs root):"
	@echo "    sudo make install     copy units + polkit, enable + start"
	@echo "    sudo make uninstall   disable + remove units + polkit"
	@echo ""
	@echo "  Runtime (no sudo — polkit rule covers christian):"
	@echo "    make up               start bot+xvfb (webapp is off-by-default)"
	@echo "    make down             stop bot+xvfb"
	@echo "    make reload           restart bot"
	@echo "    make status           unit + timer state"
	@echo "    make kickstart-<svc>  restart one (webapp|bot|upload|confirm)"
	@echo ""
	@echo "  Webapp (off-by-default; run on demand while SSH'd in):"
	@echo "    make web-up           start tiktok-webapp on 0.0.0.0:8765"
	@echo "    make web-down         stop tiktok-webapp"
	@echo "    make web-status       show current state + listening port"
	@echo ""
	@echo "  Development (foreground, Ctrl-C to stop):"
	@echo "    make dev              webapp + bot + SPA dev together"
	@echo "    make dev-webapp | dev-bot | dev-frontend"
	@echo ""
	@echo "  Logs:"
	@echo "    make logs             journalctl -f across all tiktok-* units"
	@echo "    make logs-<svc>       follow one unit's journal"
	@echo ""
	@echo "  Setup:"
	@echo "    make deps             install Python + Node deps"
	@echo "    make build-spa        build SvelteKit SPA"
	@echo "    make doctor           sanity check env"

# ---- one-time installation (root) --------------------------------------

install:
	@bash $(INSTALL_SH) install

uninstall:
	@bash $(INSTALL_SH) uninstall

# ---- runtime (no sudo, polkit-authorized) ------------------------------

up:
	@for u in $(PERSISTENT); do systemctl start $$u.service; done
	@echo "→ started: $(PERSISTENT)"

down:
	@for u in $(PERSISTENT); do systemctl stop $$u.service; done
	@echo "→ stopped: $(PERSISTENT)"

reload:
	@systemctl restart tiktok-bot.service
	@echo "→ restarted: bot"

# --- webapp (off-by-default; polkit-authorized, no sudo needed) ---
web-up:
	@systemctl start tiktok-webapp.service
	@echo "→ webapp up on 0.0.0.0:8765 — http://$$(hostname -I | awk '{print $$1}'):8765"

web-down:
	@systemctl stop tiktok-webapp.service
	@echo "→ webapp down"

web-status:
	@systemctl --no-pager status tiktok-webapp.service | head -8 || true
	@echo "---"
	@ss -tlnp 2>/dev/null | grep ':8765' || echo "  (nothing listening on :8765)"

status:
	@systemctl --no-pager status $(ALL_UNITS) || true
	@echo "---"
	@echo "port 8765:"; ss -tlnp 2>/dev/null | grep ':8765' || echo "  (nothing listening)"

kickstart-webapp:  ; @bash $(INSTALL_SH) kickstart tiktok-webapp
kickstart-bot:     ; @bash $(INSTALL_SH) kickstart tiktok-bot
kickstart-upload:  ; @bash $(INSTALL_SH) kickstart tiktok-upload
kickstart-confirm: ; @bash $(INSTALL_SH) kickstart tiktok-confirm

# ---- development (foreground) ------------------------------------------

dev:
	@echo "→ dev: webapp :8765, bot, SPA :5173. Ctrl-C to stop all."
	@set -m; \
	trap 'echo; echo "→ stopping..."; kill 0 2>/dev/null; wait 2>/dev/null; exit 0' INT TERM; \
	( cd $(REPO) && $(PY) -u -m uvicorn webapp.backend.app:app --host 127.0.0.1 --port 8765 --reload 2>&1 | sed -u "s/^/[webapp] /" ) & \
	( cd $(REPO) && $(PY) -u scripts/run_bot.py 2>&1 | sed -u "s/^/[bot]    /" ) & \
	( cd $(FRONT) && pnpm dev 2>&1 | sed -u "s/^/[spa]    /" ) & \
	wait

dev-webapp:
	@cd $(REPO) && $(PY) -u -m uvicorn webapp.backend.app:app --host 127.0.0.1 --port 8765 --reload

dev-bot:
	@cd $(REPO) && $(PY) -u scripts/run_bot.py

dev-frontend:
	@cd $(FRONT) && pnpm dev

# ---- logs (journalctl) -------------------------------------------------

logs:
	@echo "→ following journal for: $(PERSISTENT) tiktok-upload tiktok-confirm"
	@journalctl -f -n 20 \
	  -u tiktok-xvfb.service \
	  -u tiktok-webapp.service \
	  -u tiktok-bot.service \
	  -u tiktok-upload.service \
	  -u tiktok-confirm.service

logs-webapp:  ; @journalctl -f -n 100 -u tiktok-webapp.service
logs-bot:     ; @journalctl -f -n 100 -u tiktok-bot.service
logs-upload:  ; @journalctl -f -n 100 -u tiktok-upload.service
logs-confirm: ; @journalctl -f -n 100 -u tiktok-confirm.service
logs-xvfb:    ; @journalctl -f -n 100 -u tiktok-xvfb.service

# ---- setup -------------------------------------------------------------

deps: deps-py deps-node

deps-py:
	@test -x $(PY) || (echo "!! venv missing at $(PY) — run: python3.14 -m venv .venv" >&2; exit 1)
	@$(PIP) install -r requirements.txt

deps-node:
	@cd $(FRONT) && pnpm install --frozen-lockfile

build-spa:
	@cd $(FRONT) && pnpm install --frozen-lockfile && pnpm build

clean-logs:
	@sudo journalctl --vacuum-time=7d

doctor:
	@echo "repo:        $(REPO)"
	@echo "venv python: $$( [ -x $(PY) ] && $(PY) --version || echo MISSING )"
	@echo "node:        $$(command -v node && node --version || echo MISSING)"
	@echo "pnpm:        $$(command -v pnpm && pnpm --version || echo MISSING)"
	@echo "ffmpeg:      $$(command -v ffmpeg || echo MISSING)"
	@echo "xvfb:        $$(command -v Xvfb || echo MISSING)"
	@echo "systemctl:   $$(command -v systemctl || echo MISSING)"
	@echo "installed units:"
	@systemctl list-unit-files 'tiktok-*' --no-pager 2>/dev/null || echo "  (none)"
