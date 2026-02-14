#!/bin/bash
# =============================================================================
# Full End-to-End Test Suite for libpebble3-bridge
#
# Tests ALL bridge features against a running QEMU Pebble emulator:
#
#   Phase 1: Connectivity (ping → Pong)
#   Phase 2: App Install (standalone, BlobDB + PutBytes)
#   Phase 3: Screenshot (JSON output, dimensions, pixel data)
#   Phase 4: Notification (standalone BlobDB → watch accepts)
#   Phase 5: Screenshot after notification (visual change)
#   Phase 6: Emulator Controls (battery, BT, button, tap, compass, etc.)
#            with screenshot comparison to verify visual impact
#   Phase 7: Data Logging commands
#   Phase 8: PKJS + E2E round-trip (install-and-logs with JS tests)
#
# Usage: ./run-full-e2e-test.sh [qemu_port]
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_DIR="$SCRIPT_DIR/.."
BRIDGE_JAR="$BRIDGE_DIR/build/libs/libpebble3-bridge-all.jar"
PBW="$SCRIPT_DIR/pkjs-test-app/build/pkjs-test-app.pbw"
TMP="/tmp/bridge-e2e"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "${GREEN}  PASS${NC} $1"; }
fail() { echo -e "${RED}  FAIL${NC} $1"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${YELLOW}  INFO${NC} $1"; }
phase() { echo; echo -e "${CYAN}=== $1 ===${NC}"; echo; }

FAILURES=0

# Create temp dir
mkdir -p "$TMP"

echo "============================================"
echo "libpebble3-bridge - Full E2E Test Suite"
echo "============================================"
echo

# --- Find QEMU port ---
if [ -n "${1:-}" ]; then
    PORT="$1"
    info "Using provided QEMU port: $PORT"
else
    QEMU_LINE=$(ps aux | grep qemu-pebble | grep -v grep | head -1 || true)
    if [ -n "$QEMU_LINE" ]; then
        PORT=$(echo "$QEMU_LINE" | grep -o 'tcp::[0-9]*,server' | head -1 | grep -o '[0-9]*')
        info "Found running QEMU on port $PORT"
    else
        echo -e "${RED}ERROR: No QEMU emulator running${NC}"
        echo "Start with: pebble install --emulator basalt"
        exit 1
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

JAR="$BRIDGE_JAR"

# Helper: run bridge command and capture stdout+stderr (with timeout)
run_bridge() {
    local stdout_file="$TMP/$1-stdout.txt"
    local stderr_file="$TMP/$1-stderr.txt"
    shift
    timeout 30 java -jar "$JAR" "$@" > "$stdout_file" 2> "$stderr_file"
    local rc=$?
    echo "$rc"
}

# Helper: compare two screenshot files, return number of differing pixels
compare_screenshots() {
    python3 -c "
import json, base64, sys
with open('$1') as f:
    d1 = json.load(f)
with open('$2') as f:
    d2 = json.load(f)
p1 = base64.b64decode(d1['data'])
p2 = base64.b64decode(d2['data'])
diff = sum(1 for a, b in zip(p1, p2) if a != b)
print(diff)
" 2>/dev/null || echo "-1"
}

# Helper: validate screenshot JSON
validate_screenshot() {
    python3 -c "
import json, base64, sys
with open('$1') as f:
    data = json.load(f)
w, h = data['width'], data['height']
pixels = base64.b64decode(data['data'])
unique = len(set(pixels))
print(f'{w} {h} {len(pixels)} {unique}')
" 2>/dev/null || echo "0 0 0 0"
}

# =========================================================================
phase "Phase 1: Connectivity (ping)"
# =========================================================================

RC=$(run_bridge ping ping "$PORT")
PING_OUT=$(cat "$TMP/ping-stdout.txt")
PING_ERR=$(cat "$TMP/ping-stderr.txt")

if [ "$RC" = "0" ]; then
    pass "ping command exits successfully (rc=0)"
else
    fail "ping command failed (rc=$RC)"
fi

if echo "$PING_OUT" | grep -q "Pong!"; then
    pass "ping received Pong! response"
else
    fail "ping did not receive Pong! (stdout: $PING_OUT)"
fi

if echo "$PING_ERR" | grep -q "Sending ping"; then
    pass "ping logged sending message"
else
    fail "ping did not log sending message"
fi

if echo "$PING_ERR" | grep -q "Negotiation complete"; then
    pass "Protocol negotiation succeeded"
else
    fail "Protocol negotiation did not complete"
fi

if echo "$PING_ERR" | grep -q "Watch firmware:"; then
    FW=$(echo "$PING_ERR" | grep "Watch firmware:" | head -1)
    pass "Watch firmware detected: $FW"
else
    fail "Watch firmware version not detected"
fi

# =========================================================================
phase "Phase 2: App Install (standalone)"
# =========================================================================

RC=$(run_bridge install install "$PORT" "$PBW" basalt)
INSTALL_OUT=$(cat "$TMP/install-stdout.txt")
INSTALL_ERR=$(cat "$TMP/install-stderr.txt")

if [ "$RC" = "0" ]; then
    pass "install command exits successfully (rc=0)"
else
    fail "install command failed (rc=$RC)"
fi

if echo "$INSTALL_OUT" | grep -q "App install succeeded"; then
    pass "App install reports success"
else
    fail "App install did not report success (stdout: $INSTALL_OUT)"
fi

if echo "$INSTALL_ERR" | grep -q "Inserting app into BlobDB"; then
    pass "BlobDB insert initiated"
else
    fail "BlobDB insert not logged"
fi

if echo "$INSTALL_ERR" | grep -q "BlobDB response:"; then
    BLOBDB_LINE=$(echo "$INSTALL_ERR" | grep "BlobDB response:" | head -1)
    if echo "$BLOBDB_LINE" | grep -q "Success"; then
        pass "BlobDB response: Success"
    else
        fail "BlobDB response was not Success: $BLOBDB_LINE"
    fi
else
    fail "No BlobDB response received during install"
fi

if echo "$INSTALL_ERR" | grep -q "AppFetchRequest"; then
    pass "Watch sent AppFetchRequest"
else
    fail "Watch did not send AppFetchRequest"
fi

if echo "$INSTALL_ERR" | grep -q "Sent AppFetchResponse(START)"; then
    pass "Bridge responded with AppFetchResponse(START)"
else
    fail "Bridge did not send AppFetchResponse"
fi

if echo "$INSTALL_ERR" | grep -q "PutBytes transfer complete"; then
    TRANSFERS=$(echo "$INSTALL_ERR" | grep -c "PutBytes transfer complete" || echo 0)
    pass "PutBytes transfers completed ($TRANSFERS)"
else
    fail "No PutBytes transfers completed"
fi

if echo "$INSTALL_ERR" | grep -q "PutBytes init OK, cookie="; then
    pass "PutBytes handshake (init → cookie)"
else
    fail "PutBytes init handshake not logged"
fi

# =========================================================================
phase "Phase 3: Screenshot (initial)"
# =========================================================================

# Wait for app to load
sleep 3

RC=$(run_bridge screenshot1 screenshot "$PORT")
if [ "$RC" = "0" ]; then
    pass "screenshot command exits successfully"
else
    fail "screenshot command failed (rc=$RC)"
fi

SHOT1="$TMP/screenshot1-stdout.txt"
SHOT1_ERR=$(cat "$TMP/screenshot1-stderr.txt")

if [ -s "$SHOT1" ]; then
    DIMS=$(validate_screenshot "$SHOT1")
    W=$(echo "$DIMS" | cut -d' ' -f1)
    H=$(echo "$DIMS" | cut -d' ' -f2)
    SIZE=$(echo "$DIMS" | cut -d' ' -f3)
    UNIQUE=$(echo "$DIMS" | cut -d' ' -f4)

    if [ "$W" = "144" ] && [ "$H" = "168" ]; then
        pass "Screenshot dimensions: ${W}x${H}"
    else
        fail "Screenshot dimensions incorrect: ${W}x${H} (expected 144x168)"
    fi

    if [ "$SIZE" -gt 0 ]; then
        pass "Screenshot has pixel data ($SIZE bytes)"
    else
        fail "Screenshot has no pixel data"
    fi

    if [ "$UNIQUE" -gt 1 ]; then
        pass "Screenshot is not blank ($UNIQUE unique pixel values)"
    else
        fail "Screenshot appears blank (only $UNIQUE unique value)"
    fi
else
    fail "Screenshot output is empty"
fi

if echo "$SHOT1_ERR" | grep -q "Screenshot captured:"; then
    pass "Screenshot capture logged"
else
    fail "Screenshot capture not logged in stderr"
fi

# =========================================================================
phase "Phase 4: Notification (standalone BlobDB)"
# =========================================================================

RC=$(run_bridge notif send-notification "$PORT" "E2E Test Alert" "This notification was sent by the bridge E2E test")
NOTIF_OUT=$(cat "$TMP/notif-stdout.txt")
NOTIF_ERR=$(cat "$TMP/notif-stderr.txt")

if [ "$RC" = "0" ]; then
    pass "send-notification exits successfully"
else
    fail "send-notification failed (rc=$RC)"
fi

if echo "$NOTIF_OUT" | grep -q "Notification sent: Success"; then
    pass "Notification BlobDB response: Success (watch accepted)"
else
    if echo "$NOTIF_OUT" | grep -q "Notification sent:"; then
        NOTIF_STATUS=$(echo "$NOTIF_OUT" | grep "Notification sent:")
        fail "Notification BlobDB response was not Success: $NOTIF_STATUS"
    else
        fail "No notification response received (stdout: $NOTIF_OUT)"
    fi
fi

if echo "$NOTIF_ERR" | grep -q "BlobDB response for notification:"; then
    pass "Notification BlobDB response logged in stderr"
else
    fail "Notification BlobDB response not logged"
fi

# =========================================================================
phase "Phase 5: Screenshot after notification (visual verification)"
# =========================================================================

# Wait for notification to render
sleep 2

RC=$(run_bridge screenshot2 screenshot "$PORT")
SHOT2="$TMP/screenshot2-stdout.txt"

if [ "$RC" = "0" ] && [ -s "$SHOT2" ]; then
    DIMS2=$(validate_screenshot "$SHOT2")
    W2=$(echo "$DIMS2" | cut -d' ' -f1)
    H2=$(echo "$DIMS2" | cut -d' ' -f2)
    if [ "$W2" = "144" ] && [ "$H2" = "168" ]; then
        pass "Post-notification screenshot valid (${W2}x${H2})"
    else
        fail "Post-notification screenshot invalid dimensions"
    fi

    # Compare with pre-notification screenshot
    DIFF=$(compare_screenshots "$SHOT1" "$SHOT2")
    if [ "$DIFF" -gt 0 ] 2>/dev/null; then
        pass "Notification caused visual change ($DIFF pixels differ)"
    elif [ "$DIFF" = "0" ]; then
        info "No visual change detected (notification may have been dismissed)"
    else
        info "Could not compare screenshots"
    fi
else
    fail "Post-notification screenshot failed"
fi

# =========================================================================
phase "Phase 6: Emulator Control Commands"
# =========================================================================

# --- 6a: emu-battery ---
echo "--- Battery control ---"

# Take baseline screenshot
sleep 1
run_bridge shot_pre_bat screenshot "$PORT" > /dev/null

RC=$(run_bridge battery1 emu-battery "$PORT" 10 0)
BAT_ERR=$(cat "$TMP/battery1-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-battery 10% exits successfully"
else
    fail "emu-battery 10% failed (rc=$RC)"
fi
if echo "$BAT_ERR" | grep -q "Sent battery: 10%, charging=false"; then
    pass "emu-battery logged: 10%, not charging"
else
    fail "emu-battery did not log expected message"
fi

# Set to 100%
RC=$(run_bridge battery2 emu-battery "$PORT" 100 1)
BAT_ERR2=$(cat "$TMP/battery2-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-battery 100% charging exits successfully"
else
    fail "emu-battery 100% charging failed"
fi
if echo "$BAT_ERR2" | grep -q "Sent battery: 100%, charging=true"; then
    pass "emu-battery logged: 100%, charging"
else
    fail "emu-battery did not log expected message"
fi

# Take post-battery screenshot and compare
sleep 1
run_bridge shot_post_bat screenshot "$PORT" > /dev/null
DIFF=$(compare_screenshots "$TMP/shot_pre_bat-stdout.txt" "$TMP/shot_post_bat-stdout.txt")
if [ "$DIFF" -gt 0 ] 2>/dev/null; then
    pass "Battery change caused visual change ($DIFF pixels differ)"
else
    info "No visual change from battery (may require specific watchface)"
fi

# --- 6b: emu-bt-connection ---
echo
echo "--- Bluetooth connection control ---"

# Take baseline screenshot before BT disconnect
run_bridge shot_pre_bt screenshot "$PORT" > /dev/null

# Disconnect BT
RC=$(run_bridge bt_off emu-bt-connection "$PORT" 0)
BT_ERR=$(cat "$TMP/bt_off-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-bt-connection disconnect exits successfully"
else
    fail "emu-bt-connection disconnect failed"
fi
if echo "$BT_ERR" | grep -q "Sent BT connection: false"; then
    pass "emu-bt-connection logged: disconnect"
else
    fail "emu-bt-connection did not log disconnect"
fi

# Note: Cannot take screenshot while BT is disconnected (SPP hangs)
# Reconnect BT immediately
sleep 1
RC=$(run_bridge bt_on emu-bt-connection "$PORT" 1)
BT_ERR2=$(cat "$TMP/bt_on-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-bt-connection reconnect exits successfully"
else
    fail "emu-bt-connection reconnect failed"
fi
if echo "$BT_ERR2" | grep -q "Sent BT connection: true"; then
    pass "emu-bt-connection logged: reconnect"
else
    fail "emu-bt-connection did not log reconnect"
fi

# Take screenshot after reconnect and compare with pre-disconnect
sleep 2
run_bridge shot_post_bt screenshot "$PORT" > /dev/null
DIFF=$(compare_screenshots "$TMP/shot_pre_bt-stdout.txt" "$TMP/shot_post_bt-stdout.txt")
if [ "$DIFF" -gt 0 ] 2>/dev/null; then
    pass "BT disconnect/reconnect caused visual change ($DIFF pixels differ)"
else
    info "No visual change from BT toggle (expected in some states)"
fi

# --- 6c: emu-button ---
echo
echo "--- Button press ---"

RC=$(run_bridge button emu-button "$PORT" 4)
BTN_ERR=$(cat "$TMP/button-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-button exits successfully"
else
    fail "emu-button failed"
fi
if echo "$BTN_ERR" | grep -q "Sent button: state=4"; then
    pass "emu-button logged: state=4 (SELECT)"
else
    fail "emu-button did not log button press"
fi

# Release button
RC=$(run_bridge button_rel emu-button "$PORT" 0)
if [ "$RC" = "0" ]; then
    pass "emu-button release exits successfully"
else
    fail "emu-button release failed"
fi

# --- 6d: emu-tap ---
echo
echo "--- Tap ---"

RC=$(run_bridge tap emu-tap "$PORT" 0 1)
TAP_ERR=$(cat "$TMP/tap-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-tap exits successfully"
else
    fail "emu-tap failed"
fi
if echo "$TAP_ERR" | grep -q "Sent tap: axis=0, direction=1"; then
    pass "emu-tap logged: axis=0, direction=1"
else
    fail "emu-tap did not log expected message"
fi

# --- 6e: emu-compass ---
echo
echo "--- Compass ---"

RC=$(run_bridge compass emu-compass "$PORT" 18000 2)
COMP_ERR=$(cat "$TMP/compass-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-compass exits successfully"
else
    fail "emu-compass failed"
fi
if echo "$COMP_ERR" | grep -q "Sent compass: heading=18000, calibration=2"; then
    pass "emu-compass logged: heading=18000, calibration=2"
else
    fail "emu-compass did not log expected message"
fi

# --- 6f: emu-time-format ---
echo
echo "--- Time format ---"

RC=$(run_bridge timefmt emu-time-format "$PORT" 1)
TF_ERR=$(cat "$TMP/timefmt-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-time-format 24h exits successfully"
else
    fail "emu-time-format 24h failed"
fi
if echo "$TF_ERR" | grep -q "Sent time format: is24Hour=true"; then
    pass "emu-time-format logged: 24h"
else
    fail "emu-time-format did not log expected message"
fi

# Switch back
RC=$(run_bridge timefmt2 emu-time-format "$PORT" 0)
if [ "$RC" = "0" ]; then
    pass "emu-time-format 12h exits successfully"
else
    fail "emu-time-format 12h failed"
fi

# --- 6g: emu-set-timeline-peek ---
echo
echo "--- Timeline peek ---"

RC=$(run_bridge tlpeek emu-set-timeline-peek "$PORT" 1)
TLP_ERR=$(cat "$TMP/tlpeek-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-set-timeline-peek enabled exits successfully"
else
    fail "emu-set-timeline-peek failed"
fi
if echo "$TLP_ERR" | grep -q "Sent timeline peek: enabled=true"; then
    pass "emu-set-timeline-peek logged: enabled=true"
else
    fail "emu-set-timeline-peek did not log expected message"
fi

RC=$(run_bridge tlpeek2 emu-set-timeline-peek "$PORT" 0)
if [ "$RC" = "0" ]; then
    pass "emu-set-timeline-peek disabled exits successfully"
else
    fail "emu-set-timeline-peek disabled failed"
fi

# --- 6h: emu-set-content-size ---
echo
echo "--- Content size ---"

RC=$(run_bridge csize emu-set-content-size "$PORT" 2)
CS_ERR=$(cat "$TMP/csize-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-set-content-size exits successfully"
else
    fail "emu-set-content-size failed"
fi
if echo "$CS_ERR" | grep -q "Sent content size: 2"; then
    pass "emu-set-content-size logged: size=2"
else
    fail "emu-set-content-size did not log expected message"
fi

# Reset content size
run_bridge csize2 emu-set-content-size "$PORT" 0 > /dev/null

# --- 6i: emu-accel ---
echo
echo "--- Accelerometer ---"

RC=$(run_bridge accel emu-accel "$PORT" 2 100,-200,1000 -50,300,-500)
ACCEL_ERR=$(cat "$TMP/accel-stderr.txt")
if [ "$RC" = "0" ]; then
    pass "emu-accel exits successfully"
else
    fail "emu-accel failed"
fi
if echo "$ACCEL_ERR" | grep -q "Sent 2 accel samples"; then
    pass "emu-accel logged: 2 samples"
else
    fail "emu-accel did not log expected message"
fi

# =========================================================================
phase "Phase 7: Data Logging"
# =========================================================================

# --- data-logging-list ---
RC=$(run_bridge dl_list data-logging-list "$PORT")
DL_OUT=$(cat "$TMP/dl_list-stdout.txt")
DL_ERR=$(cat "$TMP/dl_list-stderr.txt")

if [ "$RC" = "0" ]; then
    pass "data-logging-list exits successfully"
else
    fail "data-logging-list failed (rc=$RC)"
fi

# Verify JSON output
if python3 -c "
import json, sys
data = json.loads('''$DL_OUT''')
assert 'sessions' in data
assert isinstance(data['sessions'], list)
print('VALID ' + str(len(data['sessions'])))
" 2>/dev/null | grep -q "VALID"; then
    SESSION_COUNT=$(python3 -c "import json; print(len(json.loads('''$DL_OUT''')['sessions']))" 2>/dev/null || echo "?")
    pass "data-logging-list returns valid JSON ($SESSION_COUNT sessions)"
else
    fail "data-logging-list output is not valid JSON: $DL_OUT"
fi

# --- data-logging-get-send-enabled ---
RC=$(run_bridge dl_get data-logging-get-send-enabled "$PORT")
DL_GET_OUT=$(cat "$TMP/dl_get-stdout.txt")

if [ "$RC" = "0" ]; then
    pass "data-logging-get-send-enabled exits successfully"
else
    fail "data-logging-get-send-enabled failed"
fi

if python3 -c "
import json
data = json.loads('''$DL_GET_OUT''')
assert 'enabled' in data
print('VALID enabled=' + str(data['enabled']))
" 2>/dev/null | grep -q "VALID"; then
    pass "data-logging-get-send-enabled returns valid JSON: $DL_GET_OUT"
else
    fail "data-logging-get-send-enabled invalid JSON: $DL_GET_OUT"
fi

# --- data-logging-set-send-enabled ---
RC=$(run_bridge dl_set data-logging-set-send-enabled "$PORT" 1)
DL_SET_OUT=$(cat "$TMP/dl_set-stdout.txt")

if [ "$RC" = "0" ]; then
    pass "data-logging-set-send-enabled exits successfully"
else
    fail "data-logging-set-send-enabled failed"
fi

if echo "$DL_SET_OUT" | grep -q '"enabled":true'; then
    pass "data-logging-set-send-enabled reports enabled=true"
else
    fail "data-logging-set-send-enabled unexpected output: $DL_SET_OUT"
fi

if echo "$DL_SET_OUT" | grep -q '"status":"ENABLED"'; then
    pass "data-logging-set-send-enabled status=ENABLED"
else
    fail "data-logging-set-send-enabled missing status"
fi

# Disable and verify
RC=$(run_bridge dl_set2 data-logging-set-send-enabled "$PORT" 0)
DL_SET2_OUT=$(cat "$TMP/dl_set2-stdout.txt")
if echo "$DL_SET2_OUT" | grep -q '"enabled":false'; then
    pass "data-logging-set-send-enabled reports enabled=false"
else
    fail "data-logging-set-send-enabled disable failed: $DL_SET2_OUT"
fi

# =========================================================================
phase "Phase 8: PKJS + E2E Round-Trip (install-and-logs)"
# =========================================================================

LOG="$TMP/pkjs-e2e.log"

# Run bridge with app install + PKJS (auto-sends CMD 1 at 1s, CMD 2 at 6s, CMD 3 at 10s)
java -jar "$JAR" install-and-logs "$PORT" "$PBW" basalt > "$LOG" 2>&1 &
BRIDGE_PID=$!
sleep 40  # Wait for all tests including network + E2E round-trips

# Kill the bridge
kill $BRIDGE_PID 2>/dev/null || true
wait $BRIDGE_PID 2>/dev/null || true

info "PKJS test output captured ($LOG)"

# 8a. JS test results
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
    if [ "$JS_PASS" -gt 0 ]; then
        pass "All $JS_PASS JS tests passed"
    else
        fail "No JS tests ran"
    fi
fi

# 8b. Watch C app logs - E2E markers
echo
echo "Watch-side E2E verification:"

if grep -q "E2E_APP_STARTED" "$LOG"; then
    pass "Watch C app started"
else
    fail "Watch C app did not start"
fi

if grep -q "E2E_CMD_SENT: 1" "$LOG"; then
    pass "Watch sent CMD 1 (weather request)"
else
    fail "Watch did not send CMD 1"
fi

if grep -q "E2E_WEATHER: T=" "$LOG"; then
    WEATHER_LINE=$(grep "E2E_WEATHER" "$LOG" | head -1)
    pass "Watch received weather: $WEATHER_LINE"
else
    fail "Watch did not receive weather data"
fi

if grep -q "E2E_ACK_SENT: WEATHER:" "$LOG"; then
    pass "Watch echoed weather back to JS"
else
    fail "Watch did not echo weather back"
fi

if grep -q "E2E_CMD_SENT: 2" "$LOG"; then
    pass "Watch sent CMD 2 (config)"
else
    fail "Watch did not send CMD 2"
fi

if grep -q "E2E_STATUS: Config test OK" "$LOG"; then
    pass "Watch received config response"
else
    fail "Watch did not receive config response"
fi

if grep -q "E2E_ACK_SENT: STATUS:Config test OK" "$LOG"; then
    pass "Watch echoed config response back"
else
    fail "Watch did not echo config response"
fi

if grep -q "E2E_CMD_SENT: 3" "$LOG"; then
    pass "Watch sent CMD 3 (timeline)"
else
    fail "Watch did not send CMD 3"
fi

if grep -q "E2E_STATUS: TL:" "$LOG"; then
    pass "Watch received timeline token"
else
    fail "Watch did not receive timeline token"
fi

if grep -q "E2E_ACK_SENT: STATUS:TL:" "$LOG"; then
    pass "Watch echoed timeline token back"
else
    fail "Watch did not echo timeline token"
fi

# 8c. JS round-trip verification
echo
echo "JS E2E round-trip verification:"

if grep -q "E2E_ACK received from watch: WEATHER:" "$LOG"; then
    pass "JS received weather echo from watch"
else
    fail "JS did not receive weather echo"
fi

if grep -q "E2E weather round-trip received" "$LOG"; then
    pass "JS verified weather round-trip"
else
    fail "JS did not verify weather round-trip"
fi

if grep -q "PASS.*E2E weather temp matches" "$LOG"; then
    pass "JS confirmed weather temp matches"
else
    fail "JS weather temp mismatch"
fi

if grep -q "PASS.*E2E weather city matches" "$LOG"; then
    pass "JS confirmed weather city matches"
else
    fail "JS weather city mismatch"
fi

if grep -q "E2E_ACK received from watch: STATUS:Config" "$LOG"; then
    pass "JS received config echo from watch"
else
    fail "JS did not receive config echo"
fi

if grep -q "E2E_ACK received from watch: STATUS:TL:" "$LOG"; then
    pass "JS received timeline echo from watch"
else
    fail "JS did not receive timeline echo"
fi

# 8d. BlobDB responses (notification + glance from PKJS)
echo
echo "PKJS BlobDB verification:"

BLOBDB_COUNT=$(grep -c "E2E_BLOBDB_RESPONSE:" "$LOG" || echo 0)
if [ "$BLOBDB_COUNT" -ge 2 ]; then
    pass "BlobDB responses received ($BLOBDB_COUNT): notification + glance"
elif [ "$BLOBDB_COUNT" -ge 1 ]; then
    pass "At least one BlobDB response received ($BLOBDB_COUNT)"
else
    fail "No BlobDB responses during PKJS session"
fi

if grep -q "E2E_BLOBDB_RESPONSE: Success" "$LOG"; then
    pass "BlobDB response includes Success"
else
    fail "No BlobDB Success response"
fi

# 8e. App install within install-and-logs
if grep -q "App install succeeded" "$LOG"; then
    pass "App install succeeded within install-and-logs"
else
    fail "App install not confirmed within install-and-logs"
fi

if grep -q "PKJS runtime started" "$LOG"; then
    pass "PKJS runtime started"
else
    fail "PKJS runtime did not start"
fi

# =========================================================================
phase "Phase 9: Post-PKJS Screenshot"
# =========================================================================

# Take final screenshot to verify app rendered weather data
sleep 2
RC=$(run_bridge shot_final screenshot "$PORT")
SHOT_FINAL="$TMP/shot_final-stdout.txt"

if [ "$RC" = "0" ] && [ -s "$SHOT_FINAL" ]; then
    DIMS=$(validate_screenshot "$SHOT_FINAL")
    W=$(echo "$DIMS" | cut -d' ' -f1)
    H=$(echo "$DIMS" | cut -d' ' -f2)
    UNIQUE=$(echo "$DIMS" | cut -d' ' -f4)
    if [ "$W" = "144" ] && [ "$H" = "168" ]; then
        pass "Final screenshot dimensions: ${W}x${H}"
    else
        fail "Final screenshot dimensions: ${W}x${H}"
    fi
    if [ "$UNIQUE" -gt 2 ]; then
        pass "Final screenshot has content ($UNIQUE unique pixel values)"
    else
        fail "Final screenshot appears blank"
    fi
else
    fail "Final screenshot failed"
fi

# =========================================================================
# Final Summary
# =========================================================================

echo
echo "============================================"
echo "Test artifact files: $TMP/"
echo

TOTAL_CHECKS=$((FAILURES + $(grep -c "PASS" /dev/stdin <<< "" || true)))

if [ "$FAILURES" -eq 0 ]; then
    echo -e "${GREEN}ALL E2E CHECKS PASSED${NC}"
else
    echo -e "${RED}$FAILURES E2E CHECK(S) FAILED${NC}"
    echo
    echo "Failed checks can be debugged using:"
    echo "  Logs:          $TMP/*-stderr.txt"
    echo "  PKJS log:      $LOG"
    echo "  Screenshots:   $TMP/*-stdout.txt (JSON)"
fi
echo "============================================"

exit $FAILURES
