# TikTok app Helm chart (Phase 6)

ONE image (`10.10.10.21:5000/tiktok/app`) run as many workloads, translating the
systemd control plane to k8s. All data-touching pods pin to one node and share a
single **RWO** PVC because the app keeps **SQLite** (single-writer, no network
FS). See `wiki/sources/2026-07-03-k8s-migration-handoff.md` and
`wiki/concepts/k8s-deploy-workflow.md`.

## Workloads
| Template | Workload | Notes |
|---|---|---|
| `webapp-deployment.yaml` | Deployment (1) | uvicorn :8765; `db-init` initContainer seeds SQLite schema |
| `webapp-service.yaml` / `ingress.yaml` | ClusterIP + Ingress | `nginx` class, host `tiktok.mk8s.lan` |
| `bot-deployment.yaml` | Deployment (`bot.replicas`) | Telegram bot; reaches webapp via `WEBAPP_BASE` + `X-Internal-Token` |
| `cronjobs.yaml` | 6 CronJobs | render 23:30/11:30, upload 00:00/12:00, confirm */30, retention hourly (Europe/Madrid) |
| `pvc.yaml` | PVC `tiktok-data` | `ceph-block` RWO, mounted `/app/data` everywhere |

render/upload run headed Chromium under `xvfb-run -a` with `HOME=/app/.chromium-home`
+ a 1Gi `/dev/shm`.

## Phase 6 = safe non-posting posture (defaults)
`bot.replicas: 0`, `cronjobs.suspend: true` — nothing posts to TikTok, no
Telegram conflict with the still-live systemd prod on mk8s-04. Proves the
manifests run without touching prod.

## Prerequisites (out-of-band)
1. Label the pin node: `kubectl label node mk8s-01 tiktok.app/pinned=true`
2. Namespace: `kubectl create namespace tiktok`
3. Secret (operator, fresh values — NOT prod creds in Phase 6):
   ```
   kubectl -n tiktok create secret generic tiktok-secrets \
     --from-literal=WEBAPP_CSRF_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
     --from-literal=WEBAPP_INTERNAL_TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
     --from-literal=WEBAPP_ALLOWED_HOSTS="tiktok.mk8s.lan,tiktok-webapp,tiktok-webapp:8765"
   ```
4. pfSense DNS host-override `tiktok.mk8s.lan -> 10.10.10.20` (ingress LB IP).

## Install / upgrade
```
helm install tiktok k8s/app -n tiktok       # from a host with the kubeconfig (mk8s-01)
helm upgrade tiktok k8s/app -n tiktok        # after values/image bumps
```

## Phase 7 cutover (later)
`helm upgrade ... --set bot.replicas=1 --set cronjobs.suspend=false` AFTER:
join mk8s-04 as agent, move the `tiktok.app/pinned` label to it, migrate real
data (DB/cookies/config/tokens) into the PVC, load real secrets, stop the
systemd units, add the cloudflared tunnel.
