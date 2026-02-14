package io.rebble.libpebblecommon.bridge

import io.rebble.libpebblecommon.packets.AppMessage
import io.rebble.libpebblecommon.packets.AppMessageTuple
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import uniffi.library_rs.JsContext
import uniffi.library_rs.JsFetcher
import uniffi.library_rs.JsRequestKt
import uniffi.library_rs.JsResponseKt
import uniffi.library_rs.FetcherException
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URI
import kotlin.uuid.Uuid

/**
 * PebbleKit JavaScript runtime using Picaros (Boa engine).
 *
 * Implements the Pebble JS API that PBW apps use:
 * - Pebble.addEventListener / removeEventListener
 * - Pebble.sendAppMessage
 * - Pebble.getAccountToken / getWatchToken
 * - Pebble.openURL
 * - Pebble.showSimpleNotificationOnPebble
 * - console.log/warn/error (built-in via Boa)
 * - XMLHttpRequest (via fetch polyfill)
 * - localStorage (in-memory)
 * - setTimeout / setInterval / clearTimeout / clearInterval
 *
 * Uses a JavaScript polyfill layer that queues outgoing messages
 * for Kotlin to drain, since Boa/UniFFI doesn't support direct
 * native callbacks from JS.
 */
class PebbleJS(
    private val bridge: QemuBridge,
    private val jsSource: String,
    private val appUuid: Uuid,
    private val appKeys: Map<String, Int> = emptyMap()
) {
    private var jsContext: JsContext? = null
    private var nextTransactionId: UByte = 1u

    /**
     * JavaScript bootstrap that defines the Pebble API, localStorage,
     * XMLHttpRequest, and timer functions. Outgoing messages from JS
     * are queued in _pkjsOutbox for Kotlin to drain via eval().
     */
    private val bootstrapJS = """
        // ========== Internal queues ==========
        var _pkjsOutbox = [];      // outgoing AppMessage queue
        var _pkjsLogs = [];        // console log queue
        var _pkjsOpenURLs = [];    // openURL calls
        var _pkjsNotifications = []; // notification calls

        // ========== Pebble API ==========
        var _pkjsHandlers = {};

        var Pebble = {
            addEventListener: function(event, callback) {
                if (!_pkjsHandlers[event]) _pkjsHandlers[event] = [];
                _pkjsHandlers[event].push(callback);
            },
            removeEventListener: function(event, callback) {
                if (!_pkjsHandlers[event]) return;
                var idx = _pkjsHandlers[event].indexOf(callback);
                if (idx >= 0) _pkjsHandlers[event].splice(idx, 1);
            },
            on: function(event, callback) { Pebble.addEventListener(event, callback); },
            off: function(event, callback) { Pebble.removeEventListener(event, callback); },
            sendAppMessage: function(dict, success, error) {
                var txId = (_pkjsOutbox.length + 1);
                _pkjsOutbox.push({dict: dict, txId: txId});
                // Schedule success callback asynchronously
                if (success) {
                    setTimeout(function() {
                        try { success({transactionId: txId}); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'sendAppMessage success callback error: ' + e});
                        }
                    }, 0);
                }
                return txId;
            },
            getAccountToken: function() {
                return '0123456789abcdef0123456789abcdef';
            },
            getWatchToken: function() {
                return 'fedcba9876543210fedcba9876543210';
            },
            openURL: function(url) {
                _pkjsOpenURLs.push(url);
                _pkjsLogs.push({level: 'info', msg: '[pkjs] openURL: ' + url});
            },
            showSimpleNotificationOnPebble: function(title, body) {
                _pkjsNotifications.push({title: title, body: body});
                _pkjsLogs.push({level: 'info', msg: '[pkjs] Notification: ' + title + ' - ' + body});
            },
            getActiveWatchInfo: function() {
                return {
                    platform: 'basalt',
                    model: 'qemu_platform_basalt',
                    language: 'en_US',
                    firmware: { major: 4, minor: 4, patch: 0, suffix: '' }
                };
            },
            showToast: function(msg) {
                _pkjsLogs.push({level: 'info', msg: '[pkjs] Toast: ' + msg});
            }
        };

        // ========== localStorage (in-memory) ==========
        var _localStorageData = {};
        var localStorage = {
            getItem: function(key) {
                var v = _localStorageData[key];
                return v !== undefined ? v : null;
            },
            setItem: function(key, value) {
                _localStorageData[key] = String(value);
            },
            removeItem: function(key) {
                delete _localStorageData[key];
            },
            clear: function() {
                _localStorageData = {};
            }
        };

        // ========== Timers ==========
        var _timerIdCounter = 0;
        var _activeTimers = {};

        // Boa has queueMicrotask but not setTimeout natively,
        // so we implement basic timer support using promises.
        // For the bridge's polling model, immediate timers are fine.
        function setTimeout(fn, delay) {
            var id = ++_timerIdCounter;
            _activeTimers[id] = true;
            // Use a resolved promise to defer execution
            Promise.resolve().then(function() {
                if (_activeTimers[id]) {
                    delete _activeTimers[id];
                    try { fn(); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'setTimeout error: ' + e});
                    }
                }
            });
            return id;
        }

        function setInterval(fn, delay) {
            // For bridge usage, we implement setInterval as a single
            // deferred call. The Kotlin side re-triggers via polling.
            var id = ++_timerIdCounter;
            _activeTimers[id] = {fn: fn, delay: delay};
            Promise.resolve().then(function() {
                if (_activeTimers[id]) {
                    try { fn(); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'setInterval error: ' + e});
                    }
                }
            });
            return id;
        }

        function clearTimeout(id) {
            delete _activeTimers[id];
        }

        function clearInterval(id) {
            delete _activeTimers[id];
        }

        // ========== XMLHttpRequest ==========
        function XMLHttpRequest() {
            this.readyState = 0;
            this.status = 0;
            this.responseText = '';
            this.response = '';
            this._method = 'GET';
            this._url = '';
            this._async = true;
            this._headers = {};
            this.onload = null;
            this.onerror = null;
            this.onreadystatechange = null;
        }

        XMLHttpRequest.prototype.open = function(method, url, async) {
            this._method = method.toUpperCase();
            this._url = url;
            this._async = (async !== false);
            this._headers = {};
            this.readyState = 1;
        };

        XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
            this._headers[name] = value;
        };

        XMLHttpRequest.prototype.send = function(body) {
            var self = this;
            var fetchOpts = {
                method: self._method,
                headers: self._headers
            };
            if (body && (self._method === 'POST' || self._method === 'PUT' || self._method === 'PATCH')) {
                fetchOpts.body = body;
            }
            fetch(self._url, fetchOpts).then(function(response) {
                self.status = response.status;
                return response.text();
            }).then(function(text) {
                self.responseText = text;
                self.response = text;
                self.readyState = 4;
                if (self.onload) {
                    try { self.onload.call(self); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'XHR onload error: ' + e});
                    }
                }
                if (self.onreadystatechange) {
                    try { self.onreadystatechange.call(self); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'XHR onreadystatechange error: ' + e});
                    }
                }
            }).catch(function(err) {
                self.readyState = 4;
                _pkjsLogs.push({level: 'error', msg: 'XHR error: ' + err});
                if (self.onerror) {
                    try { self.onerror.call(self); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'XHR onerror callback error: ' + e});
                    }
                }
            });
        };

        // ========== Console override for log capture ==========
        var _origConsole = typeof console !== 'undefined' ? console : {};
        console = {
            log: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsLogs.push({level: 'log', msg: msg});
                if (_origConsole.log) _origConsole.log.apply(_origConsole, arguments);
            },
            info: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsLogs.push({level: 'info', msg: msg});
                if (_origConsole.info) _origConsole.info.apply(_origConsole, arguments);
            },
            warn: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsLogs.push({level: 'warn', msg: msg});
                if (_origConsole.warn) _origConsole.warn.apply(_origConsole, arguments);
            },
            error: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsLogs.push({level: 'error', msg: msg});
                if (_origConsole.error) _origConsole.error.apply(_origConsole, arguments);
            },
            debug: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsLogs.push({level: 'debug', msg: msg});
                if (_origConsole.debug) _origConsole.debug.apply(_origConsole, arguments);
            }
        };

        // ========== AppKeys mapping (injected from appinfo.json) ==========
        var _appKeys = {}; // Will be set by Kotlin before loading app JS

        // ========== navigator.geolocation ==========
        var navigator = {
            geolocation: {
                getCurrentPosition: function(success, error, options) {
                    // Stub geolocation - calls error handler since bridge
                    // doesn't have real GPS access. Apps should handle
                    // the error gracefully (e.g. show mock data).
                    if (error) {
                        setTimeout(function() {
                            try {
                                error({
                                    code: 2,
                                    message: 'Position unavailable (bridge mode)',
                                    PERMISSION_DENIED: 1,
                                    POSITION_UNAVAILABLE: 2,
                                    TIMEOUT: 3
                                });
                            } catch(e) {
                                _pkjsLogs.push({level: 'error', msg: 'Geolocation error callback error: ' + e});
                            }
                        }, 0);
                    }
                },
                watchPosition: function(success, error, options) {
                    // Not supported in bridge mode
                    return 0;
                },
                clearWatch: function(id) {}
            }
        };

        // Helper: fire an event
        function _pkjsFireEvent(eventName, eventData) {
            var handlers = _pkjsHandlers[eventName];
            if (!handlers) return;
            for (var i = 0; i < handlers.length; i++) {
                try {
                    handlers[i](eventData || {type: eventName});
                } catch(e) {
                    _pkjsLogs.push({level: 'error', msg: 'Error in ' + eventName + ' handler: ' + e});
                }
            }
        }
    """.trimIndent()

    /**
     * JsFetcher implementation that delegates HTTP requests from the
     * JS fetch() API to Java's HttpURLConnection.
     */
    private val fetcher = object : JsFetcher {
        override suspend fun fetch(request: JsRequestKt): JsResponseKt {
            // IMPORTANT: UniFFI panics if any non-FetcherException escapes this callback.
            // We must catch absolutely everything and convert to FetcherException.
            try {
                return withContext(Dispatchers.IO) {
                    val conn = URI(request.url).toURL().openConnection() as HttpURLConnection
                    conn.requestMethod = request.method
                    conn.connectTimeout = 30000
                    conn.readTimeout = 30000
                    for ((k, v) in request.headers) {
                        conn.setRequestProperty(k, v)
                    }
                    if (request.body.isNotEmpty() &&
                        (request.method == "POST" || request.method == "PUT" || request.method == "PATCH")
                    ) {
                        conn.doOutput = true
                        conn.outputStream.use { it.write(request.body) }
                    }
                    val status = conn.responseCode
                    val body = try {
                        val stream = if (status in 200..299) conn.inputStream else conn.errorStream
                        stream?.readBytes() ?: ByteArray(0)
                    } catch (_: Exception) {
                        ByteArray(0)
                    }
                    val headers = mutableMapOf<String, String>()
                    conn.headerFields?.forEach { (key, values) ->
                        if (key != null && values != null) {
                            headers[key] = values.joinToString(", ")
                        }
                    }
                    JsResponseKt(
                        status = status.toUShort(),
                        headers = headers,
                        body = body
                    )
                }
            } catch (e: FetcherException) {
                throw e
            } catch (e: Throwable) {
                // Catch everything including Error subclasses and convert to the
                // expected exception type so UniFFI doesn't panic
                throw FetcherException.NetworkException(
                    "${e.javaClass.simpleName}: ${e.message ?: "unknown error"}"
                )
            }
        }
    }

    fun start() {
        try {
            jsContext = JsContext(fetcher)

            // Load the bootstrap polyfill
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
        try {
            jsContext?.close()
            jsContext = null
        } catch (_: Exception) {}
    }

    fun handleAppMessage(push: AppMessage.AppMessagePush) {
        val ctx = jsContext ?: return

        try {
            // Build a JSON payload from the AppMessage
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

            // Process any promises triggered by the event
            drainPendingWork()
        } catch (e: Exception) {
            System.err.println("[pkjs] Error handling AppMessage: ${e.message}")
        }
    }

    /**
     * Process pending events: drain logs, send queued AppMessages,
     * and run any deferred async work (fetch responses, timers).
     */
    fun processPendingEvents() {
        val ctx = jsContext ?: return

        try {
            // Run any pending async work (fetch callbacks, promise continuations)
            drainPendingWork()

            // Drain console logs
            drainLogs(ctx)

            // Drain outgoing AppMessages
            drainAppMessages(ctx)

            // Drain openURL calls
            drainOpenURLs(ctx)

            // Drain notifications
            drainNotifications(ctx)
        } catch (e: Exception) {
            System.err.println("[pkjs] Error processing events: ${e.message}")
        }
    }

    private fun drainPendingWork() {
        val ctx = jsContext ?: return
        try {
            // evalAsync runs the job queue (promise continuations, fetch callbacks)
            ctx.evalAsync("undefined")
        } catch (_: Exception) {
            // Ignore - may fail if no async work pending
        }
    }

    private fun drainLogs(ctx: JsContext) {
        try {
            val logsJson = ctx.eval("JSON.stringify(_pkjsLogs.splice(0))")
            if (logsJson != "[]" && logsJson.isNotEmpty()) {
                val logs = Json.parseToJsonElement(logsJson).jsonArray
                for (log in logs) {
                    val obj = log.jsonObject
                    val level = obj["level"]?.jsonPrimitive?.content ?: "log"
                    val msg = obj["msg"]?.jsonPrimitive?.content ?: ""
                    val ts = java.time.LocalTime.now().toString().substring(0, 8)
                    println("[$ts] [JS $level] $msg")
                    java.lang.System.out.flush()
                }
            }
        } catch (_: Exception) {}
    }

    private fun drainAppMessages(ctx: JsContext) {
        try {
            val msgsJson = ctx.eval("JSON.stringify(_pkjsOutbox.splice(0))")
            if (msgsJson == "[]" || msgsJson.isEmpty()) return

            val msgs = Json.parseToJsonElement(msgsJson).jsonArray
            for (msg in msgs) {
                val obj = msg.jsonObject
                val dict = obj["dict"]?.jsonObject ?: continue
                sendAppMessageFromJson(dict)
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining AppMessages: ${e.message}")
        }
    }

    private fun drainOpenURLs(ctx: JsContext) {
        try {
            val urlsJson = ctx.eval("JSON.stringify(_pkjsOpenURLs.splice(0))")
            if (urlsJson == "[]" || urlsJson.isEmpty()) return

            val urls = Json.parseToJsonElement(urlsJson).jsonArray
            for (url in urls) {
                System.err.println("[pkjs] openURL: ${url.jsonPrimitive.content}")
            }
        } catch (_: Exception) {}
    }

    private fun drainNotifications(ctx: JsContext) {
        try {
            val notifJson = ctx.eval("JSON.stringify(_pkjsNotifications.splice(0))")
            if (notifJson == "[]" || notifJson.isEmpty()) return

            val notifs = Json.parseToJsonElement(notifJson).jsonArray
            for (notif in notifs) {
                val obj = notif.jsonObject
                val title = obj["title"]?.jsonPrimitive?.content ?: ""
                val body = obj["body"]?.jsonPrimitive?.content ?: ""
                System.err.println("[pkjs] Notification: $title - $body")
            }
        } catch (_: Exception) {}
    }

    private fun sendAppMessageFromJson(dict: JsonObject) {
        try {
            val tuples = mutableListOf<AppMessageTuple>()

            for ((keyStr, value) in dict) {
                // Resolve string keys using appKeys mapping, fall back to numeric parsing
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

            val txId = nextTransactionId
            nextTransactionId = ((nextTransactionId.toInt() + 1) % 256).toUByte()

            val push = AppMessage.AppMessagePush(
                transactionId = txId,
                uuid = appUuid,
                tuples = tuples
            )
            bridge.sendPacket(push)
            System.err.println("[pkjs] Sent AppMessage with ${tuples.size} tuples")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send AppMessage: ${e.message}")
        }
    }
}
