#!/usr/bin/env bash
# bng-platform installer. Idempotent. Never edits netplan or NIC config.
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
for m in ppp_generic pppox pppoe ifb act_mirred sch_htb cls_u32 nf_conntrack nf_nat nft_nat 8021q; do
  if modinfo "$m" >/dev/null 2>&1; then printf '  %-14s ok\n' "$m"; else printf '  %-14s MISSING\n' "$m"; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "required kernel modules missing" >&2; exit 1; }
echo pppoe > /etc/modules-load.d/bng-platform.conf
modprobe pppoe

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q build-essential cmake git libssl-dev libpcre2-dev python3-venv \
  nftables iproute2 ethtool ppp iperf3 tcpdump conntrack curl postgresql openssl

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
install -m 0644 "$SRC/system/systemd/accel-ppp.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable accel-ppp.service   # started by the first 'bngctl config apply'

log "bngctl"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$SRC/backend[api]"
"$PREFIX/venv/bin/pip" install -q --force-reinstall --no-deps "$SRC/backend"   # pick up code changes on re-install
ln -sf "$PREFIX/venv/bin/bngctl" /usr/local/sbin/bngctl

log "Configuration"
install -d -m 0755 "$ETC" "$ETC/accel-ppp" "$ETC/nftables"
install -d -m 0700 "$ETC/secrets" "$STATE"
if [ ! -f "$ETC/config.yaml" ]; then
  UPLINK=$(ip route show default | awk '{print $5; exit}')
  sed "s/^uplink: .*/uplink: ${UPLINK}            # default-route interface at install time/" \
    "$SRC/system/config.example.yaml" > "$ETC/config.yaml"
  PUBLIC=$(ip -4 -o addr show dev "$UPLINK" scope global | awk '{split($4,a,"/"); print a[1]; exit}')
  sed -i "s/public_start: 192.0.2.1 .*/public_start: ${PUBLIC}   # uplink IPv4 at install time/" "$ETC/config.yaml"
  chmod 0640 "$ETC/config.yaml"
  echo "  created $ETC/config.yaml (aaa: lab, uplink: $UPLINK, nat public: $PUBLIC)"
else
  echo "  keeping existing $ETC/config.yaml"
fi

log "Forwarding"
# A BNG routes; the forward chain (policy drop) of 'bngctl firewall apply' decides what passes.
printf 'net.ipv4.ip_forward = 1\n' > /etc/sysctl.d/90-bng-platform.conf
sysctl -q -p /etc/sysctl.d/90-bng-platform.conf

log "nftables persistence"
INC='include "/etc/bng-platform/nftables/*.nft"'
if ! grep -qxF "$INC" /etc/nftables.conf; then
  cp -a /etc/nftables.conf "/etc/nftables.conf.bng-backup.$(date +%s)"
  echo "$INC" >> /etc/nftables.conf
fi
systemctl enable nftables.service

log "Management database (PostgreSQL; management state only, never in the data plane)"
DBURL="$ETC/secrets/db.url"
if [ ! -s "$DBURL" ]; then
  PW=$(openssl rand -hex 24)   # generated here, never printed
  VERB=CREATE
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='bng_api'" | grep -q 1 && VERB=ALTER
  # via stdin (printf is a builtin), so the password never shows in a process list; hex needs no quoting
  printf "%s ROLE bng_api LOGIN PASSWORD '%s';\n" "$VERB" "$PW" | sudo -u postgres psql -q -v ON_ERROR_STOP=1
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='bng_platform'" | grep -q 1 \
    || sudo -u postgres createdb -O bng_api bng_platform
  (umask 077; printf 'postgresql+psycopg://bng_api:%s@127.0.0.1:5432/bng_platform\n' "$PW" > "$DBURL")
  unset PW
  echo "  created role bng_api, database bng_platform, $DBURL (0600)"
fi
bngctl db upgrade

log "Web GUI"
if [ -f "$SRC/frontend/dist/index.html" ]; then
  rm -rf "$PREFIX/web.new" && cp -r "$SRC/frontend/dist" "$PREFIX/web.new"
  rm -rf "$PREFIX/web" && mv "$PREFIX/web.new" "$PREFIX/web"
  echo "  installed $PREFIX/web (served by bng-api)"
else
  echo "  no frontend/dist in $SRC - API only"
fi

log "easywall web console"
EWWEB=/etc/easywall/web.toml
if [ -f "$EWWEB" ]; then
  # Only the BNG console (bng-api, on loopback) may reach it: it is framed at /easywall/.
  cp -a "$EWWEB" "$EWWEB.bng-backup"
  sed -i -E 's|^bind_addr[[:space:]]*=.*|bind_addr   = "127.0.0.1:12227"   # bng-platform: reached via the BNG console|' "$EWWEB"
  sed -i -E 's|^trusted_proxies[[:space:]]*=.*|trusted_proxies = ["127.0.0.1/32"]   # bng-api proxy sends X-Forwarded-For|' "$EWWEB"
  if ! cmp -s "$EWWEB" "$EWWEB.bng-backup"; then systemctl restart easywall-web.service; fi
  grep -E '^(bind_addr|trusted_proxies)' "$EWWEB" | sed 's/^/  /'
else
  echo "  easywall not installed"
fi

log "TLS listener for a central console (bng-api-remote, off until configured)"
install -d -m 0755 "$ETC/tls"
if [ ! -f "$ETC/tls/api.crt" ]; then
  (umask 077; openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
    -subj "/CN=$(hostname)" -keyout "$ETC/tls/api.key" -out "$ETC/tls/api.crt" 2>/dev/null)
  chmod 0644 "$ETC/tls/api.crt"
fi
echo "  certificate fingerprint: $(bngctl tls fingerprint | cut -d' ' -f1)"
install -m 0644 "$SRC/system/systemd/bng-api-remote.service" /etc/systemd/system/
systemctl daemon-reload
if [ -f "$ETC/api-remote.env" ]; then
  systemctl enable bng-api-remote.service && systemctl restart bng-api-remote.service
else
  echo "  disabled: create $ETC/api-remote.env (see docs/multi-bng.md) to listen for a central console"
fi

log "bng-api"
install -m 0644 "$SRC/system/systemd/bng-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable bng-api.service
systemctl restart bng-api.service
for _ in $(seq 20); do curl -fs -o /dev/null http://127.0.0.1:8080/api/livez && break; sleep 0.5; done

log "Health"
bngctl health || true
cat <<EOF

Next:
  sudo bash $SRC/scripts/lab/lab-up.sh      # veth/netns lab subscriber
  sudo bngctl config apply                  # first start of accel-ppp
  sudo bngctl firewall apply                # then confirm from a NEW ssh session
  sudo bngctl admin create <name> --role super_admin   # first API administrator
  ssh -L 8080:127.0.0.1:8080 <node>         # GUI: http://localhost:8080   API docs: /api/docs
EOF
