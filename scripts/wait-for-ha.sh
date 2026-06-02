#!/usr/bin/env bash
# Poll until the demo HA is ready to serve authenticated requests.
# Usage: ./wait-for-ha.sh [URL] [TOKEN] [TIMEOUT_S]
#   URL      default http://localhost:8127
#   TOKEN    default = baked public token from seed/.storage/.PUBLIC_TOKEN
#   TIMEOUT  default 180 seconds
set -euo pipefail

URL="${1:-http://localhost:8127}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKEN="${2:-$(cat "$HERE/seed/.storage/.PUBLIC_TOKEN" 2>/dev/null || true)}"
TIMEOUT="${3:-180}"

echo "waiting for HA at $URL (timeout ${TIMEOUT}s)..."
deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  code="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $TOKEN" "$URL/api/" 2>/dev/null || echo 000)"
  if [[ "$code" == "200" ]]; then
    echo "HA is up (HTTP 200)."
    exit 0
  fi
  sleep 3
done
echo "ERROR: HA did not become ready within ${TIMEOUT}s (last code: ${code:-none})" >&2
exit 1
