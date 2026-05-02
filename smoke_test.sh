#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# LVRG Engine Smoke Test
# Run before every deploy to catch errors before they hit production.
#
# Usage:
#   ./smoke_test.sh              # tests prod engine
#   ./smoke_test.sh v2           # tests v2 engine
#   ./smoke_test.sh <full-url>   # tests any engine URL
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Config ────────────────────────────────────────────────────────────────────
PROD_URL="https://lvrg-engine-production.up.railway.app"
V2_URL="https://lvrg-engine-v2-production.up.railway.app"
APP_URL="https://lm-tool-production.up.railway.app"
TEST_DOMAIN="bluefootsd.com"
TIMEOUT=300
PASS=0
FAIL=0

# ── Args ─────────────────────────────────────────────────────────────────────
if [ "$1" = "v2" ]; then
  ENGINE_URL="$V2_URL"
  ENGINE_V2_FLAG="true"
  LABEL="V2"
elif [ -n "$1" ] && [[ "$1" == http* ]]; then
  ENGINE_URL="$1"
  ENGINE_V2_FLAG="false"
  LABEL="custom"
else
  ENGINE_URL="$PROD_URL"
  ENGINE_V2_FLAG="false"
  LABEL="prod"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LVRG Engine Smoke Test — $LABEL"
echo "  Engine: $ENGINE_URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

pass() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
section() { echo ""; echo "── $1 ──────────────────────────────────────"; }

# ── Test 1: Engine health ─────────────────────────────────────────────────────
section "Engine Health"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$ENGINE_URL/health")
if [ "$STATUS" = "200" ]; then
  pass "Engine /health → 200"
else
  fail "Engine /health → $STATUS (expected 200)"
fi

# ── Test 2: App health ────────────────────────────────────────────────────────
section "App Health"
APP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$APP_URL")
if [ "$APP_STATUS" = "200" ]; then
  pass "App → 200"
else
  fail "App → $APP_STATUS (expected 200)"
fi

# ── Test 3: App /api/engine route reachable ───────────────────────────────────
section "App Engine Route"
ROUTE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$APP_URL/api/engine" \
  -H "Content-Type: application/json" \
  -d '{"domain":"","engine_v2":false}')
# Expect 400 (bad request) or 200 — anything but 404/500
if [ "$ROUTE_STATUS" = "200" ] || [ "$ROUTE_STATUS" = "400" ]; then
  pass "App /api/engine route exists → $ROUTE_STATUS"
else
  fail "App /api/engine route → $ROUTE_STATUS (expected 200 or 400)"
fi

# ── Test 4: Full end-to-end build via app route ───────────────────────────────
section "Full Pipeline (no_deploy=true)"
echo "  Building $TEST_DOMAIN — this takes ~90s..."

LOG_FILE=$(mktemp)
curl -s -X POST "$APP_URL/api/engine" \
  -H "Content-Type: application/json" \
  -d "{\"domain\":\"$TEST_DOMAIN\",\"engine_v2\":$ENGINE_V2_FLAG,\"notes\":\"smoke test\",\"no_deploy\":true}" \
  --max-time $TIMEOUT \
  --no-buffer \
  -N > "$LOG_FILE" 2>&1

# Check for required SSE events
check_event() {
  local event="$1"
  local label="$2"
  if grep -q "\"type\": \"$event\"" "$LOG_FILE"; then
    pass "$label (got '$event' event)"
  else
    fail "$label (missing '$event' event)"
  fi
}

check_event "intel"  "Intel extraction"
check_event "grade"  "Site grading"
check_event "log" "text.*Site generated"  # proxied check
if grep -q "Site generated" "$LOG_FILE"; then
  pass "Claude site generation"
else
  fail "Claude site generation (no 'Site generated' log)"
fi
check_event "result" "Full pipeline result"

FINAL_STATUS=$(grep '"type": "done"' "$LOG_FILE" | grep -o '"status":"[^"]*"' | head -1)
if echo "$FINAL_STATUS" | grep -q "complete"; then
  pass "Pipeline status: complete"
else
  fail "Pipeline status: $FINAL_STATUS (expected complete)"
fi

# Check for any pipeline errors
ERRORS=$(grep '"level": "error"' "$LOG_FILE")
if [ -z "$ERRORS" ]; then
  pass "No pipeline errors"
else
  fail "Pipeline errors found:"
  echo "$ERRORS" | while read line; do
    echo "      $line"
  done
fi

rm -f "$LOG_FILE"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS+FAIL))
echo "  Results: $PASS/$TOTAL passed"
if [ $FAIL -gt 0 ]; then
  echo "  ❌ $FAIL test(s) failed — do not deploy"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 1
else
  echo "  ✅ All tests passed — safe to deploy"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 0
fi
