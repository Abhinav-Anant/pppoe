#!/usr/bin/env bash
# One-shot setup of a fresh node: git clone <repo> && sudo bash scripts/bootstrap.sh [admin-name]
# Builds the GUI if it is missing, runs install.sh (ACCEL-PPP, bngctl, PostgreSQL, easywall, services)
# and creates the first super_admin. Does NOT start PPPoE or touch the firewall: see the printed steps.
set -euo pipefail
SRC=$(cd "$(dirname "$0")/.." && pwd)
ADMIN=${1:-admin}
[ "$(id -u)" -eq 0 ] || { echo "bootstrap.sh: run as root" >&2; exit 1; }

if [ ! -f "$SRC/frontend/dist/index.html" ]; then   # dist is not in git
  echo "==> building the GUI"
  if ! command -v npm >/dev/null; then apt-get update -q && apt-get install -y -q nodejs npm; fi
  (cd "$SRC/frontend" && npm ci --no-audit --no-fund && npm run build) \
    || echo "GUI build failed - continuing API only (build elsewhere and copy frontend/dist)" >&2
fi

bash "$SRC/scripts/install.sh"

if ! bngctl admin list | grep -q .; then
  PW=$(openssl rand -base64 18 | tr -d '/+=')
  printf '%s\n' "$PW" | bngctl admin create "$ADMIN" --role super_admin --password-stdin
  echo
  echo "First administrator: $ADMIN   password: $PW   (change it: bngctl admin passwd $ADMIN)"
fi
cat <<MSG

Remaining manual steps (they change live traffic, so they are not automated):
  1. edit /etc/bng-platform/config.yaml  (uplink, pools, RADIUS/aaa; lab mode by default)
  2. sudo bngctl config apply            # starts accel-ppp
  3. sudo bngctl firewall apply          # then 'sudo bngctl firewall confirm' from a NEW ssh session within 120 s
  4. restore a previous node's settings/data instead of 1:  sudo bash $SRC/scripts/restore.sh <backup.tar.gz>
MSG
