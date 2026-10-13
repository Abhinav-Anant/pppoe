#!/usr/bin/env bash
# Restore a backup.sh archive onto a node that install.sh/bootstrap.sh already set up:
#   scripts/restore.sh <archive.tar.gz>
# Keeps this node's own database credential (secrets/db.url); everything else comes from the archive.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "restore.sh: run as root" >&2; exit 1; }
A=${1:?usage: restore.sh <archive.tar.gz>}
[ -s /etc/bng-platform/secrets/db.url ] || { echo "run install.sh/bootstrap.sh first" >&2; exit 1; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
tar xzf "$A" -C "$T"
[ -f "$T/db.dump" ] && [ -d "$T/bng-platform" ] || { echo "not a backup.sh archive" >&2; exit 1; }

systemctl stop bng-api bng-api-remote 2>/dev/null || true
cp -a /etc/bng-platform/secrets/db.url "$T/db.url"
rm -rf "$T/bng-platform/secrets/db.url"
cp -a "$T"/bng-platform/. /etc/bng-platform/
sudo -u postgres pg_restore --no-owner --role=bng_api --clean --if-exists -d bng_platform "$T/db.dump"
if [ -d "$T/easywall" ]; then cp -a "$T"/easywall/. /etc/easywall/; systemctl restart easywall-core easywall-web 2>/dev/null || true; fi
bngctl db upgrade
systemctl start bng-api; [ -f /etc/bng-platform/api-remote.env ] && systemctl start bng-api-remote || true
echo "restored. Review /etc/bng-platform/config.yaml (uplink name / public IP differ per node), then:"
echo "  sudo bngctl config apply && sudo bngctl firewall apply   # confirm from a NEW ssh session"
