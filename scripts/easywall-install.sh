#!/usr/bin/env bash
# Install easywall (github.com/jp1337/easywall) next to bng-platform.
#
# easywall's inet easywall table has base chains at input and forward. A drop in
# any base chain is final, so with its default routing.mode = "closed" it would
# drop every subscriber packet bng_filter accepted. This script seeds
# /etc/easywall/easywall.toml with routing.mode = "open" BEFORE the package's
# postinst starts easywall-core (postinst keeps an existing file). easywall only
# enforces after its first confirmed apply, so installing it changes no rules.
set -euo pipefail
EASYWALL_VERSION=${EASYWALL_VERSION:-2.25.0}
BASE="https://github.com/jp1337/easywall/releases/download/v${EASYWALL_VERSION}"
DEB=easywall_amd64.deb

[ "$(id -u)" -eq 0 ] || { echo "easywall-install.sh: run as root" >&2; exit 1; }
[ "$(dpkg --print-architecture)" = amd64 ] || { echo "only amd64 is scripted" >&2; exit 1; }

T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
curl -fsSL -o "$T/$DEB" "$BASE/$DEB"
curl -fsSL -o "$T/checksums.txt" "$BASE/checksums.txt"
WANT=$(awk -v f="$DEB" '$2 == f || $2 == "*"f {print $1}' "$T/checksums.txt")
GOT=$(sha256sum "$T/$DEB" | awk '{print $1}')
[ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || { echo "checksum mismatch for $DEB (want ${WANT:-none}, got $GOT)" >&2; exit 1; }
echo "verified $DEB v$EASYWALL_VERSION sha256 $GOT"

if [ ! -f /etc/easywall/easywall.toml ]; then
  dpkg-deb -x "$T/$DEB" "$T/root"
  TPL=$(find "$T/root/etc/easywall" -name 'easywall.toml*' | head -1)
  [ -n "$TPL" ] || { echo "no easywall.toml template in the package" >&2; exit 1; }
  install -d -m 0750 /etc/easywall
  sed '/^\[routing\]/,/^\[/ s/^mode[[:space:]]*=.*/mode     = "open"      # bng-platform: bng_filter decides routed traffic/' \
    "$TPL" > /etc/easywall/easywall.toml
  chmod 0600 /etc/easywall/easywall.toml
fi
python3 - <<'EOF'
import sys, tomllib
mode = tomllib.load(open("/etc/easywall/easywall.toml", "rb")).get("routing", {}).get("mode")
if mode != "open":
    sys.exit(f'easywall routing.mode is "{mode}", refusing to install: it would drop subscriber traffic')
print('easywall routing.mode = "open"')
EOF

apt-get install -y -q "$T/$DEB"
systemctl is-active easywall-core easywall-web
echo
echo "UI: ssh -L 12227:localhost:12227 <this host>, then https://localhost:12227/firstrun"
echo "setup token: sudo journalctl -u easywall-web -g 'setup token' | tail -1"
echo "Open TCP 22 (and 3799/udp from Jaze for CoA) in easywall BEFORE setting firewall.host_input: easywall."
