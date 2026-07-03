#!/usr/bin/env bash
# Nightly age-encrypted backup of secrets + tokens + cookies to Cloudflare R2.
# See wiki/improvements/env-backup.md and
# wiki/decisions/2026-07-03-secrets-backup-age-rclone.md.
#
# Recipient is a public key only (/etc/tiktok/age-recipient.txt, non-secret).
# The matching private key lives only in the operator's password manager --
# never on this host, never decryptable here.
set -euo pipefail

STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)

tar -cf - -C /etc tiktok/environment -C /srv/tiktok/app data/tiktok_tokens.json data/cookies \
  | age -R /etc/tiktok/age-recipient.txt \
  | rclone rcat --config /etc/tiktok/rclone.conf \
      "r2:webapp-bucket/secrets/mk8s-04-${STAMP}.tar.age"
