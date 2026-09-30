#!/usr/bin/env bash
# End-to-end RADIUS test against the lab FreeRADIUS (radius-lab.sh), i.e. any standard RADIUS
# server: Access-Accept/Reject, the rate attribute becoming the session shaper, accounting
# Start/Interim/Stop, CoA rate change, Disconnect-Request, and the three rate formats accel-ppp
# understands (Filter-Id, Mikrotik-Rate-Limit, WISPr). Restores the shaper setting afterwards.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "radius-test.sh: run as root" >&2; exit 1; }
NS=bnglab; RNS=bngradius; NAS=10.255.0.1; LAB=/etc/bng-platform/lab
RADDB=/etc/freeradius/bng-lab
PLUGIN=$(ls /usr/lib/pppd/*/rp-pppoe.so | head -1)
SECRET=$(cat "$LAB/radius.secret"); PW=$(cat "$LAB/password")
ACCT=/var/log/freeradius/radacct/$NAS/detail-$(date +%Y%m%d)
fail=0; T=$(mktemp -d)
acct() {  # acct STATUS SID: one whole detail record (blank-line separated) holding both, not a grep window
  awk -v s="Acct-Session-Id = \"$2\"" -v t="Acct-Status-Type = $1" 'BEGIN{RS=""} index($0,s) && index($0,t) {f=1} END{exit !f}' "$ACCT" 2>/dev/null; }
step() { printf '%-60s %s\n' "$1" "$2"; [ "$2" = PASS ] || fail=1; }
orig=$(mktemp); cp /etc/bng-platform/config.yaml "$orig"
cleanup() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null; sleep 2
  cmp -s "$orig" /etc/bng-platform/config.yaml || bngctl config apply "$orig" >/dev/null
  rm -rf "$T" "$orig"
}
trap cleanup EXIT

connect() {  # user unit -> 0 when up
  printf 'user %s\npassword %s\n' "$1" "$PW" > "$T/auth"
  timeout 20 ip netns exec "$NS" pppd plugin "$PLUGIN" nic-bnglab1 file "$T/auth" noauth nodefaultroute \
    noipdefault updetach maxfail 1 unit "$2" mtu 1492 mru 1492 > "$T/pppd-$1.log" 2>&1
}
hangup() { ip netns exec "$NS" pkill -f "unit $1 " ; sleep 2; }
rate() {  # the shaper attaches just after IPCP: poll briefly
  local r i; for i in 1 2 3 4 5 6 7 8 9 10; do
    r=$(accel-cmd -H 127.0.0.1 -p 2001 show sessions username,rate-limit match username "^$1\$" | awk 'NR==3{print $3}')
    [ -n "$r" ] && break; sleep 0.5; done; echo "$r"; }
sid() { accel-cmd -H 127.0.0.1 -p 2001 show sessions username,sid match username "^$1\$" | awk 'NR==3{print $3}'; }
shaper() {  # apply a shaper variant: python dict literal
  /opt/bng-platform/venv/bin/python - "$1" <<'PY'
import ast, sys, yaml
c = yaml.safe_load(open("/etc/bng-platform/config.yaml"))
keep = {k: c["shaper"][k] for k in ("down_limiter", "up_limiter", "max_rate_mbit", "require_rate") if k in c["shaper"]}
c["shaper"] = {**keep, **ast.literal_eval(sys.argv[1])}
open("/tmp/config.shaper.yaml", "w").write(yaml.safe_dump(c, sort_keys=False))
PY
  bngctl config apply /tmp/config.shaper.yaml >/dev/null
}

connect labuser 700 && step "Access-Accept: labuser session up" PASS || step "Access-Accept: labuser session up" FAIL
r=$(rate labuser); step "Filter-Id 20000/20000 -> shaper ($r)" "$([ "$r" = 20000/20000 ] && echo PASS || echo FAIL)"
connect suspended 701; grep -q "account suspended\|authentication failed\|Authentication failed" "$T/pppd-suspended.log" \
  && step "Access-Reject: suspended subscriber refused" PASS || step "Access-Reject: suspended subscriber refused" FAIL
connect nosuchuser 702; [ -z "$(sid nosuchuser)" ] && step "Access-Reject: unknown subscriber refused" PASS \
  || step "Access-Reject: unknown subscriber refused" FAIL

S=$(sid labuser); sleep 3
acct Start "$S" && step "Accounting-Start received by RADIUS ($S)" PASS || step "Accounting-Start received by RADIUS ($S)" FAIL

echo "User-Name=labuser,Acct-Session-Id=$S,Filter-Id=\"10240/5120\"" | ip netns exec "$RNS" radclient -r 2 -t 3 "$NAS:3799" coa "$SECRET" > "$T/coa" 2>&1
grep -q "CoA-ACK" "$T/coa" && step "CoA-Request answered with CoA-ACK" PASS || step "CoA-Request answered with CoA-ACK ($(tail -1 "$T/coa"))" FAIL
sleep 1; r=$(rate labuser); step "CoA changed the live shaper to 10240/5120 ($r)" "$([ "$r" = 10240/5120 ] && echo PASS || echo FAIL)"

echo "waiting 70 s for an interim update (Acct-Interim-Interval 60 from RADIUS)"; sleep 70
acct Interim-Update "$S" && step "Interim-Update received by RADIUS" PASS || step "Interim-Update received by RADIUS" FAIL

echo "User-Name=labuser,Acct-Session-Id=$S" | ip netns exec "$RNS" radclient -r 2 -t 3 "$NAS:3799" disconnect "$SECRET" > "$T/dm" 2>&1
grep -q "Disconnect-ACK" "$T/dm" && step "Disconnect-Request answered with Disconnect-ACK" PASS || step "Disconnect-Request answered with Disconnect-ACK" FAIL
sleep 3; [ -z "$(sid labuser)" ] && step "session removed by Disconnect-Request" PASS || step "session removed by Disconnect-Request" FAIL
acct Stop "$S" && step "Accounting-Stop received by RADIUS" PASS || step "Accounting-Stop received by RADIUS" FAIL
echo "User-Name=labuser" | ip netns exec "$RNS" radclient -r 1 -t 2 "$NAS:3799" disconnect wrong-secret-123 > "$T/dm2" 2>&1
[ -z "$(grep -E 'Disconnect-(ACK|NAK)' "$T/dm2")" ] && step "DM with a wrong secret is ignored" PASS || step "DM with a wrong secret is ignored" FAIL
hangup 700

shaper "{'attr': 'Mikrotik-Rate-Limit', 'vendor': 'Mikrotik'}"
connect home100-1 710; r=$(rate home100-1)
step "Mikrotik-Rate-Limit \"50M/100M\" (rx/tx, M = 1000) -> 100000/50000 ($r)" "$([ "$r" = 100000/50000 ] && echo PASS || echo FAIL)"
hangup 710
shaper "{'attr_down': 'WISPr-Bandwidth-Max-Down', 'attr_up': 'WISPr-Bandwidth-Max-Up', 'vendor': 'WISPr', 'rate_multiplier': 0.001}"
connect home100-2 711; r=$(rate home100-2)
step "WISPr 100000000/50000000 bit/s -> 100000/50000 ($r)" "$([ "$r" = 100000/50000 ] && echo PASS || echo FAIL)"
hangup 711

echo
[ "$fail" -eq 0 ] && echo "RADIUS RESULT: PASS" || echo "RADIUS RESULT: FAIL"
exit "$fail"
