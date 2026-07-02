# Reddit → TikTok control plane

FastAPI + SvelteKit + shadcn-svelte dashboard for the Reddit-story →
TikTok pipeline. Runs on `0.0.0.0:8765` (LAN-visible) under the
`tiktok-webapp.service` systemd unit, alongside `tiktok-bot.service`.

## Layout

```
webapp/
├── backend/
│   ├── app.py            # FastAPI entry, host allowlist, SPA mount
│   ├── settings.py       # paths, DEV_MODE, Madrid TZ
│   ├── deps.py           # Db dependency
│   ├── jobs.py           # subprocess JobManager (single-writer)
│   ├── schemas.py        # Pydantic outputs
│   └── routers/          # health, status, renders, actions,
│                         # jobs, config, cookie, logs, artifacts, agents
└── frontend/
    ├── src/
    │   ├── lib/          # api.ts + JobSheet.svelte + shadcn ui/
    │   └── routes/       # / (dashboard), /queue, /config, /logs
    ├── vite.config.ts    # adapter-static, tailwindcss, /api proxy
    └── build/            # produced by `pnpm build` (gitignored)
```

## Development

Terminal A — backend with dev host allowlist:

```
WEBAPP_DEV=1 ./.venv/bin/python -m uvicorn webapp.backend.app:app \
  --host 127.0.0.1 --port 8765 --reload
```

Terminal B — Vite dev server (Hot Module Reload, proxies `/api →
127.0.0.1:8765`):

```
cd webapp/frontend && pnpm dev
```

Open http://localhost:5173/.

## Production (single process)

The FastAPI app mounts the SvelteKit static build at `/`, so one uvicorn
serves both the SPA and the API. This is what `tiktok-webapp.service`
runs.

```
cd webapp/frontend && pnpm install --frozen-lockfile && pnpm build
./.venv/bin/python -m uvicorn webapp.backend.app:app --host 0.0.0.0 --port 8765
```

Open http://<server-ip>:8765/.

## systemd

```
sudo bash scripts/install_systemd.sh install     # copy units + polkit, enable + start
sudo bash scripts/install_systemd.sh uninstall
bash scripts/install_systemd.sh status
bash scripts/install_systemd.sh kickstart tiktok-webapp
```

The installer copies unit files to `/etc/systemd/system/` plus a scoped
polkit rule to `/etc/polkit-1/rules.d/50-tiktok.rules` so the runtime
user (`christian`) can drive `systemctl start/stop/restart tiktok-*`
without sudo. Persistent services (`tiktok-xvfb`, `tiktok-webapp`,
`tiktok-bot`) auto-start on boot; four render + four upload timers
fire the 00:00 / 06:00 / 12:00 / 18:00 Europe/Madrid slot schedule.

## Endpoints

| Method | Path                              | Purpose                         |
|-------:|:----------------------------------|:--------------------------------|
| GET    | /api/health                       | DB + config reachability        |
| GET    | /api/status                       | posts_today, agents, sessionid  |
| GET    | /api/renders/{pending,under-review,{id}} | queue rows                |
| POST   | /api/renders/{id}/{approve,reject}| flip status + Telegram edit     |
| POST   | /api/jobs/{render,upload,confirm} | spawn pipeline subprocess       |
| GET    | /api/jobs, /api/jobs/{id}         | list, get                       |
| GET    | /api/jobs/{id}/stream             | SSE stdout tail                 |
| POST   | /api/jobs/{id}/cancel             | SIGTERM (SIGKILL after 5s)      |
| GET    | /api/config/{toml,env}            | read config                     |
| PUT    | /api/config/toml                  | validate + atomic swap          |
| PUT    | /api/config/env/{key}             | rewrite one env key             |
| GET    | /api/logs/{name}/tail             | last N journald lines           |
| GET    | /api/logs/{name}/stream           | SSE `journalctl -f` follower    |
| GET    | /api/video/{id}, /api/cover/{id}  | inline artifact (206 Range)     |
| POST   | /api/agents/{label}/{load,unload,kickstart} | systemctl start/stop/restart |
| GET    | /api/cookie/health                | sessionid days remaining        |

## Security posture

LAN-first. Threat model = other devices on the same LAN; not remote
attackers (ufw allow-list restricts port 8765 to 10.0.0.0/8 +
192.168.0.0/16).

- `--host 0.0.0.0` — LAN-visible; ufw is the real perimeter.
- `WEBAPP_ALLOW_ANY_HOST=1` in the systemd unit disables the loopback
  host-header allowlist (the DNS-rebinding threat model doesn't apply
  once LAN clients need arbitrary Host headers).
- SPA + API same-origin in prod; SvelteKit `csrf.checkOrigin` stays on.
- Config editor validates every TOML edit against `core.config.load_config`
  before `os.replace` swaps the file, so a bad edit can't poison the
  pipeline.
- `.env` values render masked (`***last4`) for keys matching TOKEN /
  SECRET / KEY / etc.
- Artifact routes resolve paths and reject anything outside
  `data/output/`.
- Agents router whitelists systemd units; the self-unit
  (`tiktok-webapp.service`) is intentionally excluded so a request
  can't stop the process handling it.

Deferred: shared-token cookie / reverse proxy TLS if the port is ever
forwarded beyond the LAN. Not needed on LAN-only.

## Environment overrides

| Var                       | Purpose                            | Default        |
|---------------------------|------------------------------------|----------------|
| `WEBAPP_HOST`             | uvicorn bind                       | `127.0.0.1`    |
| `WEBAPP_PORT`             | uvicorn port                       | `8765`         |
| `WEBAPP_DEV`              | dev host allowlist + CORS + skip SPA mount | `0`    |
| `WEBAPP_ALLOWED_HOSTS`    | comma-separated extra hostnames    | (empty)        |
| `WEBAPP_ALLOW_ANY_HOST`   | disable host-header allowlist      | `0`            |
| `TELEGRAM_BOT_TOKEN`      | required for review-caption edit   | (from .env)    |
| `TELEGRAM_CHAT_ID`        | idem                               | (from .env)    |
| `PLAYWRIGHT_BROWSERS_PATH`| exported to job subprocesses       | `.playwright/` |
