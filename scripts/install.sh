#!/usr/bin/env bash
# bng-platform installer (Phase 1). Idempotent. Never edits netplan or NIC config.
set -euo pipefail
ACCEL_PPP_VERSION=1.14.0
SRC=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/opt/bng-platform
ETC=/etc/bng-platform
STATE=/var/lib/bng-platform

[ "$(id -u)" -eq 0 ] || { echo "install.sh: run as root" >&2; exit 1; }
log() { printf '\n==> %s\n' "$*"; }

log "System"
. /etc/os-release
echo "OS:     $PRETTY_NAME"
echo "Kernel: $(uname -r)"
echo "CPU:    $(nproc) x $(lscpu | sed -n 's/^Model name: *//p')"
echo "RAM:    $(free -h | awk '/^Mem:/{print $2}')"
echo "NICs:"; ip -br link | grep -v '^lo ' | sed 's/^/        /'
case "$ID" in ubuntu|debian) ;; *) echo "unsupported distribution: $ID" >&2; exit 1 ;; esac

log "Kernel modules"
missing=0
for m in ppp_generic pppox pppoe sch_cake ifb act_mirred sch_htb cls_u32 nf_conntrack nf_nat nft_nat 8021q; do
  if modinfo "$m" >/dev/null 2>&1; then printf '  %-14s ok\n' "$m"; else printf '  %-14s MISSING\n' "$m"; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "required kernel modules missing" >&2; exit 1; }
echo pppoe > /etc/modules-load.d/bng-platform.conf
modprobe pppoe

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q build-essential cmake git libssl-dev libpcre2-dev python3-venv \
  nftables iproute2 ethtool ppp iperf3 tcpdump

log "ACCEL-PPP $ACCEL_PPP_VERSION"
if /usr/local/sbin/accel-pppd -V 2>/dev/null | grep -qx "accel-ppp $ACCEL_PPP_VERSION"; then
  echo "  already installed"
else
  B=$(mktemp -d)
  git clone -q --depth 1 --branch "$ACCEL_PPP_VERSION" https://github.com/accel-ppp/accel-ppp "$B/src"
  cmake -S "$B/src" -B "$B/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
    -DRADIUS=TRUE -DSHAPER=TRUE -DLOG_PGSQL=FALSE -DNETSNMP=FALSE -DLUA=FALSE \
    -DBUILD_IPOE_DRIVER=FALSE -DBUILD_VLAN_MON_DRIVER=FALSE
  cmake --build "$B/build" -j"$(nproc)"
  cmake --install "$B/build"
  install -D -m 0644 "$B/build/install_manifest.txt" "$PREFIX/accel-ppp.manifest"
  rm -rf "$B"
  ldconfig
fi
/usr/local/sbin/accel-pppd -V

log "systemd"
install -m 0644 "$SRC/system/systemd/accel-ppp.service" /etc/systemd/system/accel-ppp.service
systemctl daemon-reload
systemctl enable accel-ppp.service   # started by the first 'bngctl config apply'

log "bngctl"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC/backend"
ln -sf "$PREFIX/venv/bin/bngctl" /usr/local/sbin/bngctl

log "Configuration"
install -d -m 0755 "$ETC" "$ETC/accel-ppp" "$ETC/nftables"
install -d -m 0700 "$ETC/secrets" "$STATE"
if [ ! -f "$ETC/config.yaml" ]; then
  UPLINK=$(ip route show default | awk '{print $5; exit}')
  sed "s/^uplink: .*/uplink: ${UPLINK}            # default-route interface at install time/" \
    "$SRC/system/config.example.yaml" > "$ETC/config.yaml"
  chmod 0640 "$ETC/config.yaml"
  echo "  created $ETC/config.yaml (aaa: lab, uplink: $UPLINK)"
else
  echo "  keeping existing $ETC/config.yaml"
fi

log "nftables persistence"
INC='include "/etc/bng-platform/nftables/*.nft"'
if ! grep -qxF "$INC" /etc/nftables.conf; then
  cp -a /etc/nftables.conf "/etc/nftables.conf.bng-backup.$(date +%s)"
  echo "$INC" >> /etc/nftables.conf
fi
systemctl enable nftables.service

log "Health"
bngctl health || true
cat <<EOF

Next:
  sudo bash $SRC/scripts/lab/lab-up.sh      # veth/netns lab subscriber
  sudo bngctl config apply                  # first start of accel-ppp
  sudo bngctl firewall apply                # then confirm from a NEW ssh session
EOF
