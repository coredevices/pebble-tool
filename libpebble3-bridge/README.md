# libpebble3-bridge

A CLI bridge tool that connects to the QEMU Pebble emulator and provides app installation, log streaming, screenshot capture, emulator control, data logging, and **PebbleKit JS (PKJS)** support using the Pebble protocol.

## Architecture

The bridge communicates with QEMU using QemuSPP framing and implements the full Pebble protocol for app management. PebbleKit JS support is powered by [Picaros](https://github.com/coredevices/picaros) — a Kotlin Multiplatform library wrapping the **Boa** JavaScript engine (Rust) via UniFFI/JNA.

```
┌─────────────────────┐     QemuSPP      ┌──────────────────┐
│  libpebble3-bridge   │◄────────────────►│   QEMU Pebble    │
│                      │   (TCP socket)   │    Emulator       │
│  ┌────────────────┐  │                  └──────────────────┘
│  │   PebbleJS.kt  │  │
│  │  (PKJS runtime)│  │
│  │                │  │
│  │ ┌────────────┐ │  │
│  │ │ JS Polyfill│ │  │   ◄── Pebble API, localStorage,
│  │ │ Bootstrap  │ │  │       XMLHttpRequest, timers
│  │ └─────┬──────┘ │  │
│  │       │        │  │
│  │ ┌─────▼──────┐ │  │
│  │ │  Picaros   │ │  │   ◄── Boa JS engine (Rust/UniFFI)
│  │ │  (Boa)     │ │  │
│  │ └─────┬──────┘ │  │
│  │       │        │  │
│  │ ┌─────▼──────┐ │  │
│  │ │ JsFetcher  │ │  │   ◄── HTTP delegate (HttpURLConnection)
│  │ └────────────┘ │  │
│  └────────────────┘  │
└─────────────────────┘
```

### PKJS Design

Since the Boa engine is accessed via UniFFI (no direct native callbacks from JS to Kotlin), the PKJS layer uses a **consolidated event queue** pattern:

- A **JS bootstrap polyfill** defines the `Pebble` object, `localStorage`, `XMLHttpRequest`, `WebSocket`, `navigator.geolocation`, and timer functions
- All outgoing JS events (AppMessages, logs, notifications, etc.) are pushed to a single typed queue (`_pkjsEvents`)
- Kotlin periodically calls `eval()` to drain this queue and dispatch events to the appropriate handlers
- Events (ready, appmessage, etc.) are fired from Kotlin into JS via `eval("_pkjsFireEvent(...)")`
- HTTP requests from `XMLHttpRequest` use the Boa engine's native `fetch()`, which delegates to a `JsFetcher` implementation backed by `HttpURLConnection`
- The `--location` CLI flag allows configuring geolocation coordinates (defaults to IP-based lookup or Palo Alto fallback)

## Prerequisites

- **Java 21+** — `apt install openjdk-21-jre-headless` or equivalent
- **Gradle 8+** — for building from source
- **Rust toolchain** — only needed if rebuilding the Picaros native library

## Building

```bash
# First time: initialize submodules and sync libpebble3 source
git submodule update --init --recursive
./sync_and_build.sh

# Or step by step:
./sync_and_build.sh --sync    # Sync libpebble3 source from submodule
./sync_and_build.sh --build   # Build the fat JAR only

# Output: build/libs/libpebble3-bridge-all.jar
# Copied to: pebble_tool/bridge/libpebble3-bridge-all.jar
```

### Source file origins

The Kotlin source tree contains files from multiple origins:

| Origin | Path | Regeneration |
|--------|------|-------------|
| **Bridge (custom)** | `bridge/Main.kt`, `bridge/PebbleJS*.kt` | Hand-written, not synced |
| **libpebble3 (submodule)** | `packets/`, `protocolhelpers/`, `structmapper/`, `metadata/`, `exceptions/` | `./sync_and_build.sh --sync` |
| **Picaros (UniFFI)** | `uniffi/library_rs/library_rs.kt` | See "Rebuilding the Picaros native library" |
| **JVM platform** | `util/DataBuffer.kt`, `co/touchlab/kermit/Logger.kt` | Hand-written platform stubs |

The synced and generated files are listed in `.gitignore` and should not be committed.
To regenerate after updating the submodule: `./sync_and_build.sh --sync`.

## Usage

The bridge is invoked as a CLI tool. All commands take a QEMU TCP port as the first argument.

### Install and run an app with PKJS

```bash
# Start the emulator first (via pebble-tool)
pebble build
pebble install --emulator basalt

# Or invoke the bridge directly:
java -jar build/libs/libpebble3-bridge-all.jar install-and-logs <qemu_port> <path/to/app.pbw> <platform>
```

The `install-and-logs` command:
1. Installs the PBW onto the emulator
2. Extracts `pebble-js-app.js` from the PBW
3. Starts the Picaros JS engine with the PKJS polyfill
4. Streams app logs and JS console output
5. Handles AppMessage bidirectionally between the watch and JS

### Other commands

```bash
# Install app only (no log streaming)
java -jar libpebble3-bridge-all.jar install <port> <pbw> <platform>

# Stream logs only
java -jar libpebble3-bridge-all.jar logs <port>

# Test connectivity
java -jar libpebble3-bridge-all.jar ping <port>

# Take a screenshot (JSON output on stdout)
java -jar libpebble3-bridge-all.jar screenshot <port>

# Emulator control
java -jar libpebble3-bridge-all.jar emu-tap <port> <axis:0-2> <dir:+1/-1>
java -jar libpebble3-bridge-all.jar emu-button <port> <state_bitmask>
java -jar libpebble3-bridge-all.jar emu-battery <port> <pct:0-100> <charging:0/1>
java -jar libpebble3-bridge-all.jar emu-bt-connection <port> <connected:0/1>

# Data logging
java -jar libpebble3-bridge-all.jar data-logging-list <port>
```

## Testing the PKJS engine

### Quick smoke test (no emulator needed)

You can verify the Picaros JS engine loads and works by writing a small test class:

```kotlin
// src/main/kotlin/TestPicaros.kt
package io.rebble.libpebblecommon.bridge

import uniffi.library_rs.*

fun main() {
    val fetcher = object : JsFetcher {
        override suspend fun fetch(request: JsRequestKt): JsResponseKt {
            throw FetcherException.NetworkException("test mode")
        }
    }

    val ctx = JsContext(fetcher)

    // Basic eval
    println(ctx.eval("1 + 2"))              // "3"
    println(ctx.eval("JSON.stringify({a:1})")) // {"a":1}

    // Test fetch (requires network)
    // val result = ctx.evalAsync("""
    //     fetch('https://jsonplaceholder.typicode.com/todos/1')
    //         .then(r => r.json())
    //         .then(d => d.title)
    // """.trimIndent())
    // println(result)

    ctx.close()
}
```

Build and run:

```bash
gradle shadowJar
java -cp build/libs/libpebble3-bridge-all.jar io.rebble.libpebblecommon.bridge.TestPicarosKt
```

### Testing with a weather watchface

1. Build a Pebble app that has a `pebble-js-app.js` (or `src/pkjs/index.js`):

```javascript
// pebble-js-app.js — example weather fetcher
Pebble.addEventListener('ready', function() {
    console.log('PebbleKit JS ready!');

    var req = new XMLHttpRequest();
    req.onload = function() {
        var json = JSON.parse(this.responseText);
        var temp = Math.round(json.main.temp - 273.15);
        console.log('Temperature: ' + temp + 'C');
        Pebble.sendAppMessage({0: temp});
    };
    req.open('GET', 'https://api.openweathermap.org/data/2.5/weather?q=London&appid=YOUR_KEY');
    req.send();
});

Pebble.addEventListener('appmessage', function(e) {
    console.log('Got message from watch: ' + JSON.stringify(e.payload));
});
```

2. Build the PBW and run with the bridge:

```bash
pebble build
java -jar build/libs/libpebble3-bridge-all.jar install-and-logs 12344 build/basalt/app.pbw basalt
```

3. You should see:
   - `[bridge] PKJS runtime started for YourApp`
   - `[HH:MM:SS] [JS log] PebbleKit JS ready!`
   - `[HH:MM:SS] [JS log] Temperature: 15C`
   - `[pkjs] Sent AppMessage with 1 tuples`

### Supported PKJS APIs

| API | Status |
|-----|--------|
| `Pebble.addEventListener` / `removeEventListener` | Supported |
| `Pebble.sendAppMessage(dict, ack, nack)` | Supported |
| `Pebble.getAccountToken()` / `getWatchToken()` | Supported (returns static tokens) |
| `Pebble.openURL(url)` | Logs URL (no browser in CLI) |
| `Pebble.showSimpleNotificationOnPebble(title, body)` | Logs notification |
| `Pebble.getActiveWatchInfo()` | Supported (returns emulator info) |
| `XMLHttpRequest` | Supported (via fetch delegate) |
| `localStorage` | Supported (in-memory, not persisted) |
| `setTimeout` / `setInterval` / `clearTimeout` / `clearInterval` | Supported (via Promise microtasks) |
| `console.log/info/warn/error/debug` | Supported (captured + printed) |
| `fetch()` | Supported natively by Boa engine |
| Events: `ready`, `appmessage` | Supported |
| Events: `showConfiguration`, `webviewclosed` | Logged only (no webview in CLI) |

## Rebuilding the Picaros native library

If you need to modify or rebuild the Rust engine:

```bash
# Clone picaros
git clone https://github.com/coredevices/picaros.git
cd picaros/library

# Build the native library
cargo build --release

# Generate UniFFI Kotlin bindings (one-time setup: add [[bin]] to Cargo.toml)
cargo build --release --bin uniffi-bindgen
./target/release/uniffi-bindgen generate \
    --library target/release/liblibrary_rs.so \
    --language kotlin \
    --out-dir generated-kotlin

# Copy artifacts into the bridge
cp target/release/liblibrary_rs.so \
    /path/to/pebble-tool/libpebble3-bridge/src/main/resources/linux-x86-64/
cp generated-kotlin/uniffi/library_rs/library_rs.kt \
    /path/to/pebble-tool/libpebble3-bridge/src/main/kotlin/uniffi/library_rs/

# Rebuild the bridge
cd /path/to/pebble-tool/libpebble3-bridge
gradle shadowJar
```

## Project structure

```
libpebble3-bridge/
├── build.gradle.kts                          # Gradle build (JNA, kotlinx deps)
├── settings.gradle.kts                       # Gradle settings
├── sync_and_build.sh                        # Sync libpebble3 source + build JAR
├── src/main/
│   ├── kotlin/
│   │   ├── io/rebble/libpebblecommon/bridge/
│   │   │   ├── Main.kt                      # CLI entry point + QemuBridge
│   │   │   ├── PebbleJS.kt                  # PKJS runtime orchestrator
│   │   │   ├── PebbleJSBootstrap.kt        # JS polyfill generation
│   │   │   ├── PebbleJSWebSocket.kt        # WebSocket connection management
│   │   │   ├── PebbleJSHttpFetcher.kt      # HTTP proxy + JsFetcher
│   │   │   └── PebbleJSBlobDB.kt           # BlobDB notification + glance
│   │   └── uniffi/library_rs/
│   │       └── library_rs.kt                # Generated UniFFI/JNA bindings (not committed)
│   └── resources/
│       └── linux-x86-64/
│           └── liblibrary_rs.so             # Picaros native library (not committed)
├── tests/
│   ├── run-e2e-test.sh                      # PKJS + watch round-trip E2E tests
│   ├── run-full-e2e-test.sh                 # Comprehensive bridge E2E tests
│   └── pkjs-test-app/                       # Test PBW app for E2E suite
└── README.md
```
