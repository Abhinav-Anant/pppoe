#!/usr/bin/env bash
# Isolated PPPoE access network for functional tests. accel-ppp serves the host
# end of a veth pair (bnglab0); pppd in network namespace "bnglab" on the peer
# end (bnglab1) is the subscriber. The uplink/management NIC is never touched.
set -euo pipefail
NS=bnglab; HOST_IF=bnglab0; PEER_IF=bnglab1
LAB=/etc/bng-platform/lab
LAB_USER=${LAB_USER:-labuser}
LAB_RATE_KBIT=${LAB_RATE_KBIT:-20480}

ip netns list | grep -qw "$NS" || ip netns add "$NS"
ip link show "$HOST_IF" >/dev/null 2>&1 || ip link add "$HOST_IF" type veth peer name "$PEER_IF" netns "$NS"
ip link set "$HOST_IF" up
ip -n "$NS" link set lo up
ip -n "$NS" link set "$PEER_IF" up
# the host resolver is 127.0.0.53 (systemd-resolved), unreachable from the namespace
install -d /etc/netns/"$NS"
echo "nameserver 8.8.8.8" > /etc/netns/"$NS"/resolv.conf

umask 077
install -d -m 0700 "$LAB"
[ -s "$LAB/password" ] || head -c 18 /dev/urandom | base64 | tr -d '/+=' > "$LAB/password"
PW=$(cat "$LAB/password")
# chap-secrets: user server password ip rate   ('*' ip = allocate from the default pool; rate = down/up kbit/s)
printf '%s * %s * %s/%s\n' "$LAB_USER" "$PW" "$LAB_RATE_KBIT" "$LAB_RATE_KBIT" > "$LAB/chap-secrets"
printf 'user %s\npassword %s\n' "$LAB_USER" "$PW" > "$LAB/pppd-auth"
echo "lab up: $HOST_IF <-> $NS:$PEER_IF  user=$LAB_USER  rate=${LAB_RATE_KBIT} kbit/s"
