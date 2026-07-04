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
{{- end -}}
