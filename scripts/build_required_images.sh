#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NF_DOCKERFILE="$ROOT_DIR/docker/Dockerfile.nf"
ENDPOINT_DOCKERFILE="$ROOT_DIR/docker/Dockerfile.endpoint"

echo "[*] Building required NF images (fw, nat)"

if [[ ! -f "$NF_DOCKERFILE" ]]; then
  echo "ERROR: Missing $NF_DOCKERFILE"
  exit 1
fi

docker build -f "$NF_DOCKERFILE" -t fw:latest "$ROOT_DIR"
docker build -f "$NF_DOCKERFILE" -t nat:latest "$ROOT_DIR"

if [[ -f "$ENDPOINT_DOCKERFILE" ]]; then
  echo "[*] Building endpoint image (optional)"
  docker build -f "$ENDPOINT_DOCKERFILE" -t endpoint:latest "$ROOT_DIR"
fi

echo "[*] Image build complete"
