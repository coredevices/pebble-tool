package io.rebble.libpebblecommon.bridge

import io.rebble.libpebblecommon.packets.AppMessage
import io.rebble.libpebblecommon.packets.AppMessageTuple
import kotlinx.serialization.json.*
import uniffi.library_rs.JsContext
import kotlin.uuid.Uuid

/**
 * PebbleKit JavaScript runtime using Picaros (Boa engine).
 *
 * Orchestrator class that delegates to:
 * - PebbleJSBootstrap: JS polyfill generation
 * - PebbleJSHttpFetcher: HTTP proxy detection and JsFetcher
 * - PebbleJSWebSocket: Real WebSocket connections
 * - PebbleJSBlobDB: BlobDB notifications and app glances
 */
class PebbleJS(
    private val bridge: QemuBridge,
    private val jsSource: String,
    private val appUuid: Uuid,
    private val appKeys: Map<String, Int> = emptyMap(),
    private val geoLatitude: Double = DEFAULT_LATITUDE,
    private val geoLongitude: Double = DEFAULT_LONGITUDE
) {
    companion object {
        const val DEFAULT_LATITUDE = 37.4419
        const val DEFAULT_LONGITUDE = -122.1430

        /**
         * Resolve geolocation coordinates from a --location argument.
         * Accepts "LAT,LON" or "auto" (IP-based lookup) or null (defaults).
         */
        fun resolveLocation(locationArg: String?): Pair<Double, Double> {
            if (locationArg == null) {
                return DEFAULT_LATITUDE to DEFAULT_LONGITUDE
            }
            if (locationArg.equals("auto", ignoreCase = true)) {
                return lookupIpLocation()
            }
            val parts = locationArg.split(",", limit = 2)
            if (parts.size == 2) {
                val lat = parts[0].trim().toDoubleOrNull()
                val lon = parts[1].trim().toDoubleOrNull()
                if (lat != null && lon != null) {
                    return lat to lon
                }
            }
            System.err.println("[pkjs] Invalid --location format: $locationArg (expected LAT,LON or auto)")
            return DEFAULT_LATITUDE to DEFAULT_LONGITUDE
        }

        private fun lookupIpLocation(): Pair<Double, Double> {
            try {
                val conn = java.net.URI("http://ip-api.com/json/?fields=lat,lon").toURL()
                    .openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                val body = conn.inputStream.bufferedReader().readText()
                conn.disconnect()
                val json = Json.parseToJsonElement(body).jsonObject
                val lat = json["lat"]?.jsonPrimitive?.double
                val lon = json["lon"]?.jsonPrimitive?.double
                if (lat != null && lon != null) {
                    System.err.println("[pkjs] IP geolocation: $lat, $lon")
                    return lat to lon
                }
            } catch (e: Exception) {
                System.err.println("[pkjs] IP geolocation lookup failed: ${e.message}")
            }
            return DEFAULT_LATITUDE to DEFAULT_LONGITUDE
        }
    }

    private var jsContext: JsContext? = null
    private val webSockets = mutableMapOf<Int, JavaWebSocket>()

    fun start() {
        try {
            jsContext = JsContext(createJsFetcher())

            // Load bootstrap polyfill
            val bootstrapJS = PebbleJSBootstrap.build(
                watchPlatform = bridge.watchPlatform,
                watchModel = bridge.watchModel,
                watchLanguage = bridge.watchLanguage,
                watchFwMajor = bridge.watchFwMajor,
                watchFwMinor = bridge.watchFwMinor,
                watchFwPatch = bridge.watchFwPatch,
                watchFwSuffix = bridge.watchFwSuffix,
                watchSerial = bridge.watchSerial,
                appUuidStr = appUuid.toString(),
                geoLatitude = geoLatitude,
                geoLongitude = geoLongitude
            )
            jsContext!!.eval(bootstrapJS)

            // Inject appKeys mapping
            if (appKeys.isNotEmpty()) {
                val keysJs = appKeys.entries.joinToString(", ") { (k, v) ->
                    "'${k.replace("'", "\\'")}': $v"
                }
                jsContext!!.eval("_appKeys = {$keysJs};")
                System.err.println("[pkjs] Injected appKeys: $appKeys")
            }

            // Load app JS
            try {
                jsContext!!.eval(jsSource)
            } catch (e: Exception) {
                System.err.println("[pkjs] Error loading JS: ${e.message}")
            }

            // Fire 'ready' event
            jsContext!!.eval("_pkjsFireEvent('ready', {type: 'ready'})")
            drainPendingWork()

        } catch (e: Exception) {
            System.err.println("[pkjs] Error starting JS engine: ${e.message}")
            e.printStackTrace(System.err)
        }
    }

    fun stop() {
        for ((_, ws) in webSockets) {
            ws.close(1001, "App stopping")
        }
        webSockets.clear()
        try {
            jsContext?.close()
            jsContext = null
        } catch (_: Exception) {}
    }

    fun triggerShowConfiguration() {
        val ctx = jsContext ?: return
        try {
            ctx.eval("_pkjsFireEvent('showConfiguration', {type: 'showConfiguration'})")
            drainPendingWork()
        } catch (e: Exception) {
            System.err.println("[pkjs] Error in showConfiguration: ${e.message}")
        }
    }

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
     * Process pending events: drain the consolidated event queue,
     * handle WebSocket lifecycle, and run deferred async work.
     */
    fun processPendingEvents() {
        val ctx = jsContext ?: return
        try {
            drainPendingWork()
            drainEventQueue(ctx)
            pumpWebSocketEvents(ctx, webSockets, ::drainPendingWork)
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
     * Drain all events from the consolidated _pkjsEvents queue in a single
     * eval() call, then dispatch each event by its type tag.
     */
    private fun drainEventQueue(ctx: JsContext) {
        try {
            val eventsJson = ctx.eval("JSON.stringify(_pkjsEvents.splice(0))")
            if (eventsJson == "[]" || eventsJson.isEmpty()) return

            val events = Json.parseToJsonElement(eventsJson).jsonArray
            for (event in events) {
                val obj = event.jsonObject
                when (obj["t"]?.jsonPrimitive?.content) {
                    "log" -> {
                        val level = obj["level"]?.jsonPrimitive?.content ?: "log"
                        val msg = obj["msg"]?.jsonPrimitive?.content ?: ""
                        val ts = java.time.LocalTime.now().toString().substring(0, 8)
                        println("[$ts] [JS $level] $msg")
                        java.lang.System.out.flush()
                    }
                    "msg" -> {
                        val dict = obj["dict"]?.jsonObject ?: continue
                        val txId = obj["txId"]?.jsonPrimitive?.int?.toUByte()
                        sendAppMessageFromJson(dict, txId)
                    }
                    "url" -> {
                        val url = obj["url"]?.jsonPrimitive?.content ?: ""
                        System.err.println("[pkjs] openURL: $url")
                    }
                    "notif" -> {
                        val title = obj["title"]?.jsonPrimitive?.content ?: ""
                        val body = obj["body"]?.jsonPrimitive?.content ?: ""
                        PebbleJSBlobDB.sendNotification(bridge, appUuid, title, body)
                    }
                    "glance" -> {
                        val slices = obj["slices"]?.jsonArray ?: continue
                        PebbleJSBlobDB.sendAppGlance(bridge, appUuid, slices)
                    }
                    "ws" -> handleWebSocketAction(obj)
                }
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining event queue: ${e.message}")
        }
    }

    private fun handleWebSocketAction(obj: JsonObject) {
        try {
            val act = obj["action"]?.jsonPrimitive?.content ?: return
            val id = obj["id"]?.jsonPrimitive?.int ?: return
            when (act) {
                "connect" -> {
                    val url = obj["url"]?.jsonPrimitive?.content ?: return
                    val ws = JavaWebSocket(id, url)
                    webSockets[id] = ws
                    Thread { ws.connect() }.start()
                    System.err.println("[pkjs] WebSocket connecting: $url")
                }
                "send" -> {
                    val data = obj["data"]?.jsonPrimitive?.content ?: ""
                    webSockets[id]?.send(data)
                }
                "close" -> {
                    val code = obj["code"]?.jsonPrimitive?.int ?: 1000
                    val reason = obj["reason"]?.jsonPrimitive?.content ?: ""
                    webSockets[id]?.close(code, reason)
                }
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error handling WS action: ${e.message}")
        }
    }

    private fun sendAppMessageFromJson(dict: JsonObject, jsTxId: UByte? = null) {
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
            val txId = jsTxId ?: 0u.toUByte()
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
