#!/usr/bin/env bash
# Phase 8 matrix on the veth lab: untuned baseline, then 'bngctl tuning apply' and the full
# sessions x traffic matrix, then a session-setup stress run. Results:
#   /var/lib/bng-platform/benchmark-baseline/  (untuned)   /var/lib/bng-platform/benchmark-results/ (tuned)
# Tuning stays applied afterwards; undo with: sudo bngctl tuning rollback
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "bench-matrix.sh: run as root" >&2; exit 1; }
TRAFFIC=${TRAFFIC:-1,5,10,20,30,40}
SESSIONS=${SESSIONS:-"1000 5000 10000 20000 30000"}
bngctl tuning rollback >/dev/null 2>&1 || true
bngctl benchmark run --sessions 1000 --traffic "$TRAFFIC" --hold 30 --out /var/lib/bng-platform/benchmark-baseline
bngctl tuning apply
for n in $SESSIONS; do
  bngctl benchmark run --sessions "$n" --traffic "$TRAFFIC" --hold 60
done
bngctl benchmark run --sessions 30000 --rate 2000 --hold 60   # setup-rate stress, no traffic
bngctl benchmark report >/dev/null
echo "MATRIX DONE"
