package io.rebble.libpebblecommon.bridge

import io.rebble.libpebblecommon.packets.AppMessage
import io.rebble.libpebblecommon.packets.AppMessageTuple
import kotlinx.serialization.json.*
import uniffi.library_rs.JsContext
import java.security.MessageDigest
import kotlin.uuid.Uuid

/**
 * PebbleKit JavaScript runtime using Picaros (Boa engine).
 *
 * Orchestrator class that delegates to focused components:
 * - [PebbleJSBootstrap] — JS polyfill generation
 * - [PebbleJSWebSocket] — WebSocket connection management
 * - [PebbleJSHttpFetcher] — HTTP proxy + JsFetcher
 * - [PebbleJSBlobDB] — BlobDB notification + glance
 *
 * Changes from the original monolith:
 * - Consolidated event queue: all JS→Kotlin events go through `_pkjsEvents[]`
 *   with typed entries, drained in a single eval() call per poll cycle.
 * - Transaction ID fix: JS-generated txId is passed through to the wire
 *   protocol, eliminating the duplicate counter.
 * - Geolocation: configurable coordinates via constructor parameter.
 */
class PebbleJS(
    private val bridge: QemuBridge,
    private val jsSource: String,
    private val appUuid: Uuid,
    private val appKeys: Map<String, Int> = emptyMap(),
    private val geoLatitude: Double = 37.4419,
    private val geoLongitude: Double = -122.1430,
) {
    private var jsContext: JsContext? = null
    private val webSocketManager = PebbleJSWebSocket()

    /**
     * Generate deterministic token by hashing seed with SHA-256.
     */
    private fun deterministicToken(seed: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(seed.toByteArray())
            .joinToString("") { "%02x".format(it) }
            .take(32)
    }

    fun start() {
        try {
            jsContext = JsContext(PebbleJSHttpFetcher.createFetcher())

            // Load the bootstrap polyfill (with real watch info baked in)
            val bootstrapJS = PebbleJSBootstrap.build(
                accountToken = deterministicToken("account:${bridge.watchSerial}"),
                watchToken = deterministicToken("watch:${bridge.watchSerial}:$appUuid"),
                timelineToken = deterministicToken("timeline:$appUuid"),
                watchPlatform = bridge.watchPlatform,
                watchModel = bridge.watchModel,
                watchLanguage = bridge.watchLanguage,
                watchFwMajor = bridge.watchFwMajor,
                watchFwMinor = bridge.watchFwMinor,
                watchFwPatch = bridge.watchFwPatch,
                watchFwSuffix = bridge.watchFwSuffix,
                geoLatitude = geoLatitude,
                geoLongitude = geoLongitude,
            )
            jsContext!!.eval(bootstrapJS)

            // Inject appKeys mapping from appinfo.json
            if (appKeys.isNotEmpty()) {
                val keysJs = appKeys.entries.joinToString(", ") { (k, v) ->
                    "'${k.replace("'", "\\'")}': $v"
                }
                jsContext!!.eval("_appKeys = {$keysJs};")
                System.err.println("[pkjs] Injected appKeys: $appKeys")
            }

            // Load the app's JS
            try {
                jsContext!!.eval(jsSource)
            } catch (e: Exception) {
                System.err.println("[pkjs] Error loading JS: ${e.message}")
            }

            // Fire 'ready' event
            jsContext!!.eval("_pkjsFireEvent('ready', {type: 'ready'})")

            // Process any pending promises/timers from startup
            drainPendingWork()

        } catch (e: Exception) {
            System.err.println("[pkjs] Error starting JS engine: ${e.message}")
            e.printStackTrace(System.err)
        }
    }

    fun stop() {
        webSocketManager.closeAll()
        try {
            jsContext?.close()
            jsContext = null
        } catch (_: Exception) {}
    }

    /**
     * Trigger the 'showConfiguration' event.
     */
    fun triggerShowConfiguration() {
        val ctx = jsContext ?: return
        try {
            ctx.eval("_pkjsFireEvent('showConfiguration', {type: 'showConfiguration'})")
            drainPendingWork()
        } catch (e: Exception) {
            System.err.println("[pkjs] Error in showConfiguration: ${e.message}")
        }
    }

    /**
     * Trigger the 'webviewclosed' event with the given response string.
     */
    fun triggerWebviewClosed(response: String) {
        val ctx = jsContext ?: return
        try {
            val escaped = response
                .replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            ctx.eval("_pkjsFireEvent('webviewclosed', {type: 'webviewclosed', response: '$escaped'})")
            drainPendingWork()
        } catch (e: Exception) {
            System.err.println("[pkjs] Error in webviewclosed: ${e.message}")
        }
    }

    fun handleAppMessage(push: AppMessage.AppMessagePush) {
        val ctx = jsContext ?: return

        try {
            val payload = buildJsonObject {
                for (i in 0 until push.count.get().toInt()) {
                    val tuple = push.dictionary.list[i]
                    val key = tuple.key.get().toString()
                    when (AppMessageTuple.Type.fromValue(tuple.type.get())) {
                        AppMessageTuple.Type.CString -> put(key, tuple.dataAsString)
                        AppMessageTuple.Type.UInt -> put(key, tuple.dataAsUnsignedNumber.toLong())
                        AppMessageTuple.Type.Int -> put(key, tuple.dataAsSignedNumber.toLong())
                        AppMessageTuple.Type.ByteArray -> {
                            put(key, buildJsonArray {
                                tuple.dataAsBytes.forEach { add(it.toInt()) }
                            })
                        }
                    }
                }
            }

            val payloadJson = payload.toString()
            val escapedJson = payloadJson
                .replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace("\n", "\\n")
                .replace("\r", "\\r")

            ctx.eval("_pkjsFireEvent('appmessage', {type: 'appmessage', payload: JSON.parse('$escapedJson')})")

            drainPendingWork()
        } catch (e: Exception) {
            System.err.println("[pkjs] Error handling AppMessage: ${e.message}")
        }
    }

    /**
     * Process pending events: drain the consolidated event queue, handle
     * WebSocket events, and run deferred async work.
     */
    fun processPendingEvents() {
        val ctx = jsContext ?: return

        try {
            // Run any pending async work (fetch callbacks, promise continuations)
            drainPendingWork()

            // Drain the consolidated event queue in a single eval() call
            drainEventQueue(ctx)

            // Pump WebSocket incoming messages to JS
            webSocketManager.pumpEvents(ctx, ::drainPendingWork)
        } catch (e: Exception) {
            System.err.println("[pkjs] Error processing events: ${e.message}")
        }
    }

    private fun drainPendingWork() {
        val ctx = jsContext ?: return
        try {
            ctx.evalAsync("undefined")
        } catch (_: Exception) {}
    }

    /**
     * Drain the consolidated `_pkjsEvents` queue and dispatch each event
     * to the appropriate handler.
     */
    private fun drainEventQueue(ctx: JsContext) {
        try {
            val eventsJson = ctx.eval("JSON.stringify(_pkjsEvents.splice(0))")
            if (eventsJson == "[]" || eventsJson.isEmpty()) return

            val events = Json.parseToJsonElement(eventsJson).jsonArray
            for (event in events) {
                val obj = event.jsonObject
                val type = obj["type"]?.jsonPrimitive?.content ?: continue

                when (type) {
                    "appmessage" -> {
                        val dict = obj["dict"]?.jsonObject ?: continue
                        val txId = obj["txId"]?.jsonPrimitive?.int
                        sendAppMessageFromJson(dict, txId)
                    }
                    "log" -> {
                        val level = obj["level"]?.jsonPrimitive?.content ?: "log"
                        val msg = obj["msg"]?.jsonPrimitive?.content ?: ""
                        val ts = java.time.LocalTime.now().toString().substring(0, 8)
                        println("[$ts] [JS $level] $msg")
                        java.lang.System.out.flush()
                    }
                    "openurl" -> {
                        val url = obj["url"]?.jsonPrimitive?.content ?: ""
                        System.err.println("[pkjs] openURL: $url")
                    }
                    "notification" -> {
                        val title = obj["title"]?.jsonPrimitive?.content ?: ""
                        val body = obj["body"]?.jsonPrimitive?.content ?: ""
                        PebbleJSBlobDB.sendNotification(bridge, appUuid, title, body)
                    }
                    "appglance" -> {
                        val slices = obj["slices"]?.jsonArray ?: continue
                        PebbleJSBlobDB.sendAppGlance(bridge, appUuid, slices)
                    }
                    "ws" -> {
                        val action = obj["action"]?.jsonPrimitive?.content ?: continue
                        val id = obj["id"]?.jsonPrimitive?.int ?: continue
                        webSocketManager.handleAction(
                            action = action,
                            id = id,
                            url = obj["url"]?.jsonPrimitive?.contentOrNull,
                            data = obj["data"]?.jsonPrimitive?.contentOrNull,
                            code = obj["code"]?.jsonPrimitive?.intOrNull,
                            reason = obj["reason"]?.jsonPrimitive?.contentOrNull,
                        )
                    }
                }
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining event queue: ${e.message}")
        }
    }

    /**
     * Send an AppMessage to the watch.
     *
     * Uses the JS-generated txId when available (Step 6 fix: no duplicate
     * counter). Falls back to sequential generation only if txId is missing.
     */
    private fun sendAppMessageFromJson(dict: JsonObject, jsTxId: Int? = null) {
        try {
            val tuples = mutableListOf<AppMessageTuple>()

            for ((keyStr, value) in dict) {
                val resolvedKey = appKeys[keyStr]?.toUInt() ?: keyStr.toUIntOrNull()
                if (resolvedKey == null) {
                    System.err.println("[pkjs] Unknown appKey: $keyStr (not in appKeys map and not numeric)")
                    continue
                }
                val key = resolvedKey

                val tuple = when {
                    value is JsonPrimitive && value.isString ->
                        AppMessageTuple.createString(key, value.content)
                    value is JsonPrimitive && value.intOrNull != null ->
                        AppMessageTuple.createInt(key, value.int)
                    value is JsonPrimitive && value.longOrNull != null ->
                        AppMessageTuple.createInt(key, value.long.toInt())
                    value is JsonPrimitive && value.doubleOrNull != null ->
                        AppMessageTuple.createInt(key, value.double.toInt())
                    value is JsonArray -> {
                        val bytes = UByteArray(value.size) {
                            value[it].jsonPrimitive.int.toUByte()
                        }
                        AppMessageTuple.createUByteArray(key, bytes)
                    }
                    else -> AppMessageTuple.createInt(key, 0)
                }
                tuples.add(tuple)
            }

            // Use JS-generated txId to ensure consistency between what JS
            // reports to the app's success callback and what goes on the wire.
            val txId = ((jsTxId ?: 1) % 256).toUByte()

            val push = AppMessage.AppMessagePush(
                transactionId = txId,
                uuid = appUuid,
                tuples = tuples
            )
            bridge.sendPacket(push)
            System.err.println("[pkjs] Sent AppMessage with ${tuples.size} tuples (txId=$txId)")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send AppMessage: ${e.message}")
        }
    }
}
