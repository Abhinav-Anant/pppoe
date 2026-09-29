#!/usr/bin/env bash
# Remove bng-platform. Stopping accel-ppp drops every PPPoE session.
# Keeps /etc/bng-platform and /var/lib/bng-platform unless --purge.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "uninstall.sh: run as root" >&2; exit 1; }
PURGE=0; YES=0
for a in "$@"; do case "$a" in --purge) PURGE=1 ;; --yes) YES=1 ;; esac; done
if [ "$YES" -ne 1 ]; then
  read -r -p "This stops accel-ppp and disconnects all subscribers. Continue? [y/N] " ans
  [ "$ans" = y ] || exit 1
fi
bash "$(dirname "$0")/lab/lab-down.sh" || true
systemctl disable --now bng-api.service 2>/dev/null || true
systemctl disable --now accel-ppp.service 2>/dev/null || true
rm -f /etc/systemd/system/accel-ppp.service /etc/systemd/system/bng-api.service
systemctl daemon-reload
if [ -f /opt/bng-platform/accel-ppp.manifest ]; then xargs -d '\n' rm -f < /opt/bng-platform/accel-ppp.manifest; fi
nft delete table inet bng_filter 2>/dev/null || true
nft delete table ip bng_nat 2>/dev/null || true
rm -f /etc/sysctl.d/90-bng-platform.conf
sysctl -q -w net.ipv4.ip_forward=0
sed -i '\#^include "/etc/bng-platform/nftables/\*\.nft"$#d' /etc/nftables.conf
rm -f /usr/local/sbin/bngctl /etc/modules-load.d/bng-platform.conf
rm -rf /opt/bng-platform
if [ "$PURGE" -eq 1 ]; then
  rm -rf /etc/bng-platform /var/lib/bng-platform /var/log/accel-ppp
  sudo -u postgres dropdb --if-exists bng_platform 2>/dev/null || true
  sudo -u postgres psql -qc 'DROP ROLE IF EXISTS bng_api' 2>/dev/null || true
else
  echo "kept /etc/bng-platform, /var/lib/bng-platform and database bng_platform (use --purge to remove)"
fi
