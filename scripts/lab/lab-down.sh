#!/usr/bin/env bash
set -uo pipefail
ip netns pids bnglab 2>/dev/null | xargs -r kill 2>/dev/null
sleep 1
ip netns del bnglab 2>/dev/null    # removes bnglab1 and, with it, bnglab0
echo "lab down"
