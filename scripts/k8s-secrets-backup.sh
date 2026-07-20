#!/usr/bin/env bash
# k8s-native nightly secrets backup — replaces the systemd-era
# deploy/scripts/tiktok-secrets-backup.sh (which backed up STALE mk8s-04 files).
#
# Runs as the `tiktok-secrets-backup` CronJob. Backs up the LIVE cluster state:
#   - k8s Secrets, mounted as files under /backup/secrets/<name>/<key>
#     (tiktok-secrets, tiktok-litestream, tiktok-cloudflared)
#   - PVC session state at /app/data (tiktok_tokens.json, cookies/)
# age-encrypts to an operator-only recipient (public key in AGE_RECIPIENT; the
# private key lives ONLY in the operator's password manager — never on-cluster),
# then PUTs to R2 via curl --aws-sigv4 (the apt rclone is too old — see
# wiki/decisions/2026-07-03-secrets-backup-age-rclone.md).
#
# Plaintext is staged only in a memory-backed /tmp (emptyDir medium=Memory) and
# torn down by the trap + pod lifecycle; the archive itself is never plaintext.
set -euo pipefail

: "${AGE_RECIPIENT:?AGE_RECIPIENT (public age key) must be set}"
: "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_ENDPOINT:?}" "${R2_BUCKET:?}"

STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
STAGE=$(mktemp -d /tmp/secbak.XXXXXX)
TMPFILE=$(mktemp /tmp/secbak.XXXXXX.tar.age)
trap 'rm -rf "$STAGE" "$TMPFILE"' EXIT

# Resolve each mounted Secret's key files (glob skips the ..data symlink dir).
for s in tiktok-secrets tiktok-litestream tiktok-cloudflared; do
  src="/backup/secrets/$s"
  [ -d "$src" ] || continue
  mkdir -p "$STAGE/$s"
  for f in "$src"/*; do
    [ -f "$f" ] && cp -L "$f" "$STAGE/$s/"
  done
done

# PVC session state (present on the tiktok-data PVC, mounted read-only).
for f in tiktok_tokens.json cookies; do
  [ -e "/app/data/$f" ] && cp -rL "/app/data/$f" "$STAGE/"
done

tar -cf - -C "$STAGE" . | age -r "$AGE_RECIPIENT" > "$TMPFILE"

curl -fsS --aws-sigv4 "aws:amz:auto:s3" \
  --user "${R2_ACCESS_KEY_ID}:${R2_SECRET_ACCESS_KEY}" \
  -X PUT \
  -T "$TMPFILE" \
  "${R2_ENDPOINT}/${R2_BUCKET}/secrets/mk8s-cluster-${STAMP}.tar.age"

echo "secrets-backup OK: secrets/mk8s-cluster-${STAMP}.tar.age ($(stat -c%s "$TMPFILE") bytes)"
