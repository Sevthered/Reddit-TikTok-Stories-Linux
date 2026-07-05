#!/usr/bin/env bash
# Create/refresh the Alertmanager Telegram config Secret in the `monitoring` ns.
# The full alertmanager routing/receiver config lives in this Secret (referenced by
# k8s/monitoring/values.yaml -> alertmanager.alertmanagerSpec.configSecret) so the
# bot token + chat id stay OUT of git. Reads them from the existing tiktok-secrets
# Secret; never echoes their values.
#
# Run on a k3s server node (has kubeconfig), as root:
#   sudo bash k8s/monitoring/make-alertmanager-telegram-secret.sh
# Then: helm upgrade monitoring prometheus-community/kube-prometheus-stack \
#         --version 87.6.0 -n monitoring -f k8s/monitoring/values.yaml
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}

TOK=$(kubectl -n tiktok get secret tiktok-secrets -o jsonpath='{.data.TELEGRAM_BOT_TOKEN}' | base64 -d)
CHAT=$(kubectl -n tiktok get secret tiktok-secrets -o jsonpath='{.data.TELEGRAM_CHAT_ID}' | base64 -d)
[ -n "$TOK" ] && [ -n "$CHAT" ] || { echo "MISSING TELEGRAM creds in tiktok-secrets"; exit 1; }

TMP=$(mktemp)
cat > "$TMP" <<EOF
global:
  resolve_timeout: 5m
route:
  receiver: telegram
  group_by: ["alertname", "namespace"]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers: ['alertname="Watchdog"']       # heartbeat -> external dead-man's-switch later
      receiver: "null"
      repeat_interval: 24h
    - matchers: ['alertname="InfoInhibitor"']
      receiver: "null"
receivers:
  - name: "null"
  - name: telegram
    telegram_configs:
      - bot_token: "$TOK"
        chat_id: $CHAT
        api_url: "https://api.telegram.org"
        parse_mode: "HTML"
        send_resolved: true
EOF

kubectl -n monitoring create secret generic alertmanager-telegram-config \
  --from-file=alertmanager.yaml="$TMP" --dry-run=client -o yaml | kubectl apply -f -
shred -u "$TMP" 2>/dev/null || rm -f "$TMP"
echo "OK: secret monitoring/alertmanager-telegram-config applied"
