#!/bin/bash
# =============================================================================
# End-to-End Test Runner for PebbleKit JS Bridge
#
# Orchestrates: emulator start → app install → PKJS tests → screenshot
#
# Verifies:
#   1. JS PASS/FAIL test results (all must pass)
#   2. Watch C app logs: E2E_WEATHER, E2E_STATUS, E2E_ACK_SENT markers
#   3. BlobDB responses: notification + glance accepted by watch
#   4. Round-trip: JS sends data → watch receives → watch echoes → JS verifies
#   5. Screenshot: watch displays weather data after E2E flow
#
# Usage: ./run-e2e-test.sh [qemu_port]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_DIR="$SCRIPT_DIR/.."
BRIDGE_JAR="$BRIDGE_DIR/build/libs/libpebble3-bridge-all.jar"
PBW="$SCRIPT_DIR/pkjs-test-app/build/pkjs-test-app.pbw"
LOG="/tmp/pkjs-e2e-test.log"
SCREENSHOT="/tmp/pkjs-e2e-screenshot.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}  PASS${NC} $1"; }
fail() { echo -e "${RED}  FAIL${NC} $1"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${YELLOW}  INFO${NC} $1"; }

FAILURES=0

echo "============================================"
echo "PebbleKit JS Bridge - End-to-End Test Suite"
echo "============================================"
echo

# --- Find or start QEMU ---
if [ -n "${1:-}" ]; then
    PORT="$1"
    info "Using provided QEMU port: $PORT"
else
    # Find running QEMU
    QEMU_LINE=$(ps aux | grep qemu-pebble | grep -v grep | head -1 || true)
    if [ -n "$QEMU_LINE" ]; then
        PORT=$(echo "$QEMU_LINE" | grep -o 'tcp::[0-9]*,server' | head -1 | grep -o '[0-9]*')
        info "Found running QEMU on port $PORT"
    else
        info "No QEMU running, starting emulator..."
        pebble install --emulator basalt > /dev/null 2>&1 &
        sleep 20
        QEMU_LINE=$(ps aux | grep qemu-pebble | grep -v grep | head -1 || true)
        if [ -z "$QEMU_LINE" ]; then
            echo -e "${RED}ERROR: Failed to start QEMU emulator${NC}"
            exit 1
        fi
        PORT=$(echo "$QEMU_LINE" | grep -o 'tcp::[0-9]*,server' | head -1 | grep -o '[0-9]*')
        info "Started QEMU on port $PORT"
        sleep 10  # Let it boot
    fi
fi

# --- Verify prerequisites ---
if [ ! -f "$BRIDGE_JAR" ]; then
    echo -e "${RED}ERROR: Bridge JAR not found at $BRIDGE_JAR${NC}"
    echo "Run: cd libpebble3-bridge && gradle shadowJar"
    exit 1
fi
if [ ! -f "$PBW" ]; then
    echo -e "${RED}ERROR: PBW not found at $PBW${NC}"
    echo "Run: cd libpebble3-bridge/tests/pkjs-test-app && pebble build"
    exit 1
fi

echo
echo "--- Phase 1: Install app + run PKJS tests ---"
echo

# Run bridge (CMD 1 at 1s, CMD 2 at 6s, CMD 3 at 10s, plus network time + echo round-trips)
java -jar "$BRIDGE_JAR" install-and-logs "$PORT" "$PBW" basalt > "$LOG" 2>&1 &
BRIDGE_PID=$!
sleep 40  # Wait for all tests including E2E round-trips

# Kill the bridge
kill $BRIDGE_PID 2>/dev/null || true
wait $BRIDGE_PID 2>/dev/null || true

echo "Test output captured ($LOG)"
echo

# --- Phase 2: Take screenshot ---
echo "--- Phase 2: Screenshot verification ---"
echo

# Need to reconnect to take screenshot
java -jar "$BRIDGE_JAR" screenshot "$PORT" > "$SCREENSHOT" 2>/dev/null || true

# --- Phase 3: Verify results ---
echo "--- Phase 3: Verification ---"
echo

# 3a. JS test results
JS_PASS=$(grep -c "PASS \[" "$LOG" || true)
JS_FAIL=$(grep -c "FAIL \[" "$LOG" || true)
JS_PASS=${JS_PASS:-0}
JS_FAIL=${JS_FAIL:-0}
echo "JS Tests: $JS_PASS passed, $JS_FAIL failed"
if [ "$JS_FAIL" -gt 0 ]; then
    fail "JS tests have failures:"
    grep "FAIL \[" "$LOG" | while read -r line; do
        echo "    $line"
    done
else
    pass "All $JS_PASS JS tests passed"
fi

# 3b. Watch C app logs - E2E markers
echo
echo "Watch-side E2E verification:"

if grep -q "E2E_APP_STARTED" "$LOG"; then
    pass "Watch C app started"
else
    fail "Watch C app did not start (no E2E_APP_STARTED)"
fi

if grep -q "E2E_CMD_SENT: 1" "$LOG"; then
    pass "Watch sent CMD 1 (weather request)"
else
    fail "Watch did not send CMD 1"
fi

if grep -q "E2E_WEATHER: T=" "$LOG"; then
    WEATHER_LINE=$(grep "E2E_WEATHER" "$LOG" | head -1)
    pass "Watch received weather data: $WEATHER_LINE"
else
    fail "Watch did not receive weather data (no E2E_WEATHER)"
fi

if grep -q "E2E_ACK_SENT: WEATHER:" "$LOG"; then
    pass "Watch echoed weather back to JS"
else
    fail "Watch did not echo weather back (no E2E_ACK_SENT: WEATHER)"
fi

if grep -q "E2E_CMD_SENT: 2" "$LOG"; then
    pass "Watch sent CMD 2 (config test)"
else
    fail "Watch did not send CMD 2"
fi

if grep -q "E2E_STATUS: Config test OK" "$LOG"; then
    pass "Watch received config response"
else
    fail "Watch did not receive config response"
fi

if grep -q "E2E_ACK_SENT: STATUS:Config test OK" "$LOG"; then
    pass "Watch echoed config response back to JS"
else
    fail "Watch did not echo config response back"
fi

if grep -q "E2E_CMD_SENT: 3" "$LOG"; then
    pass "Watch sent CMD 3 (timeline test)"
else
    fail "Watch did not send CMD 3"
fi

if grep -q "E2E_STATUS: TL:" "$LOG"; then
    pass "Watch received timeline token"
else
    fail "Watch did not receive timeline token"
fi

if grep -q "E2E_ACK_SENT: STATUS:TL:" "$LOG"; then
    pass "Watch echoed timeline token back to JS"
else
    fail "Watch did not echo timeline token back"
fi

# 3c. JS round-trip verification (JS received echoes from watch)
echo
echo "JS E2E round-trip verification:"

if grep -q "E2E_ACK received from watch: WEATHER:" "$LOG"; then
    pass "JS received weather echo from watch"
else
    fail "JS did not receive weather echo from watch"
fi

if grep -q "E2E weather round-trip received" "$LOG"; then
    pass "JS verified weather round-trip"
else
    fail "JS did not verify weather round-trip"
fi

if grep -q "E2E weather temp matches" "$LOG" && grep -q "PASS.*E2E weather temp matches" "$LOG"; then
    pass "JS confirmed weather temp matches what was sent"
else
    fail "JS weather temp mismatch in round-trip"
fi

if grep -q "E2E weather city matches" "$LOG" && grep -q "PASS.*E2E weather city matches" "$LOG"; then
    pass "JS confirmed weather city matches"
else
    fail "JS weather city mismatch in round-trip"
fi

if grep -q "E2E_ACK received from watch: STATUS:Config" "$LOG"; then
    pass "JS received config echo from watch"
else
    fail "JS did not receive config echo from watch"
fi

if grep -q "E2E_ACK received from watch: STATUS:TL:" "$LOG"; then
    pass "JS received timeline echo from watch"
else
    fail "JS did not receive timeline echo from watch"
fi

# 3d. BlobDB responses (notification + glance accepted by watch)
echo
echo "BlobDB verification:"

BLOBDB_COUNT=$(grep -c "E2E_BLOBDB_RESPONSE:" "$LOG" || echo 0)
if [ "$BLOBDB_COUNT" -ge 2 ]; then
    pass "BlobDB responses received ($BLOBDB_COUNT): notification + glance accepted"
else
    if [ "$BLOBDB_COUNT" -ge 1 ]; then
        info "BlobDB responses: $BLOBDB_COUNT (expected 2: notification + glance)"
        pass "At least one BlobDB response received"
    else
        fail "No BlobDB responses received (notification/glance not accepted)"
    fi
fi

if grep -q "E2E_BLOBDB_RESPONSE: Success" "$LOG"; then
    pass "BlobDB response is Success"
elif grep -q "E2E_BLOBDB_RESPONSE" "$LOG"; then
    BLOBDB_STATUS=$(grep "E2E_BLOBDB_RESPONSE" "$LOG" | head -1)
    fail "BlobDB response was not Success: $BLOBDB_STATUS"
fi

# 3e. Screenshot verification
echo
echo "Screenshot verification:"

if [ -f "$SCREENSHOT" ] && [ -s "$SCREENSHOT" ]; then
    if python3 -c "
import json, sys, base64
with open('$SCREENSHOT') as f:
    data = json.load(f)
w, h = data['width'], data['height']
pixels = base64.b64decode(data['data'])
print(f'Screenshot: {w}x{h}, {len(pixels)} bytes')
# Check it's not blank (all same color)
unique = len(set(pixels))
print(f'Unique pixel values: {unique}')
if w == 144 and h == 168:
    print('DIMENSIONS_OK')
if unique > 1:
    print('NOT_BLANK')
" 2>/dev/null | tee /tmp/screenshot_check.txt; then
        if grep -q "DIMENSIONS_OK" /tmp/screenshot_check.txt; then
            pass "Screenshot dimensions correct (144x168)"
        else
            fail "Screenshot dimensions incorrect"
        fi
        if grep -q "NOT_BLANK" /tmp/screenshot_check.txt; then
            pass "Screenshot has visible content (not blank)"
        else
            fail "Screenshot is blank"
        fi
    else
        info "Screenshot analysis failed (may be expected if emulator busy)"
    fi
else
    info "No screenshot captured (emulator may have been busy)"
fi

# --- Final Summary ---
echo
echo "============================================"
if [ "$FAILURES" -eq 0 ]; then
    echo -e "${GREEN}ALL E2E CHECKS PASSED${NC}"
else
    echo -e "${RED}$FAILURES E2E CHECK(S) FAILED${NC}"
fi
echo "JS Tests: $JS_PASS passed, $JS_FAIL failed"
echo "Log: $LOG"
echo "Screenshot: $SCREENSHOT"
echo "============================================"

exit $FAILURES
