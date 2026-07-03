# Rook-Ceph — platform distributed storage (k8s-migration Phase 3)

Infrastructure-as-code for the Ceph storage layer on the mk8s k3s cluster.

- **Charts** (THREE required in Rook 1.20 — see the CSI note below):
  1. `rook-release/rook-ceph` (operator), pinned **v1.20.1** — `operator-values.yaml`
  2. `rook-release/rook-ceph-cluster` (cluster), pinned **v1.20.1** — `cluster-values.yaml`
  3. `ceph-csi-operator/ceph-csi-drivers`, pinned **1.0.1** — `csi-drivers-values.yaml`
- **Cluster**: 3 servers (mk8s-01/02/03), one OSD each on the raw `sda3`
  partition (643G SATA SSD). MON x3 (quorum), MGR x2. Replica 3,
  failure-domain=host → survives one node loss.
- **Tuned for 15GB nodes**: `osd_memory_target` 2GiB, per-daemon resource
  limits, scrub/recovery throttled. See `cluster-values.yaml`.
- **Storage classes**: `ceph-block` (RBD, RWO, default) — WORKING/verified;
  `ceph-filesystem` (CephFS, RWX) + `ceph-bucket` (RGW, S3) — added next.
- Prod mk8s-04's `sda3` becomes a 4th OSD at cutover (Phase 7); cluster rebalances.

## ⚠️ Rook 1.20 CSI requires a THIRD chart + one manual CRD (hard-won)
Rook 1.20 moved CSI to the `ceph-csi-operator`. The operator + cluster charts
alone do NOT deploy a working CSI driver — PVCs hang on `ExternalProvisioning`.
Two gaps hit, both now handled:
1. **Missing chart**: must ALSO install `ceph-csi-operator/ceph-csi-drivers`
   (version-matched to the operator image, here **1.0.1**) with Rook's values
   (`deploy/charts/ceph-csi-drivers/values.yaml`, saved as
   `csi-drivers-values.yaml`). It creates the OperatorConfig + Driver CRs +
   ServiceAccounts + RBAC consistently. Rook docs: "the drivers will fail if
   only configured with the chart defaults." Install the drivers chart at the
   SAME version as the ceph-csi-operator image to avoid the v1.20.0 SA-naming
   skew bug.
2. **Missing CRD**: the operator chart shipped 4/5 `csi.ceph.io` CRDs —
   `clientprofiles.csi.ceph.io` was absent. Apply the full v1.0.1 CRD set:
   `kubectl apply --server-side -f https://raw.githubusercontent.com/ceph/ceph-csi-operator/v1.0.1/deploy/multifile/crd.yaml`

## Apply (full, correct order)
```
helm repo add rook-release https://charts.rook.io/release
helm repo add ceph-csi-operator https://ceph.github.io/ceph-csi-operator
helm repo update

# 1. operator
helm install --create-namespace -n rook-ceph rook-ceph \
  rook-release/rook-ceph --version v1.20.1 -f operator-values.yaml

# 1b. fix missing CRD (Rook 1.20.1 chart gap)
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/ceph/ceph-csi-operator/v1.0.1/deploy/multifile/crd.yaml

# 2. cluster
helm install -n rook-ceph rook-ceph-cluster --set operatorNamespace=rook-ceph \
  rook-release/rook-ceph-cluster --version v1.20.1 -f cluster-values.yaml

# 3. CSI drivers (THE step most guides miss) — match version to operator image
helm install ceph-csi-drivers ceph-csi-operator/ceph-csi-drivers --version 1.0.1 \
  -n rook-ceph -f csi-drivers-values.yaml
```

## Rollback
`helm uninstall ceph-csi-drivers rook-ceph-cluster rook-ceph -n rook-ceph`
→ on each node: `rm -rf /var/lib/rook` + `wipefs -a /dev/sda3`.
