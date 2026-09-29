#!/usr/bin/env bash
# Copy the working tree to a node: scripts/deploy.sh [ssh-host]   (default bng01)
set -euo pipefail
HOST=${1:-bng01}
cd "$(dirname "$0")/.."
tar --exclude='__pycache__' --exclude='*.egg-info' --exclude='.pytest_cache' -czf - backend system scripts \
  | ssh "$HOST" 'sudo rm -rf /opt/bng-platform/src && sudo mkdir -p /opt/bng-platform/src && sudo tar xzf - -C /opt/bng-platform/src'
echo "deployed to $HOST:/opt/bng-platform/src"
