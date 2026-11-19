#!/bin/sh
# Deploy smoke (spec 9.2 step 6): create a game and walk the public URL through
# the full create -> 403 -> 200 -> 409 -> SSE path. Usage: smoke.sh [base-url]
set -eu

BASE="${1:-http://127.0.0.1:8080}"
BASE="${BASE%/}"
fail() { echo "smoke: $*" >&2; exit 1; }

# 1. Health endpoint.
body=$(curl -fsS --max-time 10 "$BASE/api/health") || fail "health request failed"
printf '%s' "$body" | python3 -c 'import json,sys; assert json.load(sys.stdin).get("ok") is True' \
  || fail "health response is not ok"

# 2. Frontend is served.
curl -fsS --max-time 10 "$BASE/" | grep -q 'FLOP7' || fail "frontend page not served"

# 3. Create a game; capture the one-time seat token and version.
created=$(curl -fsS --max-time 10 -X POST "$BASE/api/games" \
  -H 'Content-Type: application/json' \
  -d '{"players":["Smoke A","Smoke B"]}') || fail "game creation failed"
read -r GAME_ID TOKEN VERSION <<EOF
$(printf '%s' "$created" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["id"], d["seats"][0]["token"], d["version"])')
EOF
[ -n "$GAME_ID" ] || fail "create response had no game id"

MOVE="$BASE/api/games/$GAME_ID/stay"
BODY="{\"seat\":0,\"version\":$VERSION}"
FOREIGN_TOKEN="wrong-token"  # never a real credential; kept in a variable for the secret scanner

# 4. A foreign seat token is rejected with 403.
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$MOVE" \
  -H 'Content-Type: application/json' -H "X-Seat-Token: $FOREIGN_TOKEN" -d "$BODY")
[ "$code" = 403 ] || fail "expected 403 for a foreign token, got $code"

# 5. The owner can stay (200).
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$MOVE" \
  -H 'Content-Type: application/json' -H "X-Seat-Token: $TOKEN" -d "$BODY")
[ "$code" = 200 ] || fail "expected 200 for stay, got $code"

# 6. Repeating the same stale move is rejected with 409, not drawn twice.
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -X POST "$MOVE" \
  -H 'Content-Type: application/json' -H "X-Seat-Token: $TOKEN" -d "$BODY")
[ "$code" = 409 ] || fail "expected 409 for a stale move, got $code"

# 7. The SSE stream emits retry:1000 then a data line with the current version.
sse=$(curl -sS -N --max-time 8 "$BASE/api/games/$GAME_ID/events?seconds=2" || true)
printf '%s' "$sse" | grep -q '^retry: 1000' || fail "SSE stream missing retry:1000"
printf '%s' "$sse" | grep -q '^data: ' || fail "SSE stream missing data line"
printf '%s' "$sse" | grep -m1 '^data: ' | python3 -c \
  'import json,sys; line=sys.stdin.read().strip()[6:]; assert json.loads(line)["version"] == 1' \
  || fail "SSE data did not carry version 1"

echo "smoke: ok ($BASE)"
