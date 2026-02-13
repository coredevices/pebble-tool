#!/usr/bin/env bash
# sync_and_build.sh - Sync libpebble3 protocol source from submodule and build the bridge JAR.
#
# Usage:
#   ./libpebble3-bridge/sync_and_build.sh          # Sync + build
#   ./libpebble3-bridge/sync_and_build.sh --sync    # Sync only (no build)
#   ./libpebble3-bridge/sync_and_build.sh --build   # Build only (no sync)
#
# Prerequisites:
#   - Java 21+ (for Kotlin compilation)
#   - Gradle 8.x (or use the gradle wrapper if available)
#   - git submodule initialized: git submodule update --init third_party/mobileapp

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BRIDGE_DIR="$SCRIPT_DIR"
SUBMODULE_DIR="$REPO_ROOT/third_party/mobileapp"
LIBPEBBLE3_SRC="$SUBMODULE_DIR/libpebble3/src/commonMain/kotlin"
BLOBANNOTATIONS_SRC="$SUBMODULE_DIR/blobannotations/src/commonMain/kotlin"
BRIDGE_SRC="$BRIDGE_DIR/src/main/kotlin"

# Output JAR location
JAR_DEST="$REPO_ROOT/pebble_tool/bridge/libpebble3-bridge-all.jar"

DO_SYNC=true
DO_BUILD=true

if [[ "${1:-}" == "--sync" ]]; then
    DO_BUILD=false
elif [[ "${1:-}" == "--build" ]]; then
    DO_SYNC=false
fi

# ---------------------------------------------------------------------------
# Sync libpebble3 source files from submodule
# ---------------------------------------------------------------------------
sync_sources() {
    echo "=== Syncing libpebble3 sources from submodule ==="

    if [[ ! -d "$SUBMODULE_DIR/libpebble3" ]]; then
        echo "ERROR: Submodule not found. Run:"
        echo "  git submodule update --init third_party/mobileapp"
        exit 1
    fi

    # --- Files copied unmodified from libpebble3 commonMain ---
    # These are synced directly; any local modifications will be overwritten.

    local COMMON_FILES=(
        # Protocol helpers
        "io/rebble/libpebblecommon/protocolhelpers/PebblePacket.kt"
        "io/rebble/libpebblecommon/protocolhelpers/PacketRegistry.kt"
        "io/rebble/libpebblecommon/protocolhelpers/ProtocolEndpoint.kt"

        # Struct mapper
        "io/rebble/libpebblecommon/structmapper/StructMappable.kt"
        "io/rebble/libpebblecommon/structmapper/StructMapper.kt"
        "io/rebble/libpebblecommon/structmapper/types.kt"

        # Packets
        "io/rebble/libpebblecommon/packets/AppFetch.kt"
        "io/rebble/libpebblecommon/packets/AppLog.kt"
        "io/rebble/libpebblecommon/packets/AppMessage.kt"
        "io/rebble/libpebblecommon/packets/AppReorder.kt"
        "io/rebble/libpebblecommon/packets/AppRunState.kt"
        "io/rebble/libpebblecommon/packets/Audio.kt"
        "io/rebble/libpebblecommon/packets/DataLogging.kt"
        "io/rebble/libpebblecommon/packets/Emulator.kt"
        "io/rebble/libpebblecommon/packets/GetBytes.kt"
        "io/rebble/libpebblecommon/packets/HealthSync.kt"
        "io/rebble/libpebblecommon/packets/LogDump.kt"
        "io/rebble/libpebblecommon/packets/Music.kt"
        "io/rebble/libpebblecommon/packets/PhoneControl.kt"
        "io/rebble/libpebblecommon/packets/PutBytes.kt"
        "io/rebble/libpebblecommon/packets/Reset.kt"
        "io/rebble/libpebblecommon/packets/Screenshot.kt"
        "io/rebble/libpebblecommon/packets/System.kt"
        "io/rebble/libpebblecommon/packets/Voice.kt"

        # BlobDB packets (except BlobDB.kt which is modified)
        "io/rebble/libpebblecommon/packets/blobdb/App.kt"
        "io/rebble/libpebblecommon/packets/blobdb/BlobDB2.kt"
        "io/rebble/libpebblecommon/packets/blobdb/Timeline.kt"
        "io/rebble/libpebblecommon/packets/blobdb/TimelineIcon.kt"

        # Metadata
        "io/rebble/libpebblecommon/metadata/WatchHardwarePlatform.kt"
        "io/rebble/libpebblecommon/metadata/WatchType.kt"

        # Utilities
        "io/rebble/libpebblecommon/util/Endian.kt"
        "io/rebble/libpebblecommon/exceptions/packet.kt"
    )

    local synced=0
    local failed=0
    for file in "${COMMON_FILES[@]}"; do
        local src="$LIBPEBBLE3_SRC/$file"
        local dest="$BRIDGE_SRC/$file"
        if [[ -f "$src" ]]; then
            mkdir -p "$(dirname "$dest")"
            cp "$src" "$dest"
            synced=$((synced + 1))
        else
            echo "  WARNING: Source not found: $file"
            failed=$((failed + 1))
        fi
    done

    # --- File from blobannotations module ---
    local BLOB_SRC="$BLOBANNOTATIONS_SRC/coredev/GenerateRoomEntity.kt"
    local BLOB_DEST="$BRIDGE_SRC/coredev/GenerateRoomEntity.kt"
    if [[ -f "$BLOB_SRC" ]]; then
        mkdir -p "$(dirname "$BLOB_DEST")"
        cp "$BLOB_SRC" "$BLOB_DEST"
        synced=$((synced + 1))
    else
        echo "  WARNING: blobannotations GenerateRoomEntity.kt not found"
        failed=$((failed + 1))
    fi

    echo "  Synced $synced files ($failed warnings)"

    # --- Apply patches to modified files ---
    echo "  Applying patches to modified files..."

    # Patch BlobDB.kt: Replace kotlin.time.Instant with UInt parameter
    local BLOBDB_SRC="$LIBPEBBLE3_SRC/io/rebble/libpebblecommon/packets/blobdb/BlobDB.kt"
    local BLOBDB_DEST="$BRIDGE_SRC/io/rebble/libpebblecommon/packets/blobdb/BlobDB.kt"
    if [[ -f "$BLOBDB_SRC" ]]; then
        cp "$BLOBDB_SRC" "$BLOBDB_DEST"
        # Remove the Instant import
        sed -i '/import kotlin\.time\.Instant/d' "$BLOBDB_DEST"
        # Replace Instant parameter with UInt
        sed -i 's/timestamp: Instant,/timestampSecs: UInt,/' "$BLOBDB_DEST"
        # Replace the timestamp field initialization
        sed -i 's/timestamp\.epochSeconds\.toUInt()/timestampSecs/' "$BLOBDB_DEST"
        echo "  Patched BlobDB.kt (Instant -> UInt)"
    fi

    echo ""
    echo "  NOTE: The following files are NOT synced (custom bridge code):"
    echo "    - io/rebble/libpebblecommon/bridge/Main.kt (bridge CLI)"
    echo "    - co/touchlab/kermit/Logger.kt (Kermit logging stub)"
    echo "    - io/rebble/libpebblecommon/util/DataBuffer.kt (JVM implementation)"
    echo ""
    echo "=== Sync complete ==="
}

# ---------------------------------------------------------------------------
# Build the bridge JAR
# ---------------------------------------------------------------------------
build_jar() {
    echo "=== Building libpebble3-bridge JAR ==="

    # Find gradle
    local GRADLE=""
    if command -v gradle &>/dev/null; then
        GRADLE="gradle"
    elif [[ -x "/opt/gradle/bin/gradle" ]]; then
        GRADLE="/opt/gradle/bin/gradle"
    elif [[ -x "$BRIDGE_DIR/gradlew" ]]; then
        GRADLE="$BRIDGE_DIR/gradlew"
    else
        echo "ERROR: Gradle not found. Install Gradle 8.x or add gradle wrapper."
        exit 1
    fi

    echo "  Using: $GRADLE"

    cd "$BRIDGE_DIR"
    $GRADLE --no-daemon shadowJar 2>&1 | tail -5

    local JAR="$BRIDGE_DIR/build/libs/libpebble3-bridge-all.jar"
    if [[ ! -f "$JAR" ]]; then
        echo "ERROR: Build failed - JAR not found at $JAR"
        exit 1
    fi

    # Copy to pebble_tool/bridge/
    mkdir -p "$(dirname "$JAR_DEST")"
    cp "$JAR" "$JAR_DEST"
    echo "  JAR copied to: $JAR_DEST"
    echo "  Size: $(du -h "$JAR_DEST" | cut -f1)"
    echo ""
    echo "=== Build complete ==="
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo "libpebble3-bridge sync & build"
echo ""

if $DO_SYNC; then
    sync_sources
fi

if $DO_BUILD; then
    build_jar
fi

echo ""
echo "Done. To update libpebble3 to latest upstream:"
echo "  cd $(basename "$REPO_ROOT")"
echo "  git submodule update --remote third_party/mobileapp"
echo "  ./libpebble3-bridge/sync_and_build.sh"
