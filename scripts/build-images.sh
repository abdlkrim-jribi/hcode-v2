#!/usr/bin/env bash
#
# build-images.sh — build and tag the HCode v2 Docker images with a version,
# so they can be saved/loaded or pushed to a registry without rebuilding.
#
# Usage:
#   scripts/build-images.sh                 # build hcode-backend:v2 + hcode-frontend:v2
#   VERSION=v2.1 scripts/build-images.sh    # build with a custom tag
#   scripts/build-images.sh --save          # also export both images to ./dist/ as .tar
#
# ─────────────────────────────────────────────────────────────────────────────
# OFFLINE / AIR-GAPPED DELIVERY
#
# 1. On a CONNECTED build machine:
#        scripts/build-images.sh --save
#    → produces ./dist/hcode-backend-v2.tar and ./dist/hcode-frontend-v2.tar
#      (self-contained image archives; no registry needed)
#
# 2. Copy the two .tar files + docker-compose.yml + .env.docker.example to the
#    TARGET (air-gapped) machine by any means (USB, internal transfer, etc.).
#
# 3. On the TARGET machine (only Docker required, no internet):
#        docker load -i hcode-backend-v2.tar
#        docker load -i hcode-frontend-v2.tar
#        cp .env.docker.example .env     # then edit for mock or live
#        docker compose up               # uses the loaded images (see note below)
#
#    NOTE: docker-compose.yml builds by default. To run from the LOADED images
#    without rebuilding, either run the containers directly:
#        docker run -d -p 8765:8765 --env-file .env -v "$PWD/workspace:/workspace" hcode-backend:v2
#        docker run -d -p 8080:80 -e HCODE_WS_URL=ws://localhost:8765 hcode-frontend:v2
#    or pin `image:` + remove `build:` in a compose override on the target.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# Resolve repo root from this script's location (works from any CWD).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

VERSION="${VERSION:-v2}"
BACKEND_IMAGE="hcode-backend:${VERSION}"
FRONTEND_IMAGE="hcode-frontend:${VERSION}"
SAVE=0
[ "${1:-}" = "--save" ] && SAVE=1

echo "==> Building backend image: ${BACKEND_IMAGE}"
docker build -f docker/Dockerfile.backend -t "${BACKEND_IMAGE}" .

echo "==> Building frontend image: ${FRONTEND_IMAGE}"
docker build -f docker/Dockerfile.frontend -t "${FRONTEND_IMAGE}" .

echo ""
echo "==> Built and tagged:"
docker images --filter "reference=hcode-backend:${VERSION}" \
              --filter "reference=hcode-frontend:${VERSION}" \
              --format "    {{.Repository}}:{{.Tag}}  {{.Size}}"

if [ "$SAVE" -eq 1 ]; then
    mkdir -p dist
    echo ""
    echo "==> Saving images to ./dist/ for offline transfer"
    docker save -o "dist/hcode-backend-${VERSION}.tar"  "${BACKEND_IMAGE}"
    docker save -o "dist/hcode-frontend-${VERSION}.tar" "${FRONTEND_IMAGE}"
    echo "    dist/hcode-backend-${VERSION}.tar"
    echo "    dist/hcode-frontend-${VERSION}.tar"
    echo ""
    echo "    Load on the target with:"
    echo "      docker load -i hcode-backend-${VERSION}.tar"
    echo "      docker load -i hcode-frontend-${VERSION}.tar"
fi

echo ""
echo "Done."
