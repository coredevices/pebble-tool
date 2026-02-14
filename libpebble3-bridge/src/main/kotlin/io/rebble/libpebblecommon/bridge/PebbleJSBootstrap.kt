package io.rebble.libpebblecommon.bridge

import java.security.MessageDigest

/**
 * Generates the JavaScript bootstrap that defines the Pebble API, localStorage,
 * XMLHttpRequest, WebSocket, geolocation, console, and timer polyfills.
 *
 * This is a pure function: takes config params, returns a JS string.
 * All JS→Kotlin communication goes through the _pkjsEvents consolidated queue.
 */
object PebbleJSBootstrap {

    /**
     * Generate deterministic token by hashing seed with SHA-256.
     */
    fun deterministicToken(seed: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(seed.toByteArray())
            .joinToString("") { "%02x".format(it) }
            .take(32)
    }

    /**
     * Build the full bootstrap JS polyfill.
     *
     * @param watchPlatform Platform codename (e.g. "basalt")
     * @param watchModel Watch model string
     * @param watchLanguage Language code (e.g. "en_US")
     * @param watchFwMajor Firmware major version
     * @param watchFwMinor Firmware minor version
     * @param watchFwPatch Firmware patch version
     * @param watchFwSuffix Firmware version suffix
     * @param watchSerial Watch serial number
     * @param appUuidStr String representation of the app UUID
     * @param geoLatitude Geolocation latitude
     * @param geoLongitude Geolocation longitude
     */
    fun build(
        watchPlatform: String,
        watchModel: String,
        watchLanguage: String,
        watchFwMajor: Int,
        watchFwMinor: Int,
        watchFwPatch: Int,
        watchFwSuffix: String,
        watchSerial: String,
        appUuidStr: String,
        geoLatitude: Double,
        geoLongitude: Double
    ): String {
        val accountToken = deterministicToken("account:$watchSerial")
        val watchToken = deterministicToken("watch:$watchSerial:$appUuidStr")
        val timelineToken = deterministicToken("timeline:$appUuidStr")
        val escapedSuffix = watchFwSuffix.replace("'", "\\'")

        return """
        // ========== Consolidated event queue ==========
        // All JS→Kotlin communication goes through a single typed queue,
        // drained in one eval() call per poll cycle.
        var _pkjsEvents = [];

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
                _pkjsEvents.push({t: 'msg', dict: resolved, txId: txId});
                if (success) {
                    setTimeout(function() {
                        try { success({data: {transactionId: txId}, transactionId: txId}); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'sendAppMessage success callback error: ' + e});
                        }
                    }, 0);
                }
                return txId;
            },

            getAccountToken: function() {
                return '$accountToken';
            },

            getWatchToken: function() {
                return '$watchToken';
            },

            openURL: function(url) {
                _pkjsEvents.push({t: 'url', url: url});
            },

            showSimpleNotificationOnPebble: function(title, body) {
                _pkjsEvents.push({t: 'notif', title: title, body: body});
            },

            getActiveWatchInfo: function() {
                return {
                    platform: '$watchPlatform',
                    model: '$watchModel',
                    language: '$watchLanguage',
                    firmware: {
                        major: $watchFwMajor,
                        minor: $watchFwMinor,
                        patch: $watchFwPatch,
                        suffix: '$escapedSuffix'
                    }
                };
            },

            showToast: function(msg) {
                _pkjsEvents.push({t: 'log', level: 'info', msg: '[pkjs] Toast: ' + msg});
            },

            // Timeline APIs - in-memory tracking (correct for bridge/emulator)
            getTimelineToken: function(onSuccess, onFailure) {
                var token = '$timelineToken';
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(token); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'getTimelineToken callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineSubscribe: function(topic, onSuccess, onFailure) {
                if (!Pebble._timelineTopics) Pebble._timelineTopics = [];
                if (Pebble._timelineTopics.indexOf(topic) < 0) {
                    Pebble._timelineTopics.push(topic);
                }
                _pkjsEvents.push({t: 'log', level: 'info', msg: '[pkjs] timelineSubscribe: ' + topic});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'timelineSubscribe callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineUnsubscribe: function(topic, onSuccess, onFailure) {
                if (Pebble._timelineTopics) {
                    var idx = Pebble._timelineTopics.indexOf(topic);
                    if (idx >= 0) Pebble._timelineTopics.splice(idx, 1);
                }
                _pkjsEvents.push({t: 'log', level: 'info', msg: '[pkjs] timelineUnsubscribe: ' + topic});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'timelineUnsubscribe callback error: ' + e});
                        }
                    }, 0);
                }
            },

            timelineSubscriptions: function(onSuccess, onFailure) {
                var topics = Pebble._timelineTopics ? Pebble._timelineTopics.slice() : [];
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(topics); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'timelineSubscriptions callback error: ' + e});
                        }
                    }, 0);
                }
            },

            _timelineTopics: [],

            appGlanceReload: function(slices, onSuccess, onFailure) {
                _pkjsEvents.push({t: 'glance', slices: slices});
                if (onSuccess) {
                    setTimeout(function() {
                        try { onSuccess(slices); } catch(e) {
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'appGlanceReload callback error: ' + e});
                        }
                    }, 0);
                }
            },

            // Rocky.js postMessage - stub (Rocky not supported in bridge)
            postMessage: function(data) {
                _pkjsEvents.push({t: 'log', level: 'info', msg: '[pkjs] postMessage: ' + JSON.stringify(data)});
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
                        _pkjsEvents.push({t: 'log', level: 'error', msg: 'setTimeout error: ' + e});
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
                        _pkjsEvents.push({t: 'log', level: 'error', msg: 'setInterval error: ' + e});
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
            _origFetch(self._url, fetchOpts).then(function(response) {
                self.status = response.status;
                self.statusText = response.statusText || '';
                return response.text();
            }).then(function(rawText) {
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
                self.readyState = 2;
                if (self.onreadystatechange) {
                    try { self.onreadystatechange.call(self); } catch(e) {}
                }
                self.responseText = actualText;
                self.response = actualText;
                self.readyState = 4;
                if (self.onreadystatechange) {
                    try { self.onreadystatechange.call(self); } catch(e) {
                        _pkjsEvents.push({t: 'log', level: 'error', msg: 'XHR onreadystatechange error: ' + e});
                    }
                }
                if (self.onload) {
                    try { self.onload.call(self); } catch(e) {
                        _pkjsEvents.push({t: 'log', level: 'error', msg: 'XHR onload error: ' + e});
                    }
                }
            }).catch(function(err) {
                self.readyState = 4;
                _pkjsEvents.push({t: 'log', level: 'error', msg: 'XHR error: ' + err});
                if (self.onerror) {
                    try { self.onerror.call(self); } catch(e) {
                        _pkjsEvents.push({t: 'log', level: 'error', msg: 'XHR onerror callback error: ' + e});
                    }
                }
            });
        };

        // ========== WebSocket ==========
        var _wsIdCounter = 0;
        var _wsInstances = {};

        function WebSocket(url, protocols) {
            this._id = ++_wsIdCounter;
            this.url = url;
            this.readyState = 0;
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
            _pkjsEvents.push({t: 'ws', action: 'connect', id: this._id, url: url});
        }
        WebSocket.prototype.send = function(data) {
            if (this.readyState !== 1) {
                throw new Error('WebSocket is not open (readyState=' + this.readyState + ')');
            }
            _pkjsEvents.push({t: 'ws', action: 'send', id: this._id, data: String(data)});
        };
        WebSocket.prototype.close = function(code, reason) {
            this.readyState = 2;
            _pkjsEvents.push({t: 'ws', action: 'close', id: this._id, code: code || 1000, reason: reason || ''});
        };
        WebSocket.CONNECTING = 0;
        WebSocket.OPEN = 1;
        WebSocket.CLOSING = 2;
        WebSocket.CLOSED = 3;

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
                _pkjsEvents.push({t: 'log', level: 'log', msg: msg});
                if (_origConsole.log) _origConsole.log.apply(_origConsole, arguments);
            },
            info: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsEvents.push({t: 'log', level: 'info', msg: msg});
                if (_origConsole.info) _origConsole.info.apply(_origConsole, arguments);
            },
            warn: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsEvents.push({t: 'log', level: 'warn', msg: msg});
                if (_origConsole.warn) _origConsole.warn.apply(_origConsole, arguments);
            },
            error: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsEvents.push({t: 'log', level: 'error', msg: msg});
                if (_origConsole.error) _origConsole.error.apply(_origConsole, arguments);
            },
            debug: function() {
                var msg = Array.prototype.slice.call(arguments).join(' ');
                _pkjsEvents.push({t: 'log', level: 'debug', msg: msg});
                if (_origConsole.debug) _origConsole.debug.apply(_origConsole, arguments);
            }
        };

        // ========== navigator.geolocation ==========
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
                            _pkjsEvents.push({t: 'log', level: 'error', msg: 'Geolocation success callback error: ' + e});
                        }
                    }, 0);
                },
                watchPosition: function(success, error, options) {
                    var id = ++_geoWatchCounter;
                    _geoWatchers[id] = true;
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
                    _pkjsEvents.push({t: 'log', level: 'error', msg: 'Error in ' + eventName + ' handler: ' + e});
                }
            }
        }
    """.trimIndent()
    }
}
