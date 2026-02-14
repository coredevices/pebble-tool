package io.rebble.libpebblecommon.bridge

import coredev.BlobDatabase
import io.rebble.libpebblecommon.packets.AppMessage
import io.rebble.libpebblecommon.packets.AppMessageTuple
import io.rebble.libpebblecommon.packets.blobdb.BlobCommand
import io.rebble.libpebblecommon.packets.blobdb.BlobResponse
import io.rebble.libpebblecommon.packets.blobdb.TimelineItem
import io.rebble.libpebblecommon.packets.blobdb.TimelineAttribute
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
import java.net.http.HttpClient
import java.security.MessageDigest
import kotlin.uuid.Uuid

/**
 * PebbleKit JavaScript runtime using Picaros (Boa engine).
 *
 * Implements the full Pebble JS API surface that PBW apps use:
 * - Pebble.addEventListener / removeEventListener / on / off
 * - Pebble.sendAppMessage
 * - Pebble.getAccountToken / getWatchToken
 * - Pebble.getActiveWatchInfo (real data from negotiation)
 * - Pebble.openURL
 * - Pebble.showSimpleNotificationOnPebble (real BlobDB notification)
 * - Pebble.getTimelineToken / timelineSubscribe / timelineUnsubscribe / timelineSubscriptions
 * - Pebble.appGlanceReload (real BlobDB app glance)
 * - Pebble.showToast / postMessage
 * - XMLHttpRequest (real HTTP via fetch)
 * - WebSocket (real connections via Java)
 * - navigator.geolocation (configurable via --location LAT,LON or --location auto)
 * - localStorage (in-memory, full API)
 * - setTimeout / setInterval / clearTimeout / clearInterval
 * - console.log / info / warn / error / debug
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
            // Parse "LAT,LON"
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
    /** Active WebSocket connections managed on the Kotlin side */
    private val webSockets = mutableMapOf<Int, JavaWebSocket>()
    private var nextWsId = 1

    /**
     * Represents a real WebSocket connection managed by Java's HTTP client.
     */
    private inner class JavaWebSocket(
        val id: Int,
        val url: String
    ) {
        private val incomingMessages = mutableListOf<String>()
        private val incomingBinary = mutableListOf<ByteArray>()
        var isOpen = false
            private set
        var isClosed = false
            private set
        var closeCode = 0
            private set
        var closeReason = ""
            private set
        var errorMessage: String? = null
            private set

        private var javaWs: java.net.http.WebSocket? = null

        fun connect() {
            try {
                val client = HttpClient.newHttpClient()
                val builder = client.newWebSocketBuilder()
                val listener = object : java.net.http.WebSocket.Listener {
                    private val textBuffer = StringBuilder()

                    override fun onOpen(webSocket: java.net.http.WebSocket) {
                        isOpen = true
                        webSocket.request(1)
                    }

                    override fun onText(
                        webSocket: java.net.http.WebSocket,
                        data: CharSequence,
                        last: Boolean
                    ): java.util.concurrent.CompletionStage<*>? {
                        textBuffer.append(data)
                        if (last) {
                            synchronized(incomingMessages) {
                                incomingMessages.add(textBuffer.toString())
                            }
                            textBuffer.clear()
                        }
                        webSocket.request(1)
                        return null
                    }

                    override fun onBinary(
                        webSocket: java.net.http.WebSocket,
                        data: java.nio.ByteBuffer,
                        last: Boolean
                    ): java.util.concurrent.CompletionStage<*>? {
                        val bytes = ByteArray(data.remaining())
                        data.get(bytes)
                        synchronized(incomingBinary) {
                            incomingBinary.add(bytes)
                        }
                        webSocket.request(1)
                        return null
                    }

                    override fun onClose(
                        webSocket: java.net.http.WebSocket,
                        statusCode: Int,
                        reason: String
                    ): java.util.concurrent.CompletionStage<*>? {
                        isClosed = true
                        closeCode = statusCode
                        closeReason = reason
                        isOpen = false
                        return null
                    }

                    override fun onError(webSocket: java.net.http.WebSocket, error: Throwable) {
                        errorMessage = error.message ?: "Unknown WebSocket error"
                        isClosed = true
                        isOpen = false
                    }
                }
                javaWs = builder.buildAsync(URI.create(url), listener).join()
            } catch (e: Exception) {
                errorMessage = e.message ?: "Connection failed"
                isClosed = true
                isOpen = false
            }
        }

        fun send(data: String) {
            javaWs?.sendText(data, true)
        }

        fun close(code: Int, reason: String) {
            try {
                javaWs?.sendClose(code, reason)
            } catch (_: Exception) {}
            isClosed = true
            isOpen = false
        }

        fun drainMessages(): List<String> {
            synchronized(incomingMessages) {
                val copy = incomingMessages.toList()
                incomingMessages.clear()
                return copy
            }
        }
    }

    /**
     * Generate deterministic token by hashing seed with SHA-256.
     */
    private fun deterministicToken(seed: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(seed.toByteArray())
            .joinToString("") { "%02x".format(it) }
            .take(32)
    }

    /**
     * JavaScript bootstrap that defines the Pebble API, localStorage,
     * XMLHttpRequest, WebSocket, geolocation, and timer functions.
     * Outgoing messages from JS are queued in internal arrays for
     * Kotlin to drain via eval().
     */
    private fun buildBootstrapJS(): String {
        // Compute deterministic tokens from watch serial + app UUID
        val accountToken = deterministicToken("account:${bridge.watchSerial}")
        val watchToken = deterministicToken("watch:${bridge.watchSerial}:$appUuid")

        return """
        // ========== Internal queues ==========
        var _pkjsOutbox = [];        // outgoing AppMessage queue
        var _pkjsLogs = [];          // console log queue
        var _pkjsOpenURLs = [];      // openURL calls
        var _pkjsNotifications = []; // notification calls
        var _pkjsAppGlances = [];    // appGlanceReload calls
        var _pkjsWsActions = [];     // WebSocket actions for Kotlin

        // ========== AppKeys mapping (injected from appinfo.json) ==========
        var _appKeys = {};

        // ========== Pebble API ==========
        var _pkjsHandlers = {};
        var _pkjsTxIdCounter = 0;

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
                var txId = ++_pkjsTxIdCounter;
                // Resolve string keys via _appKeys before queueing
                var resolved = {};
                for (var k in dict) {
                    if (dict.hasOwnProperty(k)) {
                        var numKey = _appKeys.hasOwnProperty(k) ? _appKeys[k] : (isNaN(Number(k)) ? k : Number(k));
                        resolved[numKey] = dict[k];
                    }
                }
                _pkjsOutbox.push({dict: resolved, txId: txId});
                if (success) {
                    setTimeout(function() {
                        try { success({data: {transactionId: txId}, transactionId: txId}); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'sendAppMessage success callback error: ' + e});
                        }
                    }, 0);
                }
                return txId;
            },

            getAccountToken: function() {
                return '${accountToken}';
            },

            getWatchToken: function() {
                return '${watchToken}';
            },

            openURL: function(url) {
                _pkjsOpenURLs.push(url);
            },

            showSimpleNotificationOnPebble: function(title, body) {
                _pkjsNotifications.push({title: title, body: body});
            },

            getActiveWatchInfo: function() {
                return {
                    platform: '${bridge.watchPlatform}',
                    model: '${bridge.watchModel}',
                    language: '${bridge.watchLanguage}',
                    firmware: {
                        major: ${bridge.watchFwMajor},
                        minor: ${bridge.watchFwMinor},
                        patch: ${bridge.watchFwPatch},
                        suffix: '${bridge.watchFwSuffix.replace("'", "\\'")}'
                    }
                };
            },

            showToast: function(msg) {
                _pkjsLogs.push({level: 'info', msg: '[pkjs] Toast: ' + msg});
            },

            // Timeline APIs - in-memory tracking (correct for bridge/emulator)
            getTimelineToken: function(onSuccess, onFailure) {
                var token = '${deterministicToken("timeline:$appUuid")}';
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(token); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'getTimelineToken callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineSubscribe: function(topic, onSuccess, onFailure) {
                if (!Pebble._timelineTopics) Pebble._timelineTopics = [];
                if (Pebble._timelineTopics.indexOf(topic) < 0) {
                    Pebble._timelineTopics.push(topic);
                }
                _pkjsLogs.push({level: 'info', msg: '[pkjs] timelineSubscribe: ' + topic});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'timelineSubscribe callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineUnsubscribe: function(topic, onSuccess, onFailure) {
                if (Pebble._timelineTopics) {
                    var idx = Pebble._timelineTopics.indexOf(topic);
                    if (idx >= 0) Pebble._timelineTopics.splice(idx, 1);
                }
                _pkjsLogs.push({level: 'info', msg: '[pkjs] timelineUnsubscribe: ' + topic});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'timelineUnsubscribe callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineSubscriptions: function(onSuccess, onFailure) {
                var topics = Pebble._timelineTopics ? Pebble._timelineTopics.slice() : [];
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(topics); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'timelineSubscriptions callback error: ' + e});
                        }
                    }, 0);
                }
            },

            _timelineTopics: [],

            appGlanceReload: function(slices, onSuccess, onFailure) {
                _pkjsAppGlances.push({slices: slices});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(slices); } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'appGlanceReload callback error: ' + e});
                        }
                    }, 0);
                }
            },

            // Rocky.js postMessage - stub (Rocky not supported in bridge)
            postMessage: function(data) {
                _pkjsLogs.push({level: 'info', msg: '[pkjs] postMessage: ' + JSON.stringify(data)});
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
            },
            get length() {
                return Object.keys(_localStorageData).length;
            },
            key: function(index) {
                var keys = Object.keys(_localStorageData);
                return index < keys.length ? keys[index] : null;
            }
        };

        // ========== Timers ==========
        var _timerIdCounter = 0;
        var _activeTimers = {};

        function setTimeout(fn, delay) {
            var id = ++_timerIdCounter;
            _activeTimers[id] = true;
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

        // ========== fetch() wrapper ==========
        // Boa's native fetch() doesn't expose response headers on the JS
        // Response object. The Kotlin JsFetcher prepends a JSON header block
        // to the response body (delimited by \0). XHR.send() uses _origFetch
        // and strips the prefix. We also wrap fetch() for direct callers.
        var _origFetch = fetch;
        fetch = function(url, opts) {
            return _origFetch(url, opts).then(function(response) {
                var origText = response.text.bind(response);
                response.text = function() {
                    return origText().then(function(rawText) {
                        var sepIdx = rawText.indexOf('\0');
                        if (sepIdx >= 0) return rawText.substring(sepIdx + 1);
                        return rawText;
                    });
                };
                return response;
            });
        };

        // ========== XMLHttpRequest ==========
        function XMLHttpRequest() {
            this.readyState = 0;
            this.status = 0;
            this.statusText = '';
            this.responseText = '';
            this.response = '';
            this.responseType = '';
            this.timeout = 0;
            this.withCredentials = false;
            this._method = 'GET';
            this._url = '';
            this._async = true;
            this._headers = {};
            this._responseHeaders = {};
            this.onload = null;
            this.onerror = null;
            this.ontimeout = null;
            this.onabort = null;
            this.onreadystatechange = null;
            this.onprogress = null;
            this.UNSENT = 0;
            this.OPENED = 1;
            this.HEADERS_RECEIVED = 2;
            this.LOADING = 3;
            this.DONE = 4;
        }

        XMLHttpRequest.prototype.open = function(method, url, async) {
            this._method = method.toUpperCase();
            this._url = url;
            this._async = (async !== false);
            this._headers = {};
            this._responseHeaders = {};
            this.readyState = 1;
            this.status = 0;
            this.statusText = '';
            this.responseText = '';
            this.response = '';
        };

        XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
            this._headers[name] = value;
        };

        XMLHttpRequest.prototype.getResponseHeader = function(name) {
            if (this.readyState < 2) return null;
            var lowerName = name.toLowerCase();
            for (var key in this._responseHeaders) {
                if (key.toLowerCase() === lowerName) {
                    return this._responseHeaders[key];
                }
            }
            return null;
        };

        XMLHttpRequest.prototype.getAllResponseHeaders = function() {
            if (this.readyState < 2) return '';
            var result = '';
            for (var key in this._responseHeaders) {
                result += key + ': ' + this._responseHeaders[key] + '\r\n';
            }
            return result;
        };

        XMLHttpRequest.prototype.overrideMimeType = function(mime) {
            this._overrideMime = mime;
        };

        XMLHttpRequest.prototype.abort = function() {
            this.readyState = 0;
            if (this.onabort) {
                try { this.onabort.call(this); } catch(e) {}
            }
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
            // Use the raw _origFetch to get the response with our header prefix
            _origFetch(self._url, fetchOpts).then(function(response) {
                self.status = response.status;
                self.statusText = response.statusText || '';
                return response.text();
            }).then(function(rawText) {
                // The Kotlin JsFetcher prepends headers as JSON + \0 to the body.
                // Strip them out and populate _responseHeaders.
                var actualText = rawText;
                var sepIdx = rawText.indexOf('\0');
                if (sepIdx >= 0) {
                    try {
                        var hdrs = JSON.parse(rawText.substring(0, sepIdx));
                        for (var key in hdrs) {
                            if (hdrs.hasOwnProperty(key)) {
                                self._responseHeaders[key] = hdrs[key];
                            }
                        }
                    } catch(e) {}
                    actualText = rawText.substring(sepIdx + 1);
                }
                self.readyState = 2; // HEADERS_RECEIVED
                if (self.onreadystatechange) {
                    try { self.onreadystatechange.call(self); } catch(e) {}
                }
                self.responseText = actualText;
                self.response = actualText;
                self.readyState = 4;
                if (self.onreadystatechange) {
                    try { self.onreadystatechange.call(self); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'XHR onreadystatechange error: ' + e});
                    }
                }
                if (self.onload) {
                    try { self.onload.call(self); } catch(e) {
                        _pkjsLogs.push({level: 'error', msg: 'XHR onload error: ' + e});
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

        // ========== WebSocket ==========
        // Real WebSocket backed by Kotlin/Java. JS queues actions,
        // Kotlin creates real connections and pumps events back to JS.
        var _wsIdCounter = 0;
        var _wsInstances = {};

        function WebSocket(url, protocols) {
            this._id = ++_wsIdCounter;
            this.url = url;
            this.readyState = 0; // CONNECTING
            this.bufferedAmount = 0;
            this.extensions = '';
            this.protocol = typeof protocols === 'string' ? protocols : '';
            this.binaryType = 'blob';
            this.onopen = null;
            this.onerror = null;
            this.onclose = null;
            this.onmessage = null;
            this.CONNECTING = 0;
            this.OPEN = 1;
            this.CLOSING = 2;
            this.CLOSED = 3;
            _wsInstances[this._id] = this;
            // Queue connect action for Kotlin
            _pkjsWsActions.push({action: 'connect', id: this._id, url: url});
        }
        WebSocket.prototype.send = function(data) {
            if (this.readyState !== 1) {
                throw new Error('WebSocket is not open (readyState=' + this.readyState + ')');
            }
            _pkjsWsActions.push({action: 'send', id: this._id, data: String(data)});
        };
        WebSocket.prototype.close = function(code, reason) {
            this.readyState = 2; // CLOSING
            _pkjsWsActions.push({action: 'close', id: this._id, code: code || 1000, reason: reason || ''});
        };
        WebSocket.CONNECTING = 0;
        WebSocket.OPEN = 1;
        WebSocket.CLOSING = 2;
        WebSocket.CLOSED = 3;

        // Helper: Kotlin calls this to deliver WS events back to JS
        function _pkjsWsEvent(id, type, data, code, reason) {
            var ws = _wsInstances[id];
            if (!ws) return;
            if (type === 'open') {
                ws.readyState = 1;
                if (ws.onopen) { try { ws.onopen({type:'open'}); } catch(e) {} }
            } else if (type === 'message') {
                if (ws.onmessage) { try { ws.onmessage({type:'message', data: data}); } catch(e) {} }
            } else if (type === 'error') {
                if (ws.onerror) { try { ws.onerror({type:'error', message: data}); } catch(e) {} }
            } else if (type === 'close') {
                ws.readyState = 3;
                if (ws.onclose) { try { ws.onclose({type:'close', code: code||1006, reason: reason||'', wasClean: (code===1000)}); } catch(e) {} }
                delete _wsInstances[id];
            }
        }

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

        // ========== navigator.geolocation ==========
        // Coordinates are injected from Kotlin (--location LAT,LON or auto).
        var _geoLat = $geoLatitude;
        var _geoLon = $geoLongitude;
        var _geoWatchCounter = 0;
        var _geoWatchers = {};
        var navigator = {
            geolocation: {
                getCurrentPosition: function(success, error, options) {
                    setTimeout(function() {
                        try {
                            if (success) {
                                success({
                                    coords: {
                                        latitude: _geoLat,
                                        longitude: _geoLon,
                                        altitude: 30.0,
                                        accuracy: 25.0,
                                        altitudeAccuracy: 10.0,
                                        heading: null,
                                        speed: null
                                    },
                                    timestamp: Date.now()
                                });
                            }
                        } catch(e) {
                            _pkjsLogs.push({level: 'error', msg: 'Geolocation success callback error: ' + e});
                        }
                    }, 0);
                },
                watchPosition: function(success, error, options) {
                    var id = ++_geoWatchCounter;
                    _geoWatchers[id] = true;
                    // Fire once immediately
                    setTimeout(function() {
                        if (_geoWatchers[id] && success) {
                            try {
                                success({
                                    coords: {
                                        latitude: _geoLat,
                                        longitude: _geoLon,
                                        altitude: 30.0,
                                        accuracy: 25.0,
                                        altitudeAccuracy: 10.0,
                                        heading: null,
                                        speed: null
                                    },
                                    timestamp: Date.now()
                                });
                            } catch(e) {}
                        }
                    }, 0);
                    return id;
                },
                clearWatch: function(id) {
                    delete _geoWatchers[id];
                }
            }
        };

        // Helper: fire an event to all registered handlers
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
    }

    /**
     * Detect HTTP(S) proxy from environment variables (HTTP_PROXY, HTTPS_PROXY, etc.)
     * Returns a Proxy object or Proxy.NO_PROXY.
     */
    private fun detectProxy(url: String): java.net.Proxy {
        val isHttps = url.startsWith("https://", ignoreCase = true)

        // Check NO_PROXY / no_proxy
        val noProxy = System.getenv("NO_PROXY") ?: System.getenv("no_proxy") ?: ""
        if (noProxy.isNotEmpty()) {
            val host = try { URI(url).host ?: "" } catch (_: Exception) { "" }
            for (entry in noProxy.split(",")) {
                val pattern = entry.trim()
                if (pattern.isEmpty()) continue
                if (pattern == "*") return java.net.Proxy.NO_PROXY
                if (host == pattern || host.endsWith(pattern.removePrefix("*"))) {
                    return java.net.Proxy.NO_PROXY
                }
            }
        }

        // Pick proxy URL from env: HTTPS_PROXY for https, HTTP_PROXY for http
        val proxyUrl = if (isHttps) {
            System.getenv("HTTPS_PROXY") ?: System.getenv("https_proxy")
        } else {
            System.getenv("HTTP_PROXY") ?: System.getenv("http_proxy")
        } ?: return java.net.Proxy.NO_PROXY

        return try {
            val proxyUri = URI(proxyUrl)
            val proxyHost = proxyUri.host ?: return java.net.Proxy.NO_PROXY
            val proxyPort = if (proxyUri.port > 0) proxyUri.port else 80

            // Extract proxy credentials from URL (user:password@host format)
            val userInfo = proxyUri.userInfo
            if (userInfo != null) {
                val parts = userInfo.split(":", limit = 2)
                val user = parts[0]
                val pass = if (parts.size > 1) parts[1] else ""
                // Set the default Authenticator for proxy auth (used by CONNECT tunneling)
                java.net.Authenticator.setDefault(object : java.net.Authenticator() {
                    override fun getPasswordAuthentication(): java.net.PasswordAuthentication? {
                        if (requestorType == RequestorType.PROXY) {
                            return java.net.PasswordAuthentication(user, pass.toCharArray())
                        }
                        return null
                    }
                })
                // Also set system properties for HTTPS tunneling
                System.setProperty("jdk.http.auth.tunneling.disabledSchemes", "")
            }

            java.net.Proxy(java.net.Proxy.Type.HTTP, java.net.InetSocketAddress(proxyHost, proxyPort))
        } catch (_: Exception) {
            java.net.Proxy.NO_PROXY
        }
    }

    /**
     * JsFetcher implementation that delegates HTTP requests from the
     * JS fetch() API to Java's HttpURLConnection.
     * Automatically detects and uses HTTP_PROXY/HTTPS_PROXY env vars.
     */
    private val fetcher = object : JsFetcher {
        override suspend fun fetch(request: JsRequestKt): JsResponseKt {
            // IMPORTANT: UniFFI panics if any non-FetcherException escapes this callback.
            // We must catch absolutely everything and convert to FetcherException.
            try {
                return withContext(Dispatchers.IO) {
                    val proxy = detectProxy(request.url)
                    val conn = URI(request.url).toURL().openConnection(proxy) as HttpURLConnection
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
                            headers[key.lowercase()] = values.joinToString(", ")
                        }
                    }
                    // Boa's fetch() doesn't expose response headers on the JS
                    // Response object, so we prepend them as JSON + \0 to the
                    // body. Our JS fetch() wrapper strips them out.
                    // Serialize headers to JSON manually (simpler than kotlinx serialization)
                    val headersJson = buildString {
                        append("{")
                        headers.entries.forEachIndexed { i, (k, v) ->
                            if (i > 0) append(",")
                            append("\"")
                            append(k.replace("\"", "\\\""))
                            append("\":\"")
                            append(v.replace("\"", "\\\""))
                            append("\"")
                        }
                        append("}")
                    }
                    val prefixedBody = headersJson.toByteArray(Charsets.UTF_8) +
                        byteArrayOf(0) + body
                    JsResponseKt(
                        status = status.toUShort(),
                        headers = headers,
                        body = prefixedBody
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

            // Load the bootstrap polyfill (with real watch info baked in)
            jsContext!!.eval(buildBootstrapJS())

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
        // Close all WebSocket connections
        for ((_, ws) in webSockets) {
            ws.close(1001, "App stopping")
        }
        webSockets.clear()
        try {
            jsContext?.close()
            jsContext = null
        } catch (_: Exception) {}
    }

    /**
     * Trigger the 'showConfiguration' event. In the real Pebble app this
     * opens the config page; here we just fire the event so the app's
     * handler can call openURL().
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
     * The response is typically URL-encoded JSON from the config page.
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
     * handle WebSocket actions, notifications, glances, and run any
     * deferred async work (fetch responses, timers).
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

            // Drain notifications (send real BlobDB notifications)
            drainNotifications(ctx)

            // Drain appGlance reloads (send real BlobDB app glances)
            drainAppGlances(ctx)

            // Process WebSocket actions
            drainWebSocketActions(ctx)

            // Pump WebSocket incoming messages to JS
            pumpWebSocketEvents(ctx)
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
                val txId = obj["txId"]?.jsonPrimitive?.int?.toUByte()
                sendAppMessageFromJson(dict, txId)
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

    /**
     * Send real notifications to the watch via BlobDB Notification database.
     */
    private fun drainNotifications(ctx: JsContext) {
        try {
            val notifJson = ctx.eval("JSON.stringify(_pkjsNotifications.splice(0))")
            if (notifJson == "[]" || notifJson.isEmpty()) return

            val notifs = Json.parseToJsonElement(notifJson).jsonArray
            for (notif in notifs) {
                val obj = notif.jsonObject
                val title = obj["title"]?.jsonPrimitive?.content ?: ""
                val body = obj["body"]?.jsonPrimitive?.content ?: ""
                sendNotificationToWatch(title, body)
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining notifications: ${e.message}")
        }
    }

    /**
     * Send a real notification to the watch via BlobDB.
     */
    private fun sendNotificationToWatch(title: String, body: String) {
        try {
            val notifUuid = Uuid.random()
            val now = (System.currentTimeMillis() / 1000).toUInt()

            val attributes = mutableListOf<TimelineItem.Attribute>()
            // Title attribute
            val titleBytes = title.toByteArray(Charsets.UTF_8).toUByteArray()
            attributes.add(TimelineItem.Attribute(TimelineAttribute.Title.id, titleBytes))
            // Body attribute
            val bodyBytes = body.toByteArray(Charsets.UTF_8).toUByteArray()
            attributes.add(TimelineItem.Attribute(TimelineAttribute.Body.id, bodyBytes))
            // Sender (app name)
            val senderBytes = "PebbleKit JS".toByteArray(Charsets.UTF_8).toUByteArray()
            attributes.add(TimelineItem.Attribute(TimelineAttribute.Sender.id, senderBytes))

            val timelineItem = TimelineItem(
                itemId = notifUuid,
                parentId = appUuid,
                timestampSecs = now,
                duration = 0u,
                type = TimelineItem.Type.Notification,
                flags = TimelineItem.Flag.makeFlags(listOf(TimelineItem.Flag.IS_VISIBLE)),
                layout = TimelineItem.Layout.GenericNotification,
                attributes = attributes,
                actions = listOf(
                    TimelineItem.Action(
                        0u,
                        TimelineItem.Action.Type.Dismiss,
                        listOf(TimelineItem.Attribute(
                            TimelineAttribute.Title.id,
                            "Dismiss".toByteArray(Charsets.UTF_8).toUByteArray()
                        ))
                    )
                )
            )

            val itemBytes = timelineItem.toBytes()
            val uuidBytes = notifUuid.toByteArray().toUByteArray()
            val token = (System.currentTimeMillis() % 65536).toUShort()

            val insertCmd = BlobCommand.InsertCommand(
                token = token,
                database = BlobDatabase.Notification,
                key = uuidBytes,
                value = itemBytes
            )
            bridge.sendPacket(insertCmd)
            System.err.println("[pkjs] Sent notification to watch: $title")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send notification: ${e.message}")
        }
    }

    /**
     * Send real app glance data to the watch via BlobDB AppGlance database.
     */
    private fun drainAppGlances(ctx: JsContext) {
        try {
            val glancesJson = ctx.eval("JSON.stringify(_pkjsAppGlances.splice(0))")
            if (glancesJson == "[]" || glancesJson.isEmpty()) return

            val glances = Json.parseToJsonElement(glancesJson).jsonArray
            for (glance in glances) {
                val obj = glance.jsonObject
                val slices = obj["slices"]?.jsonArray ?: continue
                sendAppGlanceToWatch(slices)
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining app glances: ${e.message}")
        }
    }

    /**
     * Send app glance data to the watch via BlobDB.
     * The glance is stored in the AppGlance database keyed by app UUID.
     */
    private fun sendAppGlanceToWatch(slices: JsonArray) {
        try {
            // Build the app glance binary payload:
            // Format: [version:u8=1][created_at:u32LE][num_slices:u8][slices...]
            // Each slice: [type:u8=0][expiration:u32LE][num_attributes:u8][attributes...]
            // Each attribute: [id:u8][length:u16LE][data...]
            val buf = java.io.ByteArrayOutputStream()
            buf.write(1) // version
            val now = (System.currentTimeMillis() / 1000).toInt()
            buf.write(now and 0xFF)
            buf.write((now shr 8) and 0xFF)
            buf.write((now shr 16) and 0xFF)
            buf.write((now shr 24) and 0xFF)
            buf.write(slices.size) // num_slices

            for (slice in slices) {
                val sliceObj = slice.jsonObject
                buf.write(0) // type = icon-subtitle
                // expiration = 0 (no expiration)
                buf.write(0); buf.write(0); buf.write(0); buf.write(0)

                val layout = sliceObj["layout"]?.jsonObject
                val attrs = mutableListOf<Pair<UByte, ByteArray>>()
                if (layout != null) {
                    val icon = layout["icon"]?.jsonPrimitive?.content
                    if (icon != null) {
                        // Icon attribute (0x30) - encode as string resource
                        attrs.add(TimelineAttribute.Icon.id to icon.toByteArray(Charsets.UTF_8))
                    }
                    val subtitle = layout["subtitleTemplateString"]?.jsonPrimitive?.content
                    if (subtitle != null) {
                        attrs.add(TimelineAttribute.SubtitleTemplateString.id to subtitle.toByteArray(Charsets.UTF_8))
                    }
                }
                buf.write(attrs.size)
                for ((id, data) in attrs) {
                    buf.write(id.toInt())
                    buf.write(data.size and 0xFF)
                    buf.write((data.size shr 8) and 0xFF)
                    buf.write(data)
                }
            }

            val glanceBytes = buf.toByteArray().toUByteArray()
            val uuidBytes = appUuid.toByteArray().toUByteArray()
            val token = (System.currentTimeMillis() % 65536).toUShort()

            val insertCmd = BlobCommand.InsertCommand(
                token = token,
                database = BlobDatabase.AppGlance,
                key = uuidBytes,
                value = glanceBytes
            )
            bridge.sendPacket(insertCmd)
            System.err.println("[pkjs] Sent app glance to watch (${slices.size} slices)")
        } catch (e: Exception) {
            System.err.println("[pkjs] Failed to send app glance: ${e.message}")
        }
    }

    /**
     * Process WebSocket actions queued from JS (connect, send, close).
     */
    private fun drainWebSocketActions(ctx: JsContext) {
        try {
            val actionsJson = ctx.eval("JSON.stringify(_pkjsWsActions.splice(0))")
            if (actionsJson == "[]" || actionsJson.isEmpty()) return

            val actions = Json.parseToJsonElement(actionsJson).jsonArray
            for (action in actions) {
                val obj = action.jsonObject
                val act = obj["action"]?.jsonPrimitive?.content ?: continue
                val id = obj["id"]?.jsonPrimitive?.int ?: continue

                when (act) {
                    "connect" -> {
                        val url = obj["url"]?.jsonPrimitive?.content ?: continue
                        val ws = JavaWebSocket(id, url)
                        webSockets[id] = ws
                        // Connect in background thread
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
            }
        } catch (e: Exception) {
            System.err.println("[pkjs] Error draining WS actions: ${e.message}")
        }
    }

    /**
     * Pump WebSocket events from Java connections back into JS.
     */
    private fun pumpWebSocketEvents(ctx: JsContext) {
        val toRemove = mutableListOf<Int>()
        for ((id, ws) in webSockets) {
            try {
                if (ws.isOpen) {
                    // Check if we need to fire onopen (transition detection)
                    ctx.eval("if (_wsInstances[$id] && _wsInstances[$id].readyState === 0) { _pkjsWsEvent($id, 'open', '', 0, ''); }")
                    drainPendingWork()

                    // Deliver incoming messages
                    for (msg in ws.drainMessages()) {
                        val escaped = msg
                            .replace("\\", "\\\\")
                            .replace("'", "\\'")
                            .replace("\n", "\\n")
                            .replace("\r", "\\r")
                        ctx.eval("_pkjsWsEvent($id, 'message', '$escaped', 0, '')")
                        drainPendingWork()
                    }
                }

                if (ws.errorMessage != null) {
                    val errEsc = (ws.errorMessage ?: "")
                        .replace("\\", "\\\\")
                        .replace("'", "\\'")
                    ctx.eval("_pkjsWsEvent($id, 'error', '$errEsc', 0, '')")
                    ws.errorMessage?.let { _ ->
                        // Clear error after delivering
                    }
                    drainPendingWork()
                }

                if (ws.isClosed) {
                    val reasonEsc = ws.closeReason
                        .replace("\\", "\\\\")
                        .replace("'", "\\'")
                    ctx.eval("_pkjsWsEvent($id, 'close', '', ${ws.closeCode}, '$reasonEsc')")
                    drainPendingWork()
                    toRemove.add(id)
                }
            } catch (e: Exception) {
                System.err.println("[pkjs] Error pumping WS events for #$id: ${e.message}")
                toRemove.add(id)
            }
        }
        for (id in toRemove) {
            webSockets.remove(id)
        }
    }

    private fun sendAppMessageFromJson(dict: JsonObject, jsTxId: UByte? = null) {
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

            // Use the JS-generated txId to match what the JS success callback expects
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
