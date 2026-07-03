#!/usr/bin/env bash
# Nightly age-encrypted backup of secrets + tokens + cookies to Cloudflare R2.
# See wiki/improvements/env-backup.md and
# wiki/decisions/2026-07-03-secrets-backup-age-rclone.md.
#
# Recipient is a public key only (/etc/tiktok/age-recipient.txt, non-secret).
# The matching private key lives only in the operator's password manager --
# never on this host, never decryptable here.
#
# Uses curl's native --aws-sigv4 (curl 7.75+) instead of rclone: the
# apt-packaged rclone (v1.60.1-DEV, Ubuntu 24.04) 403s on every R2 write
# regardless of token permissions -- confirmed by testing a raw signed PUT
# with curl against the same credentials, which succeeded. curl also drops
# a dependency entirely (no separate client to install/maintain). A raw PUT
# needs a known Content-Length, so the encrypted (never plaintext) archive
# is staged to a temp file first -- PrivateTmp=yes gives this unit a private,
# auto-torn-down /tmp; the trap is defense in depth on top of that.
set -euo pipefail

# shellcheck disable=SC1091
source /etc/tiktok/r2-credentials.env

STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
TMPFILE=$(mktemp /tmp/tiktok-secrets-backup.XXXXXX.tar.age)
trap 'rm -f "$TMPFILE"' EXIT

tar -cf - -C /etc tiktok/environment -C /srv/tiktok/app data/tiktok_tokens.json data/cookies \
  | age -R /etc/tiktok/age-recipient.txt \
  > "$TMPFILE"

curl -fsS --aws-sigv4 "aws:amz:auto:s3" \
  --user "${R2_ACCESS_KEY_ID}:${R2_SECRET_ACCESS_KEY}" \
  -X PUT \
  -T "$TMPFILE" \
  "${R2_ENDPOINT}/webapp-bucket/secrets/mk8s-04-${STAMP}.tar.age"
