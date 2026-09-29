#!/usr/bin/env bash
# Copy the working tree to a node: scripts/deploy.sh [ssh-host]   (default bng01)
set -euo pipefail
HOST=${1:-bng01}
cd "$(dirname "$0")/.."
# the node has no Node.js: the GUI is built here first (cd frontend && npm ci && npm run build)
[ -f frontend/dist/index.html ] || { echo "deploy.sh: build the GUI first (cd frontend && npm ci && npm run build)" >&2; exit 1; }
tar --exclude='__pycache__' --exclude='*.egg-info' --exclude='.pytest_cache' -czf - backend system scripts frontend/dist \
  | ssh "$HOST" 'sudo rm -rf /opt/bng-platform/src && sudo mkdir -p /opt/bng-platform/src && sudo tar xzf - -C /opt/bng-platform/src'
echo "deployed to $HOST:/opt/bng-platform/src"
