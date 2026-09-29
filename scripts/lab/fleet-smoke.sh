#!/usr/bin/env bash
# Phase 7 end-to-end test on one machine: this node's own TLS listener (bng-api-remote)
# is registered as a remote node, so the full console -> node path runs for real:
# certificate pinning, service token, acting-admin narrowing, HTTP + WebSocket proxy,
# fleet views, audit. Throwaway admins/token/node are removed afterwards. Exit 1 on FAIL.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "fleet-smoke.sh: run as root" >&2; exit 1; }
. /etc/bng-platform/api-remote.env 2>/dev/null || { echo "no /etc/bng-platform/api-remote.env (TLS listener off)" >&2; exit 1; }
API=http://localhost:8080
NODE_URL="https://${BNG_API_REMOTE_HOST}:${BNG_API_REMOTE_PORT}"
NS=bnglab; LAB=/etc/bng-platform/lab
PLUGIN=$(ls /usr/lib/pppd/*/rp-pppoe.so /usr/lib/pppd/*/pppoe.so 2>/dev/null | head -1)
U=fleet-smoke-$$; RO=fleet-smoke-ro-$$; TOK=smoke-console-$$; NODE=selftls-$$
T=$(mktemp -d)
fail=0
step() { printf '%-52s %s\n' "$1" "$2"; [ "$2" = PASS ] || fail=1; }
cleanup() {
  ip netns pids "$NS" 2>/dev/null | xargs -r kill 2>/dev/null
  [ -s "$T/$U.csrf" ] && curl -s -o /dev/null -b "$T/$U.jar" -H "X-CSRF-Token: $(cat "$T/$U.csrf")" -X DELETE "$API/api/nodes/$NODE"
  bngctl token revoke "$TOK" >/dev/null 2>&1
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
call() { local jar=$1; shift; curl -s -b "$T/$jar.jar" -c "$T/$jar.jar" -H "X-CSRF-Token: $(cat "$T/$jar.csrf" 2>/dev/null)" \
  -H 'Content-Type: application/json' -w '\n%{http_code}' "$@"; }
code() { call "$@" | tail -1; }
body() { call "$@" | sed '$d'; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
for j in "$U" "$RO"; do
  body "$j" -H 'X-Requested-With: bng' --data @"$T/$j.login" "$API/api/auth/login" | json 'd["csrf_token"]' > "$T/$j.csrf"
done

TOKEN=$(bngctl token create "$TOK" --role network_admin 2>/dev/null)
FP=$(bngctl tls fingerprint | cut -d' ' -f1)
step "service token created (network_admin)" "$([[ $TOKEN == bngt_* ]] && echo PASS || echo FAIL)"
GOT=$(body "$U" -d "{\"url\": \"$NODE_URL\"}" "$API/api/nodes/probe" | json 'd["fingerprint"]')
step "probe fingerprint = bngctl tls fingerprint" "$([ "$GOT" = "$FP" ] && echo PASS || echo "FAIL ($GOT)")"
ADD() { printf '{"name":"%s","url":"%s","token":"%s","fingerprint":"%s"}' "$NODE" "$NODE_URL" "$1" "$2"; }
c=$(code "$U" -d "$(ADD "$TOKEN" "$(printf '0%.0s' {1..64})")" "$API/api/nodes")
step "register with a wrong fingerprint -> 409" "$([ "$c" = 409 ] && echo PASS || echo "FAIL ($c)")"
c=$(code "$U" -d "$(ADD "bngt_$(openssl rand -hex 20)" "$FP")" "$API/api/nodes")
step "register with a wrong token -> 409" "$([ "$c" = 409 ] && echo PASS || echo "FAIL ($c)")"
c=$(code "$RO" -d "$(ADD "$TOKEN" "$FP")" "$API/api/nodes")
step "read_only cannot register nodes -> 403" "$([ "$c" = 403 ] && echo PASS || echo "FAIL ($c)")"
c=$(code "$U" -d "$(ADD "$TOKEN" "$FP")" "$API/api/nodes")
step "register node $NODE (pinned) -> 201" "$([ "$c" = 201 ] && echo PASS || echo "FAIL ($c)")"
step "token/cert files are 0600" "$([ "$(stat -c %a /etc/bng-platform/secrets/nodes/$NODE.token /etc/bng-platform/secrets/nodes/$NODE.crt | sort -u)" = 600 ] && echo PASS || echo FAIL)"

F=$(body "$U" "$API/api/fleet" | json '" ".join(x["name"] + "=" + str(x["ok"]) for x in d)')
step "GET /api/fleet ($F)" "$(echo "$F" | grep -q "$NODE=True" && echo PASS || echo FAIL)"
for ep in system/status health sessions qos/status config config/history; do
  c=$(code "$U" "$API/api/nodes/$NODE/$ep")
  step "proxied GET $ep" "$([ "$c" = 200 ] && echo PASS || echo "FAIL ($c)")"
done
c=$(code "$U" "$API/api/nodes/$NODE/auth/me")
step "proxy refuses auth/* -> 404" "$([ "$c" = 404 ] && echo PASS || echo "FAIL ($c)")"

timeout 60 ip netns exec "$NS" pppd plugin "$PLUGIN" nic-bnglab1 file "$LAB/pppd-auth" noauth nodefaultroute noipdefault \
  updetach maxfail 1 linkname bnglab mtu 1492 mru 1492 >/dev/null 2>&1 && step "lab PPPoE session up" PASS || step "lab PPPoE session up" FAIL
sleep 3
S=$(body "$U" "$API/api/fleet/sessions?search=labuser")
N=$(echo "$S" | json '" ".join(sorted({i["node"] for i in d["items"]}))')
step "fleet subscriber search finds labuser on both ($N)" "$(echo "$N" | grep -q "$NODE" && echo PASS || echo FAIL)"
SID=$(body "$U" "$API/api/nodes/$NODE/sessions?search=labuser" | json 'd["items"][0]["sid"]')

/opt/bng-platform/venv/bin/python - "$RO" "$T" "$NODE" <<'PY' > "$T/ws.out" 2>&1
import asyncio, json, sys
import websockets
ro, t, node = sys.argv[1:4]
cookie = next(l.split("\t")[-1].strip() for l in open(f"{t}/{ro}.jar") if "bng_session" in l)
async def main():
    async with websockets.connect(f"ws://localhost:8080/api/nodes/{node}/ws/metrics", open_timeout=10,
                                  additional_headers={"Origin": "http://localhost:8080", "Cookie": f"bng_session={cookie}"}) as ws:
        m = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print("ok", m["node"], m["sessions"]["active"])
asyncio.run(main())
PY
step "WebSocket through the proxy ($(tail -1 "$T/ws.out"))" "$(grep -q '^ok ' "$T/ws.out" && echo PASS || echo FAIL)"

c=$(code "$RO" -d '{}' "$API/api/nodes/$NODE/sessions/$SID/disconnect")
step "read_only disconnect via proxy -> 403 (node narrows)" "$([ "$c" = 403 ] && echo PASS || echo "FAIL ($c)")"
c=$(code "$U" -d '{}' "$API/api/nodes/$NODE/sessions/$SID/disconnect")
step "super_admin disconnect via proxy -> 200" "$([ "$c" = 200 ] && echo PASS || echo "FAIL ($c)")"
sleep 3
step "session gone from accel-ppp" "$(accel-cmd -H 127.0.0.1 -p 2001 show sessions sid | grep -q "$SID" && echo FAIL || echo PASS)"
WHO=$(tail -1 /var/lib/bng-platform/audit.jsonl | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["admin"], d["action"])')
step "node audit names the console admin ($WHO)" "$([ "$WHO" = "$U@$TOK session_disconnect" ] && echo PASS || echo FAIL)"

c=$(code "$U" -X DELETE "$API/api/nodes/$NODE")
step "remove node" "$([ "$c" = 200 ] && [ ! -e /etc/bng-platform/secrets/nodes/$NODE.token ] && echo PASS || echo "FAIL ($c)")"
echo
[ "$fail" -eq 0 ] && echo "FLEET RESULT: PASS" || echo "FLEET RESULT: FAIL"
exit "$fail"
