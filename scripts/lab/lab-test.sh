#!/usr/bin/env bash
# Phase 1 end-to-end test on the veth lab:
# PPPoE discovery -> auth (chap-secrets) -> IPCP from pool -> visible in accel
# -> gateway reachable -> per-session shaper present and enforced both ways
# -> disconnect via bngctl. Prints PASS/FAIL per step; exit 1 on any FAIL.
set -uo pipefail
NS=bnglab; PEER_IF=bnglab1; LAB=/etc/bng-platform/lab
PLUGIN=$(ls /usr/lib/pppd/*/rp-pppoe.so /usr/lib/pppd/*/pppoe.so 2>/dev/null | head -1)
RATE=$(awk '{split($5, r, "/"); print r[1]; exit}' "$LAB/chap-secrets")
fail=0
step() { printf '%-34s %s\n' "$1" "$2"; [ "$2" = PASS ] || fail=1; }
cleanup() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null
  pkill -f 'iperf3 -s -1 -B' 2>/dev/null
  for h in $(nft -a list chain inet bng_filter input 2>/dev/null | awk '/bng-lab/{print $NF}'); do
    nft delete rule inet bng_filter input handle "$h"
  done
}
trap cleanup EXIT

timeout 60 ip netns exec "$NS" pppd plugin "$PLUGIN" "nic-$PEER_IF" file "$LAB/pppd-auth" \
  noauth nodefaultroute noipdefault updetach maxfail 1 linkname bnglab mtu 1492 mru 1492 \
  lcp-echo-interval 10 lcp-echo-failure 3 > /tmp/bnglab-pppd.log 2>&1 \
  && step "PPPoE session up" PASS || { step "PPPoE session up" FAIL; cat /tmp/bnglab-pppd.log; exit 1; }

read -r CLIENT_IP GW < <(ip -n "$NS" -j -4 addr show dev ppp0 | python3 -c \
  'import json,sys; a=json.load(sys.stdin)[0]["addr_info"][0]; print(a["local"], a["address"])')
step "IP assigned ($CLIENT_IP, gw $GW)" "$([ -n "${CLIENT_IP:-}" ] && echo PASS || echo FAIL)"

SES=$(bngctl sessions --json --search labuser)
read -r SID IFNAME SIP < <(echo "$SES" | python3 -c \
  'import json,sys; s=json.load(sys.stdin)[0]; print(s["sid"], s["ifname"], s["ip"])')
step "Session in accel ($SID on $IFNAME)" "$([ "${SIP:-}" = "$CLIENT_IP" ] && echo PASS || echo FAIL)"

ip netns exec "$NS" ping -c 3 -W 2 "$GW" >/dev/null && step "Gateway reachable" PASS || step "Gateway reachable" FAIL

tc qdisc show dev "$IFNAME" | grep -q tbf && step "Download shaper (tbf) on $IFNAME" PASS || step "Download shaper (tbf) on $IFNAME" FAIL
tc filter show dev "$IFNAME" ingress | grep -q police && step "Upload policer on $IFNAME" PASS || step "Upload policer on $IFNAME" FAIL

nft list table inet bng_filter >/dev/null 2>&1 && \
  nft insert rule inet bng_filter input iifname "ppp*" tcp dport 5201 accept comment '"bng-lab"'
measure() {  # $1 = extra iperf3 client flag (-R = download); prints Mbit/s received
  iperf3 -s -1 -B "$GW" >/dev/null 2>&1 &
  sleep 1
  ip netns exec "$NS" iperf3 -c "$GW" -t 8 -O 2 -J $1 | python3 -c \
    'import json,sys; print(round(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"]/1e6, 2))'
}
LIMIT=$(python3 -c "print($RATE/1000)")
for dir in download upload; do
  flag=$([ $dir = download ] && echo -R || echo "")
  mbps=$(measure "$flag")
  ok=$(python3 -c "print('PASS' if 0.5*$LIMIT <= $mbps <= 1.10*$LIMIT else 'FAIL')")
  step "$dir ${mbps} Mbit/s (limit ${LIMIT})" "$ok"
done

gone() { [ -z "$(bngctl sessions --json --search labuser | python3 -c 'import json,sys; print(json.load(sys.stdin) or "")')" ]; }
bngctl session disconnect "$SID" >/dev/null
for _ in $(seq 20); do gone && break; sleep 0.5; done
gone && step "Disconnect via bngctl" PASS || step "Disconnect via bngctl" FAIL

echo; [ "$fail" -eq 0 ] && echo "LAB RESULT: PASS" || echo "LAB RESULT: FAIL"
exit "$fail"
