#!/usr/bin/env bash
# Backup everything a replacement node needs: scripts/backup.sh [outfile]
# = /etc/bng-platform (config, secrets, TLS identity, branding) + /etc/easywall + the bng_platform database.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "backup.sh: run as root" >&2; exit 1; }
OUT=${1:-/var/lib/bng-platform/backup-$(hostname)-$(date +%Y%m%dT%H%M%S).tar.gz}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
sudo -u postgres pg_dump -Fc bng_platform > "$T/db.dump"
cp -a /etc/bng-platform "$T/bng-platform"
[ -d /etc/easywall ] && cp -a /etc/easywall "$T/easywall"
install -d -m 0700 "$(dirname "$OUT")"
(umask 077; tar czf "$OUT" -C "$T" .)
echo "$OUT   (contains secrets: keep it private)"
