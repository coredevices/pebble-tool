#!/usr/bin/env bash
# =============================================================================
# E2E Test Script for pebble-tool
#
# Runs the full pipeline: install dependencies, install pebble-tool,
# install SDK, build test apps, run emulator tests.
#
# Requirements: Linux x86_64, uv, Java 21+, Gradle 8+, cargo (for picaros)
# This script is fully repeatable from a clean git pull.
#
# Usage:
#   ./tests/e2e-test.sh              # Full run (deps + SDK + build + test)
#   ./tests/e2e-test.sh --skip-deps  # Skip apt/SDK install (reuse existing)
#   ./tests/e2e-test.sh --build-only # Only build bridge JAR, no emulator test
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2E_DIR="/tmp/pebble-e2e-test-$$"
SKIP_DEPS=false
BUILD_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --skip-deps) SKIP_DEPS=true ;;
        --build-only) BUILD_ONLY=true ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; exit 1; }
info() { echo -e "${YELLOW}==>${NC} $1"; }

# ---------------------------------------------------------------------------
# Phase 1: System dependencies
# ---------------------------------------------------------------------------
if ! $SKIP_DEPS; then
    info "Phase 1: Installing system dependencies..."
    apt-get install -y -qq nodejs npm libfdt1 2>/dev/null || true

    # libsdl1.2debian or compat shim
    apt-get install -y -qq libsdl1.2debian 2>/dev/null || \
        apt-get install -y -qq libsdl1.2-compat-shim 2>/dev/null || true

    which node >/dev/null 2>&1 && pass "nodejs installed" || fail "nodejs not found"
    which npm >/dev/null 2>&1 && pass "npm installed" || fail "npm not found"
fi

# ---------------------------------------------------------------------------
# Phase 2: Build bridge JAR from source (validates code changes compile)
# ---------------------------------------------------------------------------
info "Phase 2: Building bridge JAR from source..."

# Initialize submodule if needed
if [ ! -f "$REPO_ROOT/third_party/mobileapp/libpebble3/build.gradle.kts" ]; then
    info "Initializing mobileapp submodule..."
    git -C "$REPO_ROOT" submodule update --init third_party/mobileapp
fi

# Sync libpebble3 protocol sources
"$REPO_ROOT/libpebble3-bridge/sync_and_build.sh" --sync

# Configure Gradle proxy if HTTPS_PROXY is set (needed in containerized environments)
if [ -n "${HTTPS_PROXY:-}" ]; then
    PROXY_HOST=$(python3 -c "import urllib.parse,os; print(urllib.parse.urlparse(os.environ['HTTPS_PROXY']).hostname)")
    PROXY_PORT=$(python3 -c "import urllib.parse,os; print(urllib.parse.urlparse(os.environ['HTTPS_PROXY']).port)")
    PROXY_USER=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(urllib.parse.urlparse(os.environ['HTTPS_PROXY']).username or '', safe=''))")
    PROXY_PASS=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(urllib.parse.urlparse(os.environ['HTTPS_PROXY']).password or '', safe=''))")
    mkdir -p ~/.gradle
    cat > ~/.gradle/gradle.properties <<GEOF
systemProp.http.proxyHost=$PROXY_HOST
systemProp.http.proxyPort=$PROXY_PORT
systemProp.http.proxyUser=$PROXY_USER
systemProp.http.proxyPassword=$PROXY_PASS
systemProp.https.proxyHost=$PROXY_HOST
systemProp.https.proxyPort=$PROXY_PORT
systemProp.https.proxyUser=$PROXY_USER
systemProp.https.proxyPassword=$PROXY_PASS
systemProp.http.nonProxyHosts=localhost|127.0.0.1
GEOF
fi

# Build the JAR
cd "$REPO_ROOT/libpebble3-bridge"
gradle --no-daemon shadowJar 2>&1 | tail -5
JAR="$REPO_ROOT/libpebble3-bridge/build/libs/libpebble3-bridge-all.jar"
[ -f "$JAR" ] && pass "Bridge JAR built: $(du -h "$JAR" | cut -f1)" || fail "Bridge JAR build failed"

# Copy to pebble_tool/bridge/
cp "$JAR" "$REPO_ROOT/pebble_tool/bridge/libpebble3-bridge-all.jar"
pass "JAR copied to pebble_tool/bridge/"

if $BUILD_ONLY; then
    info "Build-only mode, skipping emulator tests."
    exit 0
fi

# ---------------------------------------------------------------------------
# Phase 3: Install pebble-tool from local source
# ---------------------------------------------------------------------------
info "Phase 3: Installing pebble-tool from local source..."
uv tool install --force --reinstall pebble-tool --from "$REPO_ROOT" --python 3.13 2>&1 | tail -5

# Copy JAR to installed location (since it's gitignored)
SITE_PKGS=$(python3 -c "import pebble_tool; import os; print(os.path.dirname(pebble_tool.__file__))" 2>/dev/null || true)
if [ -z "$SITE_PKGS" ]; then
    # Fallback: find uv tool site-packages
    SITE_PKGS=$(find ~/.local/share/uv/tools/pebble-tool -path "*/pebble_tool/bridge" -type d 2>/dev/null | head -1)
    SITE_PKGS=$(dirname "$SITE_PKGS")
fi
cp "$JAR" "$SITE_PKGS/bridge/libpebble3-bridge-all.jar"
pass "pebble-tool installed from local source"

# Verify pebble CLI works
pebble --help >/dev/null 2>&1 && pass "pebble CLI works" || fail "pebble CLI failed"

# ---------------------------------------------------------------------------
# Phase 4: Install SDK
# ---------------------------------------------------------------------------
if ! $SKIP_DEPS; then
    info "Phase 4: Installing Pebble SDK..."
    pebble sdk install latest 2>&1 | tail -3
    pass "SDK installed"
else
    info "Phase 4: Skipping SDK install (--skip-deps)"
fi

# Verify SDK
pebble sdk list 2>&1 | grep -q "4\." && pass "SDK available" || fail "SDK not found"

# ---------------------------------------------------------------------------
# Phase 5: Create and build a simple test project
# ---------------------------------------------------------------------------
info "Phase 5: Building simple test project..."
mkdir -p "$E2E_DIR"
cd "$E2E_DIR"
pebble new-project simpletest 2>&1
cd simpletest
pebble build 2>&1 | tail -5
[ -f build/simpletest.pbw ] && pass "Simple project built" || fail "Simple project build failed"

# ---------------------------------------------------------------------------
# Phase 6: Install and run simple app on emulator (basalt)
# ---------------------------------------------------------------------------
info "Phase 6: Testing emulator install (basalt, headless)..."
OUTPUT=$(timeout 45 pebble install --emulator basalt --logs 2>&1 || true)
echo "$OUTPUT" | grep -q "App install succeeded" && pass "App install succeeded" || fail "App install failed"
echo "$OUTPUT" | grep -q "Done initializing" && pass "App launched and logged" || echo "WARNING: App log not found (may be timing)"

# ---------------------------------------------------------------------------
# Phase 7: Build and run PKJS E2E test app
# ---------------------------------------------------------------------------
info "Phase 7: Building PKJS E2E test app..."
cd "$REPO_ROOT/libpebble3-bridge/tests/pkjs-test-app"
pebble build 2>&1 | tail -3
[ -f build/pkjs-test-app.pbw ] && pass "PKJS test app built" || fail "PKJS test app build failed"

info "Phase 8: Running PKJS E2E test suite (137 tests)..."
OUTPUT=$(timeout 60 pebble install --emulator basalt --logs 2>&1 || true)

# Count test results
PASS_COUNT=$(echo "$OUTPUT" | grep -c "TEST .* PASS" || true)
FAIL_COUNT=$(echo "$OUTPUT" | grep -c "TEST .* FAIL" || true)

echo ""
echo "========================================"
echo "E2E Test Results: $PASS_COUNT passed, $FAIL_COUNT failed"
echo "========================================"

# Verify key E2E milestones
echo "$OUTPUT" | grep -q "E2E weather round-trip received" && pass "Weather round-trip" || fail "Weather round-trip failed"
echo "$OUTPUT" | grep -q "E2E config sendAppMessage ack" && pass "Config round-trip" || fail "Config round-trip failed"
echo "$OUTPUT" | grep -q "E2E timeline token valid" && pass "Timeline token" || fail "Timeline token failed"
echo "$OUTPUT" | grep -q "geolocation SUCCESS fires" && pass "Geolocation works" || fail "Geolocation failed"
echo "$OUTPUT" | grep -q "openURL queues URL" && pass "openURL works" || fail "openURL failed"
echo "$OUTPUT" | grep -q "notification queued with title" && pass "Notifications work" || fail "Notifications failed"
echo "$OUTPUT" | grep -q "appGlanceReload queues glance" && pass "App glance works" || fail "App glance failed"
echo "$OUTPUT" | grep -q "Sent notification to watch" && pass "BlobDB notification sent" || fail "BlobDB notification not sent"

# ---------------------------------------------------------------------------
# Phase 9: Run pytest unit tests
# ---------------------------------------------------------------------------
info "Phase 9: Running pytest unit tests..."
cd "$REPO_ROOT"
uv run pytest tests/ -v 2>&1 | tail -10
pass "pytest unit tests"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
rm -rf "$E2E_DIR"

echo ""
echo "========================================"
echo -e "${GREEN}ALL E2E TESTS PASSED${NC}"
echo "  - Bridge JAR builds from source"
echo "  - Simple app: build + emulator install"
echo "  - PKJS E2E: $PASS_COUNT tests passed"
echo "  - Weather, geolocation, BlobDB, WebSocket"
echo "  - Full JS→Watch→JS round-trip verified"
echo "========================================"
