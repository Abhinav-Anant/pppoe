#!/usr/bin/env bash
# Replace systemd-resolved with a static /etc/resolv.conf on a BNG.
#   dns-static.sh apply [NAMESERVER...]   default: the uplink DNS servers resolved uses now
#   dns-static.sh rollback                restore the resolved stub and re-enable resolved
# Why: resolved tracks every rtnetlink link; with 20-30k ppp interfaces it used ~1 core
# (Phase 8, docs/phase8-results.md). The host only needs plain upstream resolvers.
# Subscriber DNS is unaffected (accel-ppp hands out config.yaml 'dns:' via IPCP).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "dns-static.sh: run as root" >&2; exit 1; }
BACKUP=/var/lib/bng-platform/dns-static.backup
case "${1:-}" in
apply)
  shift
  servers=("$@")
  if [ ${#servers[@]} -eq 0 ]; then
    mapfile -t servers < <(awk '/^nameserver/{print $2}' /run/systemd/resolve/resolv.conf 2>/dev/null)
  fi
  [ ${#servers[@]} -gt 0 ] || servers=(1.1.1.1 8.8.8.8)
  for s in "${servers[@]}"; do
    [[ $s =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "not an IPv4 address: $s" >&2; exit 1; }
  done
  mkdir -p "$(dirname "$BACKUP")"
  [ -e "$BACKUP" ] || readlink /etc/resolv.conf > "$BACKUP" || echo "" > "$BACKUP"
  tmp=$(mktemp)
  { echo "# static resolvers written by bng-platform dns-static.sh (rollback: dns-static.sh rollback)"
    printf 'nameserver %s\n' "${servers[@]}"
    echo "options timeout:2 attempts:2"; } > "$tmp"
  # test the servers before switching: a dead resolver would break apt, easywall updates, etc.
  for s in "${servers[@]}"; do
    if ! timeout 5 python3 - "$s" <<'PY'
import socket, struct, sys, os
q = os.urandom(2) + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x06github\x03com\x00\x00\x01\x00\x01"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(q, (sys.argv[1], 53)); r = s.recv(512)
sys.exit(0 if r[:2] == q[:2] and struct.unpack("!H", r[6:8])[0] > 0 else 1)
PY
    then echo "resolver $s does not answer; nothing changed" >&2; rm -f "$tmp"; exit 1; fi
  done
  systemctl disable --now systemd-resolved.service
  rm -f /etc/resolv.conf
  install -m 0644 "$tmp" /etc/resolv.conf
  rm -f "$tmp"
  getent hosts github.com >/dev/null && echo "static DNS active: ${servers[*]}" || {
    echo "lookup failed after the switch: rolling back" >&2; exec "$0" rollback; }
  ;;
rollback)
  target=$(cat "$BACKUP" 2>/dev/null || true)
  systemctl enable --now systemd-resolved.service
  rm -f /etc/resolv.conf
  ln -s "${target:-../run/systemd/resolve/stub-resolv.conf}" /etc/resolv.conf
  rm -f "$BACKUP"
  echo "systemd-resolved restored"
  ;;
*) echo "usage: $0 apply [NAMESERVER...] | rollback" >&2; exit 2 ;;
esac
