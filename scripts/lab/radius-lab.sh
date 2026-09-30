#!/usr/bin/env bash
# A stand-in RADIUS server for the lab and demos: FreeRADIUS 3 in its own network namespace,
# reached over a veth pair exactly like an external server (Jaze, radiusdesk, NPS...).
#   radius-lab.sh up     install/configure, start bng-radius-lab.service, print the settings
#   radius-lab.sh down   stop and remove the namespace (config and users are kept)
# BNG side: 10.255.0.1 (NAS-IP, CoA/DM listener)   RADIUS side: 10.255.0.2
# Subscribers: $RADDB/mods-config/files/authorize (plans carry Filter-Id, Mikrotik-Rate-Limit
# and WISPr attributes, so any shaper attribute choice works).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "radius-lab.sh: run as root" >&2; exit 1; }
NS=bngradius; HOST_IF=bngrad0; PEER_IF=bngrad1; NAS=10.255.0.1; SRV=10.255.0.2
LAB=/etc/bng-platform/lab; RADDB=/etc/freeradius/bng-lab   # freerad must read it; secrets stay in $LAB

MODE=${1:-up}
case "$MODE" in
down)
  systemctl disable --now bng-radius-lab.service 2>/dev/null || true
  ip link del "$HOST_IF" 2>/dev/null || true
  ip netns del "$NS" 2>/dev/null || true
  echo "radius lab down"; exit 0 ;;
up|net) ;;
*) echo "usage: $0 up|down|net" >&2; exit 2 ;;
esac

if [ "$MODE" = up ] && ! command -v freeradius >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q freeradius freeradius-utils >/dev/null
fi
# the packaged instance would listen on every host address; the lab one lives in the namespace
systemctl disable --now freeradius.service 2>/dev/null || true
systemctl mask freeradius.service >/dev/null 2>&1 || true

ip netns list | grep -qw "$NS" || ip netns add "$NS"
ip link show "$HOST_IF" >/dev/null 2>&1 || ip link add "$HOST_IF" type veth peer name "$PEER_IF" netns "$NS"
ip addr replace "$NAS/30" dev "$HOST_IF"; ip link set "$HOST_IF" up
ip -n "$NS" addr replace "$SRV/30" dev "$PEER_IF"; ip -n "$NS" link set "$PEER_IF" up; ip -n "$NS" link set lo up
[ "$MODE" = net ] && exit 0   # boot path (bng-radius-lab.service): namespace + veth only

umask 077
install -d -m 0700 "$LAB"
[ -s "$LAB/radius.secret" ] || head -c 24 /dev/urandom | base64 | tr -d '/+=,' | head -c 24 > "$LAB/radius.secret"
SECRET=$(cat "$LAB/radius.secret")
[ -s "$LAB/password" ] || head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$LAB/password"
PW=$(cat "$LAB/password")

if [ ! -d "$RADDB" ]; then
  cp -a /etc/freeradius/3.0 "$RADDB"
fi
chown -R freerad:freerad "$RADDB"
cat > "$RADDB/clients.conf" <<EOF
client bng {
	ipaddr = $NAS
	secret = $SECRET
	require_message_authenticator = no
	nas_type = other
}
client localhost {
	ipaddr = 127.0.0.1
	secret = $SECRET
}
EOF
# plans: Filter-Id "down/up" kbit, Mikrotik-Rate-Limit "rx/tx" (= up/down), WISPr bit/s
plan() {  # name down_mbit up_mbit
  printf '%s Cleartext-Password := "%s"\n\tFilter-Id := "%s/%s",\n\tMikrotik-Rate-Limit := "%sM/%sM",\n\tWISPr-Bandwidth-Max-Down := %s,\n\tWISPr-Bandwidth-Max-Up := %s,\n\tAcct-Interim-Interval := 60\n\n' \
    "$1" "$PW" "$(( $2 * 1000 ))" "$(( $3 * 1000 ))" "$3" "$2" "$(( $2 * 1000000 ))" "$(( $3 * 1000000 ))"
}
{
  echo "# bng-platform lab subscribers (radius-lab.sh); all share the lab password"
  plan labuser 20 20
  for i in $(seq 1 8); do plan "home50-$i" 50 25; done
  for i in $(seq 1 6); do plan "home100-$i" 100 50; done
  for i in $(seq 1 4); do plan "biz200-$i" 200 200; done
  for i in $(seq 1 2); do plan "biz500-$i" 500 500; done
  # simulated subscribers for demos (bng-demo.service): one username per session, e.g. sub100-42
  for spec in "sub50 50 25" "sub100 100 50" "sub200 200 100" "sub500 500 500"; do
    set -- $spec
    plan "DEFAULT User-Name =~ \"^$1-[0-9]+$\"," "$2" "$3"
  done
  printf 'suspended Cleartext-Password := "%s", Auth-Type := Reject\n\tReply-Message := "account suspended"\n\n' "$PW"
  echo "DEFAULT Auth-Type := Reject"
  printf '\tReply-Message := "unknown subscriber"\n'
} > "$RADDB/mods-config/files/authorize"
chown freerad:freerad "$RADDB/clients.conf" "$RADDB/mods-config/files/authorize"
ip netns exec "$NS" freeradius -C -d "$RADDB" >/dev/null   # config check before (re)start

cat > /etc/systemd/system/bng-radius-lab.service <<EOF
[Unit]
Description=bng-platform lab RADIUS server (FreeRADIUS in netns $NS)
After=network-online.target
# the veth (NAS address, CoA listener) must exist before accel-ppp binds dae-server to it
Before=accel-ppp.service

[Service]
ExecStartPre=/opt/bng-platform/src/scripts/lab/radius-lab.sh net
ExecStart=/usr/sbin/ip netns exec $NS /usr/sbin/freeradius -f -d $RADDB
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 /etc/systemd/system/bng-radius-lab.service   # written under umask 077
systemctl daemon-reload
systemctl enable bng-radius-lab.service >/dev/null
systemctl restart bng-radius-lab.service
sleep 2
systemctl is-active --quiet bng-radius-lab.service
cat <<EOF
radius lab up: FreeRADIUS $SRV:1812/1813 in netns $NS, NAS $NAS
config.yaml:  aaa: radius
              radius: {servers: [{address: $SRV}], nas_identifier: <node>, nas_ip_address: $NAS, coa_listen: $NAS}
secret:       sudo bngctl radius secret < $LAB/radius.secret
CoA test:     echo 'User-Name=home50-1,Filter-Id="10240/10240"' | ip netns exec $NS radclient $NAS:3799 coa \$(cat $LAB/radius.secret)
EOF
