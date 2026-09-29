#!/usr/bin/env bash
# Phase 5 end-to-end test of bng-api on this node, against the veth lab subscriber.
# Uses a throwaway super_admin with a random password (never printed) and deletes it.
# Read endpoints, RBAC/CSRF refusals, session list + disconnect through the API, and
# an API config apply that must be audited. Exit 1 on any FAIL.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "api-smoke.sh: run as root" >&2; exit 1; }
API=http://localhost:8080
NS=bnglab; PEER_IF=bnglab1; LAB=/etc/bng-platform/lab
PLUGIN=$(ls /usr/lib/pppd/*/rp-pppoe.so /usr/lib/pppd/*/pppoe.so 2>/dev/null | head -1)
U=api-smoke-$$; RO=api-smoke-ro-$$
T=$(mktemp -d)
fail=0
step() { printf '%-44s %s\n' "$1" "$2"; [ "$2" = PASS ] || fail=1; }
cleanup() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null
  bngctl admin delete "$U" >/dev/null 2>&1; bngctl admin delete "$RO" >/dev/null 2>&1
  rm -rf "$T"
}
trap cleanup EXIT

for who in "$U super_admin" "$RO read_only"; do
  set -- $who
  openssl rand -hex 24 > "$T/$1.pw"
  bngctl admin create "$1" --role "$2" --password-stdin < "$T/$1.pw" >/dev/null
  python3 -c 'import json,sys; print(json.dumps({"username": sys.argv[1], "password": open(sys.argv[2]).read().strip()}))' \
    "$1" "$T/$1.pw" > "$T/$1.login"
done

# $1 = cookie jar name; rest = curl args. Prints body, then the HTTP code on the last line.
call() { local jar=$1; shift; curl -s -b "$T/$jar.jar" -c "$T/$jar.jar" -H "X-CSRF-Token: $(cat "$T/$jar.csrf" 2>/dev/null)" \
  -w '\n%{http_code}' "$@"; }
code() { call "$@" | tail -1; }
body() { call "$@" | sed '$d'; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
login() {
  body "$1" -H 'X-Requested-With: bng' -H 'Content-Type: application/json' --data @"$T/$1.login" \
    "$API/api/auth/login" | json 'd["csrf_token"]' > "$T/$1.csrf"
}

login "$U"; login "$RO"
step "login (super_admin, read_only)" "$([ -s "$T/$U.csrf" ] && [ -s "$T/$RO.csrf" ] && echo PASS || echo FAIL)"
step "unauthenticated -> 401" "$([ "$(code none "$API/api/sessions")" = 401 ] && echo PASS || echo FAIL)"

for ep in system/status health interfaces pppoe/status radius/status qos/status qos/config nat/status \
          nat/pools ip-pools metrics config config/history audit audit/node users; do
  c=$(code "$U" "$API/api/$ep")
  step "GET /api/$ep" "$([ "$c" = 200 ] && echo PASS || echo "FAIL ($c)")"
done
H=$(body "$U" "$API/api/health" | json '" ".join(c["name"]+"="+c["status"] for c in d["checks"] if c["status"]!="PASS")')
step "API health has no FAIL (${H:-all PASS})" "$(echo "$H" | grep -q FAIL && echo FAIL || echo PASS)"

timeout 60 ip netns exec "$NS" pppd plugin "$PLUGIN" "nic-$PEER_IF" file "$LAB/pppd-auth" \
  noauth nodefaultroute noipdefault updetach maxfail 1 linkname bnglab mtu 1492 mru 1492 \
  > /tmp/bnglab-pppd.log 2>&1 && step "lab PPPoE session up" PASS || step "lab PPPoE session up" FAIL
sleep 3   # past the API's 2 s session cache
S=$(body "$RO" "$API/api/sessions?search=labuser")
SID=$(echo "$S" | json 'd["items"][0]["sid"] if d["items"] else ""')
RATE=$(echo "$S" | json 'd["items"][0]["rate_down_kbit"] if d["items"] else ""')
step "GET /api/sessions finds labuser ($SID, ${RATE} kbit)" "$([ -n "$SID" ] && echo PASS || echo FAIL)"
step "session detail" "$([ "$(code "$RO" "$API/api/sessions/$SID")" = 200 ] && echo PASS || echo FAIL)"

c=$(code "$RO" -X POST -H 'Content-Type: application/json' -d '{}' "$API/api/sessions/$SID/disconnect")
step "read_only disconnect -> 403" "$([ "$c" = 403 ] && echo PASS || echo "FAIL ($c)")"
c=$(curl -s -o /dev/null -w '%{http_code}' -b "$T/$U.jar" -X POST -H 'Content-Type: application/json' -d '{}' \
  "$API/api/sessions/$SID/disconnect")
step "disconnect without CSRF token -> 403" "$([ "$c" = 403 ] && echo PASS || echo "FAIL ($c)")"
c=$(code "$U" -X POST -H 'Content-Type: application/json' -d '{}' "$API/api/sessions/$SID/disconnect")
step "super_admin disconnect -> 200" "$([ "$c" = 200 ] && echo PASS || echo "FAIL ($c)")"
sleep 3
gone=$(accel-cmd -H 127.0.0.1 -p 2001 show sessions sid | grep -c "$SID")
step "session gone from accel-ppp" "$([ "$gone" = 0 ] && echo PASS || echo FAIL)"

before=$(bngctl config history | wc -l)
body "$U" "$API/api/config" | json 'json.dumps({"config": d["config"]})' > "$T/cfg.json"
c=$(code "$U" -X POST -H 'Content-Type: application/json' --data @"$T/cfg.json" "$API/api/config/validate")
step "POST /api/config/validate (current config)" "$([ "$c" = 200 ] && echo PASS || echo "FAIL ($c)")"
body "$U" "$API/api/qos/config" > "$T/qos.json"
qos() {  # PUT the shaper with max_rate_mbit + $1, print the result
  python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["shaper"]; s["max_rate_mbit"]=(s.get("max_rate_mbit") or 1000)+int(sys.argv[2]); print(json.dumps({"shaper": s}))'     "$T/qos.json" "$1" > "$T/qos-put.json"
  body "$U" -X PUT -H 'Content-Type: application/json' --data @"$T/qos-put.json" "$API/api/qos/config"
}
R1=$(qos 1); R2=$(qos 0)   # change, then restore
echo "    $R1"; echo "    $R2"
after=$(bngctl config history | wc -l)
src=$(bngctl config history | tail -1 | awk '{print $4}')
step "PUT /api/qos/config applied twice (health gate)"   "$([ "$after" -eq $((before + 2)) ] && [ "$src" = api:127.0.0.1 ] && echo PASS || echo FAIL)"
R=$(body "$U" -X PUT -H 'Content-Type: application/json' --data @"$T/qos-put.json" "$API/api/qos/config")
step "unchanged config -> no new version" "$(echo "$R" | grep -q '"no changes"' && echo PASS || echo FAIL)"
c=$(code "$U" -X POST -H 'Content-Type: application/json' -d '{"config": {"node": "x; reboot"}}' "$API/api/config/apply")
step "invalid config -> 422" "$([ "$c" = 422 ] && echo PASS || echo "FAIL ($c)")"
step "accel-ppp still active" "$(systemctl is-active --quiet accel-ppp && echo PASS || echo FAIL)"

A=$(body "$U" "$API/api/audit?limit=50" | json '" ".join(sorted({a["action"] for a in d}))')
step "DB audit has login/disconnect/config ($A)" \
  "$(for x in login session_disconnect config_apply; do echo "$A" | grep -qw $x || echo miss; done | grep -q miss && echo FAIL || echo PASS)"

echo
[ "$fail" -eq 0 ] && echo "API RESULT: PASS" || echo "API RESULT: FAIL"
exit "$fail"
