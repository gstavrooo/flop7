#!/bin/sh
# container-test gate: build the image, smoke it over HTTP, then Trivy-scan for
# fixable HIGH/CRITICAL findings (spec section 8).
set -eu

IMAGE="${1:-flop7:test}"
# Pin the scanner image by digest like every other tool (spec 8 / 9.3).
TRIVY_IMAGE="${TRIVY_IMAGE:-ghcr.io/aquasecurity/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb}"
ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
TMP=$(mktemp -d)
CID=""
cleanup() {
  [ -n "$CID" ] && docker rm -f "$CID" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT INT TERM

docker build -t "$IMAGE" "$ROOT"

# Choose a free host port instead of relying on a fixed one in CI.
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')

CID=$(docker run -d --name "flop7-test-$$" \
  -p "127.0.0.1:$PORT:8080" \
  -e PORT=8080 \
  -e DATABASE_URL='sqlite:////data/flop7.db' \
  -e MIGRATE_ON_START=1 \
  "$IMAGE")

BASE="http://127.0.0.1:$PORT"
attempt=0
until curl -fsS --max-time 2 "$BASE/api/health" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "test-image: container did not become healthy in 30 s" >&2
    exit 1
  fi
  sleep 1
done

sh "$ROOT/scripts/smoke.sh" "$BASE"

# Scan a saved tar instead of mounting the Docker socket (least privilege).
# --ignore-unfixed => fail only on fixable HIGH/CRITICAL; acknowledged findings
# are listed in .trivyignore.
docker save "$IMAGE" -o "$TMP/image.tar"
docker run --rm \
  -v "$TMP:/scan:ro" \
  -v "$ROOT/.trivyignore:/.trivyignore:ro" \
  "$TRIVY_IMAGE" \
  image --input /scan/image.tar \
  --severity HIGH,CRITICAL --ignore-unfixed \
  --ignorefile /.trivyignore --exit-code 1

echo "test-image: ok ($IMAGE)"
