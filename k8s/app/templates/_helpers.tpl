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
