{{- define "tiktok.labels" -}}
app.kubernetes.io/name: tiktok
app.kubernetes.io/part-of: tiktok
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "tiktok.image" -}}
{{ .Values.image.repo }}:{{ .Values.image.tag }}
{{- end -}}

{{/*
Hardened container securityContext for the app-image containers (webapp, bot,
cronjobs, litestream, init). The image already runs as non-root (USER pwuser);
this drops all caps, blocks privilege escalation, and pins the seccomp profile
(defense-in-depth toward PSS "restricted" / CIS 5.2). readOnlyRootFilesystem is
intentionally NOT set — the app writes /app/logs, /tmp and /dev/shm at runtime.
NOT applied to the third-party cloudflared image (its default user is external).
*/}}
{{- define "tiktok.containerSecurityContext" -}}
runAsNonRoot: true
# Pin the numeric UID/GID (image USER is the NAME `pwuser` = 1001; the kubelet
# cannot verify runAsNonRoot against a non-numeric username → CreateContainerConfigError).
runAsUser: 1001
runAsGroup: 1001
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Shared env for every workload: timezone + the app Secret (CSRF, internal token,
allowed hosts, and — in Phase 7 — the real TikTok/Reddit/Telegram creds).
*/}}
{{- define "tiktok.commonEnv" -}}
- name: TZ
  value: {{ .Values.timezone | quote }}
- name: PYTHONUNBUFFERED
  value: "1"
# config.toml and the webapp .env editor target live on the writable PVC
# (/app/data) instead of the ephemeral image layer, so edits survive pod
# restarts. CONFIG_PATH is read by both the pipeline (core.config.load_config)
# and the webapp (settings.CONFIG_PATH); WEBAPP_ENV_PATH is webapp-only (inert
# elsewhere). The migration seeds /app/data/config.toml at cutover.
- name: CONFIG_PATH
  value: /app/data/config.toml
- name: WEBAPP_ENV_PATH
  value: /app/data/webapp.env
{{- end -}}
