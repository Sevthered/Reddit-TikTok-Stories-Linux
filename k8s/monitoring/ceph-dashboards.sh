#!/usr/bin/env bash
# Ceph Grafana dashboards as ConfigMaps (task #28, A4).
# The kube-prometheus-stack Grafana runs a dashboard sidecar that auto-loads any
# ConfigMap in ANY namespace labeled `grafana_dashboard=1`. This ships the
# canonical Ceph dashboards (from ceph/ceph ceph-mixin, matched to the cluster's
# Ceph version) so they're GitOps-managed instead of imported by-ID via the UI.
# They read the existing Prometheus datasource (ceph_* metrics are already
# scraped via the ceph ServiceMonitor).
#
# Run on a k3s server (needs `k3s kubectl` + internet). Re-run to update.
set -euo pipefail
REF="${CEPH_REF:-tentacle}"          # ceph 20.2.x = tentacle; bump with the cluster
NS="${NS:-monitoring}"
K="sudo -n k3s kubectl"
BASE="https://raw.githubusercontent.com/ceph/ceph/${REF}/monitoring/ceph-mixin/dashboards_out"
# Curated for this block-storage cluster (skip radosgw/nvmeof/smb/cephfs):
DASHBOARDS=(ceph-cluster hosts-overview osds-overview pool-overview rbd-overview)

for d in "${DASHBOARDS[@]}"; do
  curl -sSL "${BASE}/${d}.json" -o "/tmp/${d}.json"
  $K create configmap "ceph-dash-${d}" -n "$NS" \
    --from-file="${d}.json=/tmp/${d}.json" --dry-run=client -o yaml | $K apply -f -
  $K label configmap "ceph-dash-${d}" -n "$NS" grafana_dashboard=1 --overwrite
  rm -f "/tmp/${d}.json"
done
echo "Applied ${#DASHBOARDS[@]} Ceph dashboards (ref=${REF}) to ns/${NS}."
