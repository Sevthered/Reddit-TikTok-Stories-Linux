# Reddit → TikTok control plane

FastAPI + SvelteKit + shadcn-svelte dashboard for the Reddit-story →
TikTok pipeline. Runs on `127.0.0.1:8765`, side-by-side with the
`com.sebastian.tiktok-bot` LaunchAgent.

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
WEBAPP_DEV=1 ./venv/bin/python -m uvicorn webapp.backend.app:app \
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
serves both the SPA and the API. This is what the `tiktok-webapp`
LaunchAgent runs.

```
cd webapp/frontend && pnpm install --frozen-lockfile && pnpm build
./venv/bin/python -m uvicorn webapp.backend.app:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765/.

## LaunchAgent

```
./scripts/install_launchd.sh install   # symlink plists + bootstrap
./scripts/install_launchd.sh reload    # rebuild SPA + bootout + bootstrap
./scripts/install_launchd.sh status
./scripts/install_launchd.sh kickstart com.sebastian.tiktok-webapp
```

The installer uses `launchctl bootstrap gui/$(id -u)` so agents run in
the Aqua GUI session (required for Playwright headful renders and
macOS Keychain prompts).

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
| GET    | /api/logs/{name}/tail             | last N lines                    |
| GET    | /api/logs/{name}/stream           | SSE follower (rotation-safe)    |
| GET    | /api/video/{id}, /api/cover/{id}  | inline artifact (206 Range)     |
| POST   | /api/agents/{label}/{load,unload,kickstart} | launchctl in gui/uid  |
| GET    | /api/cookie/health                | sessionid days remaining        |

## Security posture

Local-first. Threat model = other processes / browser pages on the same
Mac; not remote attackers (127.0.0.1 bind rules them out).

- `--host 127.0.0.1` — never listens on 0.0.0.0.
- Host-header allowlist middleware — closes DNS-rebinding on loopback.
- SPA + API same-origin in prod; SvelteKit `csrf.checkOrigin` stays on.
- Config editor validates every TOML edit against `core.config.load_config`
  before `os.replace` swaps the file, so a bad edit can't poison the
  pipeline.
- `.env` values render masked (`***last4`) for keys matching TOKEN /
  SECRET / KEY / etc.
- Artifact routes resolve paths and reject anything outside
  `data/output/`.
- Agents router whitelists three labels; the self-agent
  (`com.sebastian.tiktok-webapp`) is intentionally excluded so a request
  can't bootout the process handling it.

Deferred: shared-token HttpOnly cookie for a second layer of defence
when the port is ever forwarded. Not needed on local-only.

## Environment overrides

| Var                       | Purpose                            | Default        |
|---------------------------|------------------------------------|----------------|
| `WEBAPP_HOST`             | uvicorn bind                       | `127.0.0.1`    |
| `WEBAPP_PORT`             | uvicorn port                       | `8765`         |
| `WEBAPP_DEV`              | dev host allowlist + CORS + skip SPA mount | `0`    |
| `TELEGRAM_BOT_TOKEN`      | required for review-caption edit   | (from .env)    |
| `TELEGRAM_CHAT_ID`        | idem                               | (from .env)    |
| `PLAYWRIGHT_BROWSERS_PATH`| exported to job subprocesses       | user cache dir |
