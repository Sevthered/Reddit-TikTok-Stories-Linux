# Automated-TikTok-Upload — one-command control surface.
#
# Two modes:
#   * prod  — LaunchAgents (webapp + bot always-on, one-shot upload/confirm).
#             Persists across reboots. Uses scripts/install_launchd.sh.
#   * dev   — foreground processes for interactive iteration. Ctrl-C to stop.
#
# Quick start:
#   make up            # install + bootstrap all LaunchAgents (prod)
#   make status        # show agent + port state
#   make down          # bootout + unlink all LaunchAgents
#   make dev           # foreground webapp + bot + SPA dev server (Ctrl-C stops all)
#   make logs          # tail every service log
#   make help          # full target list

SHELL      := /bin/bash
REPO       := $(CURDIR)
PY         := $(REPO)/venv/bin/python
PIP        := $(REPO)/venv/bin/pip
FRONT      := $(REPO)/webapp/frontend
LOG_DIR    := $(REPO)/data/logs
INSTALL_SH := $(REPO)/scripts/install_launchd.sh
DOMAIN     := gui/$(shell id -u)

AGENTS := \
  com.sebastian.tiktok-webapp \
  com.sebastian.tiktok-bot \
  com.sebastian.tiktok-upload \
  com.sebastian.tiktok-confirm

.DEFAULT_GOAL := help
.PHONY: help up down reload status install uninstall build-spa \
        dev dev-webapp dev-bot dev-frontend \
        kickstart-webapp kickstart-bot kickstart-upload kickstart-confirm \
        logs logs-webapp logs-bot logs-upload logs-confirm \
        deps deps-py deps-node clean-logs doctor

help:
	@echo "Automated-TikTok-Upload — make targets"
	@echo ""
	@echo "  Production (LaunchAgents):"
	@echo "    make up            install + bootstrap all agents"
	@echo "    make down          bootout + unlink all agents"
	@echo "    make reload        bootout + bootstrap (picks up plist changes)"
	@echo "    make status        show agent + port state"
	@echo "    make kickstart-<svc>   force-restart one (webapp|bot|upload|confirm)"
	@echo ""
	@echo "  Development (foreground, Ctrl-C to stop):"
	@echo "    make dev           run webapp + bot + SPA dev server together"
	@echo "    make dev-webapp    just FastAPI (uvicorn --reload)"
	@echo "    make dev-bot       just Telegram bot"
	@echo "    make dev-frontend  just SvelteKit dev server (HMR)"
	@echo ""
	@echo "  Logs:"
	@echo "    make logs          tail all service logs"
	@echo "    make logs-<svc>    tail one (webapp|bot|upload|confirm)"
	@echo ""
	@echo "  Setup:"
	@echo "    make deps          install Python + Node deps"
	@echo "    make build-spa     build SvelteKit SPA (adapter-static)"
	@echo "    make doctor        sanity check venv, node, launchd, agents"

# ---- production (LaunchAgents) -----------------------------------------

up install:
	@bash $(INSTALL_SH) install

down uninstall:
	@bash $(INSTALL_SH) uninstall

reload:
	@bash $(INSTALL_SH) reload

status:
	@bash $(INSTALL_SH) status
	@echo "---"
	@echo "port 8765:"; lsof -nP -iTCP:8765 -sTCP:LISTEN 2>/dev/null || echo "  (nothing listening)"

kickstart-webapp:  ; @bash $(INSTALL_SH) kickstart com.sebastian.tiktok-webapp
kickstart-bot:     ; @bash $(INSTALL_SH) kickstart com.sebastian.tiktok-bot
kickstart-upload:  ; @bash $(INSTALL_SH) kickstart com.sebastian.tiktok-upload
kickstart-confirm: ; @bash $(INSTALL_SH) kickstart com.sebastian.tiktok-confirm

# ---- development (foreground) ------------------------------------------

# Fan out webapp + bot + SPA dev in one shell. Trap SIGINT so Ctrl-C kills
# every child instead of orphaning uvicorn / node.
dev:
	@mkdir -p $(LOG_DIR)
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

# ---- logs --------------------------------------------------------------

logs:
	@echo "→ tailing all service logs (Ctrl-C to stop)"
	@tail -F \
	  $(LOG_DIR)/webapp.stdout.log $(LOG_DIR)/webapp.stderr.log \
	  $(LOG_DIR)/bot.stdout.log    $(LOG_DIR)/bot.stderr.log 2>/dev/null

logs-webapp:  ; @tail -F $(LOG_DIR)/webapp.stdout.log $(LOG_DIR)/webapp.stderr.log
logs-bot:     ; @tail -F $(LOG_DIR)/bot.stdout.log    $(LOG_DIR)/bot.stderr.log
logs-upload:  ; @tail -F $(LOG_DIR)/upload.stdout.log $(LOG_DIR)/upload.stderr.log 2>/dev/null || echo "(no upload log yet)"
logs-confirm: ; @tail -F $(LOG_DIR)/confirm.stdout.log $(LOG_DIR)/confirm.stderr.log 2>/dev/null || echo "(no confirm log yet)"

# ---- setup -------------------------------------------------------------

deps: deps-py deps-node

deps-py:
	@test -x $(PY) || (echo "!! venv missing at $(PY) — run: python3.14 -m venv venv" >&2; exit 1)
	@$(PIP) install -r requirements.txt

deps-node:
	@cd $(FRONT) && pnpm install --frozen-lockfile

build-spa:
	@cd $(FRONT) && pnpm install --frozen-lockfile && pnpm build

clean-logs:
	@rm -f $(LOG_DIR)/*.log && echo "cleared $(LOG_DIR)/*.log"

doctor:
	@echo "repo:        $(REPO)"
	@echo "venv python: $$( [ -x $(PY) ] && $(PY) --version || echo MISSING )"
	@echo "node:        $$(command -v node && node --version || echo MISSING)"
	@echo "pnpm:        $$(command -v pnpm && pnpm --version || echo MISSING)"
	@echo "launchctl:   $$(command -v launchctl || echo MISSING)"
	@echo "domain:      $(DOMAIN)"
	@echo "installed agents:"
	@launchctl list | grep -E "com\.sebastian\.tiktok" || echo "  (none)"
