#!/usr/bin/env bash
set -euo pipefail

# Build both Docker images required by gvisor-sandbox-api.
#
# sandbox-executor:latest  — runs untrusted code inside gVisor (used on-demand
#                            by sandbox_engine.py, never started by compose).
# gvisor-sandbox-api-sandbox-api:latest — the FastAPI service container.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Building sandbox executor image (sandbox-executor:latest)..."
docker build \
    --file "${SCRIPT_DIR}/Dockerfile" \
    --tag sandbox-executor:latest \
    "${SCRIPT_DIR}"

echo "==> Building API service image (gvisor-sandbox-api-sandbox-api:latest)..."
docker build \
    --file "${SCRIPT_DIR}/Dockerfile.api" \
    --tag gvisor-sandbox-api-sandbox-api:latest \
    "${SCRIPT_DIR}"

echo ""
echo "Build complete. Start the service with:"
echo "  docker compose up -d"
