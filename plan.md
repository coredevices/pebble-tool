# Plan: Address Picaros Engineer Feedback on libpebble3-bridge

## Issues Identified (from review)

1. **Prebuilt Rust binary with no source** - `liblibrary_rs.so` (14MB) is committed as a blob
2. **Prebuilt JAR committed** - `libpebble3-bridge-all.jar` (5.9MB) in `pebble_tool/bridge/`
3. **Picaros Kotlin wrapper copied into source tree** - `uniffi/library_rs/library_rs.kt` (2,441 lines of generated bindings) is vendored instead of referenced as a dependency
4. **libpebble3 source files copied** - 27 files are copied from `third_party/mobileapp` submodule into the bridge source tree via `sync_and_build.sh`
5. **Polling queue is inefficient** - JS→Kotlin communication uses `_pkjsOutbox`/`_pkjsLogs`/etc. arrays polled via `eval(JSON.stringify(...))` rather than native callbacks
6. **sync_and_build.sh monkeypatching** - Script copies files and sed-patches `BlobDB.kt`, bypassing the build system
7. **Geolocation missing entirely** - Only canned Palo Alto coordinates, no real location support
8. **AppMessage transaction ID handled twice** - JS generates `_pkjsTxIdCounter`, then `sendAppMessageFromJson` ignores it and uses its own `nextTransactionId`
9. **One huge PebbleJS class** - 1,369 lines including all bootstrap JS, HTTP proxy, WebSocket bridge, event loop, AppMessage handling, BlobDB notifications, app glances

---

## Step 1: Remove prebuilt binaries from git tracking

**Problem**: `liblibrary_rs.so` (14MB) and `libpebble3-bridge-all.jar` (5.9MB) are tracked by git. GitHub has limited binary storage; these bloat the repo.

**Changes**:
- Add both paths to `.gitignore`
- `git rm --cached` both files
- Update `README.md` in the bridge directory to document how to obtain/build these artifacts
- The `.so` should be built from picaros source (`cargo build --release`)
- The `.jar` is a build output (`gradle shadowJar`) and should never be committed

**Files modified**: `.gitignore`, `libpebble3-bridge/README.md`

---

## Step 2: Reference picaros as a proper dependency instead of vendored files

**Problem**: The UniFFI-generated Kotlin bindings (`library_rs.kt`, 2,441 lines) and native `.so` are copied directly into the source tree.

**Changes**:
- Add picaros as a git submodule: `git submodule add https://github.com/coredevices/picaros third_party/picaros`
- Add a Gradle task in `build.gradle.kts` that:
  - Runs `cargo build --release` in the picaros submodule to produce `liblibrary_rs.so`
  - Runs `uniffi-bindgen generate --language kotlin` to produce the bindings `.kt` file
  - Copies both outputs to the appropriate build directories (NOT the source tree)
- Remove the vendored `uniffi/library_rs/library_rs.kt` from the source tree
- Remove the vendored `liblibrary_rs.so` from `src/main/resources/`
- The generated bindings go into `build/generated/` and the `.so` goes into the JAR at packaging time via Gradle's `processResources`

**Files modified**: `build.gradle.kts`, `settings.gradle.kts`, `.gitmodules`
**Files removed**: `src/main/kotlin/uniffi/library_rs/library_rs.kt`, `src/main/resources/linux-x86-64/liblibrary_rs.so`

---

## Step 3: Reference libpebble3 as a Gradle source dependency instead of copying files

**Problem**: 27 files are `cp`'d from the `third_party/mobileapp/libpebble3` submodule into the bridge source tree, with sed patches applied to `BlobDB.kt`.

**Changes**:
- Add `third_party/mobileapp/libpebble3` as a Gradle included build or source set in `settings.gradle.kts` / `build.gradle.kts`
- Configure the bridge to compile against libpebble3's `commonMain` sources directly using Gradle's `srcDir()` directive pointing at the submodule path
- For the BlobDB.kt incompatibility (`kotlin.time.Instant` → `UInt`): Create a thin adapter/extension in the bridge source rather than patching the upstream file. Alternatively, contribute the fix upstream to libpebble3 to accept both `Instant` and `UInt`
- For `DataBuffer.kt` (JVM-specific): Keep it in bridge source as the `actual` implementation, which is the normal KMP pattern
- For the `Logger.kt` stub: Keep it as a minimal shim, or add `co.touchlab:kermit` as a real dependency

**Files modified**: `build.gradle.kts`, `settings.gradle.kts`
**Files removed**: All 27 copied files under `src/main/kotlin/io/rebble/libpebblecommon/{packets,protocolhelpers,structmapper,metadata,util,exceptions}/`

---

## Step 4: Eliminate sync_and_build.sh

**Problem**: This script copies files, applies sed patches, and runs gradle — all of which should be handled by the build system.

**Changes**:
- After Steps 2 and 3, the sync phase is no longer needed (sources come from submodule via Gradle)
- The build phase is just `gradle shadowJar`
- Delete `sync_and_build.sh` entirely
- Add a simple build instruction to `README.md`: `git submodule update --init --recursive && gradle shadowJar`
- The JAR output stays in `build/libs/` and is referenced from Python at runtime (not committed)
- Update `pebble_tool/bridge/__init__.py` to handle the case where the JAR isn't pre-built (print a helpful error message pointing to build instructions)

**Files removed**: `sync_and_build.sh`
**Files modified**: `README.md`, `pebble_tool/bridge/__init__.py`

---

## Step 5: Replace polling queues with native picaros callbacks

**Problem**: All JS→Kotlin communication goes through global arrays (`_pkjsOutbox`, `_pkjsLogs`, etc.) that are polled by calling `eval("JSON.stringify(_pkjsOutbox.splice(0))")`. This is inefficient — it serializes to JSON, crosses the FFI boundary, parses JSON, and must be called repeatedly.

**Changes**:
- **Picaros side** (requires changes to the picaros library):
  - Add a new `JsCallback` trait/interface in the UniFFI definition that the Kotlin host implements
  - The callback interface should have methods like:
    ```
    fn on_app_message(dict_json: String, tx_id: u32)
    fn on_log(level: String, message: String)
    fn on_open_url(url: String)
    fn on_notification(title: String, body: String)
    fn on_app_glance(slices_json: String)
    fn on_ws_action(action_json: String)
    ```
  - Register these as global functions in the Boa JS context so JS can call them directly
  - When JS calls `Pebble.sendAppMessage(...)`, it calls `__native_sendAppMessage(json)` which is a Rust-registered function that invokes the Kotlin callback

- **Bridge side**:
  - Implement the `JsCallback` interface in `PebbleJS.kt`
  - Remove all `_pkjs*` global arrays from the bootstrap JS
  - Remove all `drain*()` methods
  - Replace `processPendingEvents()` with just `drainPendingWork()` (to pump the async job queue)
  - JS API functions like `Pebble.sendAppMessage()` call native functions directly instead of pushing to arrays

- **Fallback** (if picaros changes are out of scope for this PR):
  - At minimum, replace per-field JSON polling with a single consolidated queue:
    ```javascript
    var _pkjsEvents = []; // single queue, typed entries
    ```
  - Drain once per poll cycle instead of 6+ separate `eval()` calls

**Files modified**: `PebbleJS.kt` (bridge side), picaros library (Rust + UDL), regenerated `library_rs.kt`

---

## Step 6: Fix AppMessage transaction ID duplication

**Problem**: Transaction IDs are generated in two places:
1. JS side: `var txId = ++_pkjsTxIdCounter` (line 220 of bootstrap JS)
2. Kotlin side: `val txId = nextTransactionId` (line 1355 of `PebbleJS.kt`)

The JS txId is used for the success callback but then ignored — `sendAppMessageFromJson` generates a new one. This means the txId reported to the JS app doesn't match what's sent on the wire.

**Changes**:
- Pass the JS-generated `txId` through the outbox queue entry (it's already in the JSON: `{dict: resolved, txId: txId}`)
- In `sendAppMessageFromJson`, read `txId` from the queued message instead of generating a new one
- Remove the `nextTransactionId` field from `PebbleJS`
- Alternatively (if using native callbacks from Step 5): pass `txId` as a parameter to the native callback

**Files modified**: `PebbleJS.kt`

---

## Step 7: Implement real geolocation support

**Problem**: `navigator.geolocation` returns hardcoded Palo Alto coordinates. Real apps that depend on location won't work correctly.

**Changes**:
- Add a `--location` CLI argument to the bridge (format: `--location LAT,LON` or `--location auto`)
- For `--location LAT,LON`: use the provided coordinates in geolocation responses
- For `--location auto` (or as default when available): query an IP geolocation API (e.g., `ip-api.com/json`) once at startup to get approximate location
- Store the coordinates in `PebbleJS` and inject them into the bootstrap JS
- Keep `watchPosition` as single-fire (reasonable for emulator — position doesn't change)
- Without `--location`, keep current behavior (Palo Alto defaults) but print a stderr note that real geolocation isn't configured

**Files modified**: `Main.kt` (CLI arg parsing), `PebbleJS.kt` (dynamic coordinates), Python CLI (`install.py` to pass through the arg)

---

## Step 8: Break up the PebbleJS monolith class

**Problem**: `PebbleJS.kt` is 1,369 lines containing: bootstrap JS generation, HTTP proxy detection, JsFetcher implementation, WebSocket management, event loop, AppMessage handling, BlobDB notifications, app glances, and configuration triggers — all in one class.

**Changes**:
Split into focused files in `io/rebble/libpebblecommon/bridge/`:

1. **`PebbleJSBootstrap.kt`** (~200 lines)
   - `buildBootstrapJS()` function (or object) that generates the JS polyfill
   - Pebble API definition, localStorage, timers, console, XMLHttpRequest, WebSocket, geolocation stubs
   - Pure function: takes config params (tokens, watch info, appKeys, coordinates), returns JS string

2. **`PebbleJSWebSocket.kt`** (~120 lines)
   - `JavaWebSocket` inner class extracted to standalone class
   - WebSocket action draining (`drainWebSocketActions`, `pumpWebSocketEvents`)
   - WebSocket lifecycle management

3. **`PebbleJSHttpFetcher.kt`** (~100 lines)
   - `detectProxy()` function
   - `JsFetcher` implementation
   - HTTP proxy auth handling

4. **`PebbleJS.kt`** (~400 lines, trimmed)
   - Orchestrator class: holds `JsContext`, delegates to the above
   - `start()`, `stop()`, `processPendingEvents()`
   - `handleAppMessage()`, `sendAppMessageFromJson()`
   - `triggerShowConfiguration()`, `triggerWebviewClosed()`

5. **`PebbleJSBlobDB.kt`** (~150 lines)
   - `sendNotificationToWatch()`, `drainNotifications()`
   - `sendAppGlanceToWatch()`, `drainAppGlances()`

**Files added**: `PebbleJSBootstrap.kt`, `PebbleJSWebSocket.kt`, `PebbleJSHttpFetcher.kt`, `PebbleJSBlobDB.kt`
**Files modified**: `PebbleJS.kt` (significantly reduced)

---

## Execution Order

Steps 1-4 are dependency/build system cleanup and should be done together as they're interconnected. Steps 5-8 are code quality improvements that can be done independently.

**Phase A** (Build system): Steps 1 → 2 → 3 → 4 (sequential, each builds on the previous)
**Phase B** (Code quality): Steps 6, 7, 8 (can be parallelized)
**Phase C** (Picaros changes): Step 5 (requires upstream picaros changes, may be a separate PR)

### Practical consideration for this PR

Steps 2-3 require the Gradle build to compile against submodule source paths and build picaros from Rust source. This environment may not have `cargo` installed. If so, a pragmatic intermediate approach:
- Step 1: Remove binaries from git (always doable)
- Steps 2-3 (lite): Keep vendored files but add `.gitignore` entries and document the regeneration process; add a Gradle `generatePicarosBindings` task that's manually invoked
- Step 4: Simplify sync_and_build.sh rather than delete (reduce to just the BlobDB patch until upstream is fixed)
- Steps 5-8: Implement as described

This gives immediate wins while the full build system integration happens in a follow-up.
