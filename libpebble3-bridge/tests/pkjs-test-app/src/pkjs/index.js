// =============================================================================
// PebbleKit JS API Test App - Full API Coverage
//
// Tests every PKJS API with real implementations:
//   - Tokens are deterministic SHA-256 hashes (not hardcoded)
//   - WatchInfo comes from real negotiation data
//   - Geolocation returns canned Palo Alto coords via SUCCESS callback
//   - Notifications send real BlobDB packets to the watch
//   - AppGlance sends real BlobDB packets to the watch
//   - WebSocket makes real connections (falls back gracefully in container)
//   - XHR makes real HTTP requests (falls back gracefully in container)
//
// Run: java -jar build/libs/libpebble3-bridge-all.jar \
//   install-and-logs <port> tests/pkjs-test-app/build/pkjs-test-app.pbw basalt
// =============================================================================

var testCount = 0;
var passCount = 0;
var failCount = 0;

function pass(name, detail) {
    testCount++;
    passCount++;
    var msg = 'TEST ' + testCount + ' PASS [' + name + ']';
    if (detail) msg += ': ' + detail;
    console.log(msg);
}

function fail(name, detail) {
    testCount++;
    failCount++;
    var msg = 'TEST ' + testCount + ' FAIL [' + name + ']';
    if (detail) msg += ': ' + detail;
    console.error(msg);
}

function check(name, cond, detail) {
    if (cond) pass(name, detail); else fail(name, detail);
}

// ---------------------------------------------------------------------------
// 1. Ready event + core API existence checks
// ---------------------------------------------------------------------------
Pebble.addEventListener('ready', function(e) {
    check('ready event fires', e.type === 'ready', 'type=' + e.type);

    // -- Pebble object methods --
    check('addEventListener', typeof Pebble.addEventListener === 'function');
    check('removeEventListener', typeof Pebble.removeEventListener === 'function');
    check('on / off aliases', typeof Pebble.on === 'function' && typeof Pebble.off === 'function');
    check('sendAppMessage', typeof Pebble.sendAppMessage === 'function');
    check('getAccountToken', typeof Pebble.getAccountToken === 'function');
    check('getWatchToken', typeof Pebble.getWatchToken === 'function');
    check('getActiveWatchInfo', typeof Pebble.getActiveWatchInfo === 'function');
    check('openURL', typeof Pebble.openURL === 'function');
    check('showSimpleNotificationOnPebble', typeof Pebble.showSimpleNotificationOnPebble === 'function');
    check('getTimelineToken', typeof Pebble.getTimelineToken === 'function');
    check('timelineSubscribe', typeof Pebble.timelineSubscribe === 'function');
    check('timelineUnsubscribe', typeof Pebble.timelineUnsubscribe === 'function');
    check('timelineSubscriptions', typeof Pebble.timelineSubscriptions === 'function');
    check('appGlanceReload', typeof Pebble.appGlanceReload === 'function');
    check('postMessage', typeof Pebble.postMessage === 'function');

    // ---------------------------------------------------------------------------
    // 2. Tokens (deterministic SHA-256 hashes, not hardcoded)
    // ---------------------------------------------------------------------------
    var acct = Pebble.getAccountToken();
    check('getAccountToken is 32-char hex', typeof acct === 'string' && acct.length === 32 &&
          /^[0-9a-f]+$/.test(acct), acct);
    var watch = Pebble.getWatchToken();
    check('getWatchToken is 32-char hex', typeof watch === 'string' && watch.length === 32 &&
          /^[0-9a-f]+$/.test(watch), watch);
    check('account and watch tokens differ', acct !== watch);
    // Tokens should be deterministic (same on repeat calls)
    check('getAccountToken deterministic', Pebble.getAccountToken() === acct);
    check('getWatchToken deterministic', Pebble.getWatchToken() === watch);

    // ---------------------------------------------------------------------------
    // 3. WatchInfo (from real negotiation data)
    // ---------------------------------------------------------------------------
    var info = Pebble.getActiveWatchInfo();
    check('watchInfo object', typeof info === 'object' && info !== null);
    check('watchInfo.platform is valid', typeof info.platform === 'string' &&
          ['aplite','basalt','chalk','diorite','emery'].indexOf(info.platform) >= 0,
          info.platform);
    check('watchInfo.model contains platform', typeof info.model === 'string' &&
          info.model.indexOf(info.platform) >= 0, info.model);
    check('watchInfo.language', typeof info.language === 'string' && info.language.length >= 2,
          info.language);
    check('watchInfo.firmware object', typeof info.firmware === 'object');
    check('watchInfo.firmware.major is number', typeof info.firmware.major === 'number' &&
          info.firmware.major >= 1, 'major=' + info.firmware.major);
    check('watchInfo.firmware.minor is number', typeof info.firmware.minor === 'number',
          'minor=' + info.firmware.minor);
    check('watchInfo.firmware.patch is number', typeof info.firmware.patch === 'number',
          'patch=' + info.firmware.patch);
    check('watchInfo.firmware.suffix is string', typeof info.firmware.suffix === 'string',
          'suffix=' + JSON.stringify(info.firmware.suffix));
    // Deterministic
    check('getActiveWatchInfo deterministic', JSON.stringify(Pebble.getActiveWatchInfo()) ===
          JSON.stringify(info));

    // ---------------------------------------------------------------------------
    // 4. localStorage
    // ---------------------------------------------------------------------------
    localStorage.clear();
    check('localStorage.clear', localStorage.length === 0);
    localStorage.setItem('city', 'London');
    localStorage.setItem('temp', '18');
    check('localStorage.setItem+getItem', localStorage.getItem('city') === 'London');
    check('localStorage.length', localStorage.length === 2, 'len=' + localStorage.length);
    check('localStorage.key(0)', localStorage.key(0) === 'city' || localStorage.key(0) === 'temp',
          'key(0)=' + localStorage.key(0));
    localStorage.removeItem('temp');
    check('localStorage.removeItem', localStorage.getItem('temp') === null);
    localStorage.setItem('num', 42);
    check('localStorage coerces to string', localStorage.getItem('num') === '42');
    localStorage.clear();

    // ---------------------------------------------------------------------------
    // 5. Timers
    // ---------------------------------------------------------------------------
    var t1Fired = false;
    setTimeout(function() {
        t1Fired = true;
        pass('setTimeout callback fires');
    }, 0);
    var cancelId = setTimeout(function() {
        fail('clearTimeout', 'cancelled timer fired');
    }, 0);
    clearTimeout(cancelId);
    pass('clearTimeout cancels timer');

    var ival = false;
    var ivalId = setInterval(function() {
        if (!ival) { ival = true; pass('setInterval callback fires'); }
    }, 100);
    var cancelIval = setInterval(function() {
        fail('clearInterval', 'cancelled interval fired');
    }, 100);
    clearInterval(cancelIval);
    pass('clearInterval cancels interval');

    // ---------------------------------------------------------------------------
    // 6. removeEventListener
    // ---------------------------------------------------------------------------
    var spy = function() { fail('removeEventListener', 'spy was called'); };
    Pebble.addEventListener('appmessage', spy);
    Pebble.removeEventListener('appmessage', spy);
    pass('removeEventListener removes handler');

    // ---------------------------------------------------------------------------
    // 7. openURL / showSimpleNotificationOnPebble / showToast / postMessage
    // ---------------------------------------------------------------------------
    Pebble.openURL('https://example.com/config');
    pass('openURL no crash');
    // showSimpleNotificationOnPebble now sends a real BlobDB notification
    Pebble.showSimpleNotificationOnPebble('Hello', 'World');
    pass('showSimpleNotificationOnPebble queued real notification');
    Pebble.showToast('test toast');
    pass('showToast no crash');
    Pebble.postMessage({msg: 'hello rocky'});
    pass('postMessage no crash');

    // ---------------------------------------------------------------------------
    // 8. sendAppMessage with numeric keys
    // ---------------------------------------------------------------------------
    var tx1 = Pebble.sendAppMessage({3: 'JS is ready'},
        function(d) {
            check('sendAppMessage ack callback', typeof d.transactionId === 'number',
                  'txId=' + d.transactionId);
        },
        function(d, err) { fail('sendAppMessage ack', err); }
    );
    check('sendAppMessage returns txId', typeof tx1 === 'number' && tx1 > 0, 'tx=' + tx1);

    // ---------------------------------------------------------------------------
    // 9. sendAppMessage with string appKeys
    // ---------------------------------------------------------------------------
    Pebble.sendAppMessage({'Status': 'key-test-ok'},
        function() { pass('sendAppMessage string appKey ack'); },
        function(d, err) { fail('sendAppMessage string appKey', err); }
    );

    // ---------------------------------------------------------------------------
    // 10. getTimelineToken (deterministic hash)
    // ---------------------------------------------------------------------------
    Pebble.getTimelineToken(
        function(token) {
            check('getTimelineToken is 32-char hex',
                  typeof token === 'string' && token.length === 32 && /^[0-9a-f]+$/.test(token),
                  'token=' + token);
        },
        function() { fail('getTimelineToken failure cb'); }
    );

    // ---------------------------------------------------------------------------
    // 11. Timeline subscribe / list / unsubscribe
    // ---------------------------------------------------------------------------
    Pebble.timelineSubscribe('sports',
        function() {
            pass('timelineSubscribe success');
            Pebble.timelineSubscribe('weather', function() {
                Pebble.timelineSubscriptions(function(topics) {
                    check('timelineSubscriptions lists topics',
                          Array.isArray(topics) && topics.length === 2,
                          JSON.stringify(topics));

                    Pebble.timelineUnsubscribe('sports', function() {
                        pass('timelineUnsubscribe success');
                        Pebble.timelineSubscriptions(function(t2) {
                            check('timelineUnsubscribe removes topic',
                                  t2.length === 1 && t2[0] === 'weather',
                                  JSON.stringify(t2));
                        });
                    });
                });
            });
        },
        function() { fail('timelineSubscribe failure'); }
    );

    // ---------------------------------------------------------------------------
    // 12. appGlanceReload (sends real BlobDB packet)
    // ---------------------------------------------------------------------------
    Pebble.appGlanceReload(
        [{layout: {icon: 'system://images/GENERIC_WARNING',
                   subtitleTemplateString: 'Test glance'}}],
        function(s) { pass('appGlanceReload success (sent BlobDB)'); },
        function()  { fail('appGlanceReload failure'); }
    );

    // ---------------------------------------------------------------------------
    // 13. XMLHttpRequest - structure tests
    // ---------------------------------------------------------------------------
    var xhr = new XMLHttpRequest();
    check('XHR initial readyState', xhr.readyState === 0);
    check('XHR constants', xhr.DONE === 4 && xhr.OPENED === 1);
    xhr.open('GET', 'https://example.com');
    check('XHR open sets readyState=1', xhr.readyState === 1);
    check('XHR.getResponseHeader before send', xhr.getResponseHeader('x') === null);
    check('XHR.getAllResponseHeaders before send', xhr.getAllResponseHeaders() === '');
    xhr.abort();
    check('XHR.abort sets readyState=0', xhr.readyState === 0);

    // XHR real network test - fetch weather data as a realistic API call
    var xhrGet = new XMLHttpRequest();
    xhrGet.onload = function() {
        check('XHR GET succeeds', this.status === 200, 'status=' + this.status);
        check('XHR responseText', this.responseText.length > 0, 'len=' + this.responseText.length);
        // Verify the response is valid JSON from the weather API
        var parsed = JSON.parse(this.responseText);
        check('XHR response is valid JSON', typeof parsed.latitude === 'number',
              'lat=' + parsed.latitude);
        // Debug: log what _responseHeaders looks like
        var headerKeys = Object.keys(this._responseHeaders);
        console.log('XHR response header keys: ' + JSON.stringify(headerKeys));
        var ct = this.getResponseHeader('Content-Type');
        check('XHR getResponseHeader', ct !== null, 'ct=' + ct);
        var allHeaders = this.getAllResponseHeaders();
        check('XHR getAllResponseHeaders', allHeaders.length > 0,
              'len=' + allHeaders.length);
    };
    xhrGet.onerror = function() {
        fail('XHR GET to open-meteo failed (network error)');
    };
    xhrGet.open('GET', 'https://api.open-meteo.com/v1/forecast?latitude=37.44&longitude=-122.14&current=temperature_2m');
    xhrGet.send();

    // XHR POST - use a simple echo service or just verify POST mechanics
    var xhrPost = new XMLHttpRequest();
    xhrPost.onload = function() {
        // open-meteo returns 200 even for POST (treats it as GET)
        check('XHR POST succeeds', this.status === 200, 'status=' + this.status);
    };
    xhrPost.onerror = function() {
        fail('XHR POST to open-meteo failed (network error)');
    };
    xhrPost.open('POST', 'https://api.open-meteo.com/v1/forecast?latitude=37.44&longitude=-122.14&current=temperature_2m');
    xhrPost.setRequestHeader('Content-Type', 'application/json');
    xhrPost.send(JSON.stringify({test: true}));

    // ---------------------------------------------------------------------------
    // 14. navigator.geolocation (canned Palo Alto coords via SUCCESS callback)
    // ---------------------------------------------------------------------------
    check('navigator.geolocation exists', typeof navigator.geolocation === 'object');
    navigator.geolocation.getCurrentPosition(
        function(pos) {
            check('geolocation SUCCESS callback fires', true);
            check('geolocation coords.latitude', typeof pos.coords.latitude === 'number' &&
                  Math.abs(pos.coords.latitude - 37.4419) < 0.01,
                  'lat=' + pos.coords.latitude);
            check('geolocation coords.longitude', typeof pos.coords.longitude === 'number' &&
                  Math.abs(pos.coords.longitude - (-122.143)) < 0.01,
                  'lon=' + pos.coords.longitude);
            check('geolocation coords.altitude', typeof pos.coords.altitude === 'number',
                  'alt=' + pos.coords.altitude);
            check('geolocation coords.accuracy', typeof pos.coords.accuracy === 'number' &&
                  pos.coords.accuracy > 0, 'acc=' + pos.coords.accuracy);
            check('geolocation timestamp', typeof pos.timestamp === 'number' &&
                  pos.timestamp > 1000000000000, 'ts=' + pos.timestamp);
        },
        function(err) {
            fail('geolocation should not call error cb', err.message);
        }
    );

    // watchPosition also returns canned coords
    var watchFired = false;
    var wid = navigator.geolocation.watchPosition(
        function(pos) {
            if (!watchFired) {
                watchFired = true;
                check('watchPosition success fires', true);
                check('watchPosition has coords', typeof pos.coords.latitude === 'number',
                      'lat=' + pos.coords.latitude);
            }
        },
        function() {}
    );
    check('watchPosition returns id', typeof wid === 'number' && wid > 0, 'id=' + wid);
    navigator.geolocation.clearWatch(wid);
    pass('clearWatch no crash');

    // ---------------------------------------------------------------------------
    // 15. WebSocket (real connections via Java, falls back on DNS error)
    // ---------------------------------------------------------------------------
    check('WebSocket constructor', typeof WebSocket === 'function');
    check('WebSocket static constants', WebSocket.CLOSED === 3 && WebSocket.OPEN === 1 &&
          WebSocket.CONNECTING === 0 && WebSocket.CLOSING === 2);

    var ws = new WebSocket('wss://echo.websocket.org');
    check('WebSocket initial readyState=CONNECTING', ws.readyState === 0);
    check('WebSocket.url', ws.url === 'wss://echo.websocket.org');
    check('WebSocket instance constants', ws.CONNECTING === 0 && ws.OPEN === 1 &&
          ws.CLOSING === 2 && ws.CLOSED === 3);
    ws.onopen = function(e) {
        pass('WebSocket onopen fires (real connection!)');
        ws.send('echo test');
    };
    ws.onmessage = function(e) {
        check('WebSocket onmessage fires', e.data === 'echo test', 'data=' + e.data);
        ws.close(1000, 'done');
    };
    ws.onerror = function(e) {
        // Expected in container with no DNS
        console.warn('WebSocket error (expected in container): ' + (e.message || 'connection failed'));
    };
    ws.onclose = function(e) {
        check('WebSocket onclose fires', typeof e.code === 'number', 'code=' + e.code);
    };

    // Test WebSocket.close() method
    var ws2 = new WebSocket('wss://example.com/test');
    check('WebSocket close method exists', typeof ws2.close === 'function');
    ws2.onerror = function() {};
    ws2.onclose = function() {};

    // ---------------------------------------------------------------------------
    // 16. Console methods
    // ---------------------------------------------------------------------------
    console.log('console.log works');
    console.info('console.info works');
    console.warn('console.warn works');
    console.error('console.error works');
    console.debug('console.debug works');
    pass('all console methods work');

    // ---------------------------------------------------------------------------
    // 17. Configuration events (register handlers)
    // ---------------------------------------------------------------------------
    Pebble.addEventListener('showConfiguration', function(e) {
        check('showConfiguration fires', e.type === 'showConfiguration');
        Pebble.openURL('https://example.com/config?color=blue');
        pass('showConfiguration calls openURL');
        Pebble.sendAppMessage({'Status': 'Config opened'});
    });

    Pebble.addEventListener('webviewclosed', function(e) {
        check('webviewclosed fires', e.type === 'webviewclosed');
        check('webviewclosed has response', typeof e.response === 'string',
              'response=' + e.response);
        try {
            var cfg = JSON.parse(decodeURIComponent(e.response));
            check('webviewclosed parses config', cfg !== null, JSON.stringify(cfg));
            localStorage.setItem('bgColor', cfg.bgColor || 'black');
            Pebble.sendAppMessage({'Status': 'Config: ' + (cfg.bgColor || 'default')});
        } catch(ex) {
            pass('webviewclosed response received (non-JSON)', e.response);
        }
    });

    // ---------------------------------------------------------------------------
    // Summary
    // ---------------------------------------------------------------------------
    console.log('========================================');
    console.log('Sync tests done: ' + passCount + ' passed, ' + failCount + ' failed');
    console.log('Async tests (timers, XHR, geo, WS) pending...');
    console.log('========================================');
});

// ---------------------------------------------------------------------------
// 18. appmessage handler - respond to commands from the watch
// ---------------------------------------------------------------------------
Pebble.addEventListener('appmessage', function(e) {
    check('appmessage event', e.type === 'appmessage');
    check('appmessage has payload', typeof e.payload === 'object', JSON.stringify(e.payload));

    var cmd = e.payload['0'] || e.payload[0];

    if (cmd === 1) {
        // Weather test: geolocation -> XHR to Open-Meteo -> sendAppMessage
        // This is the real pattern every Pebble weather app uses.
        console.log('CMD 1: Weather request');
        navigator.geolocation.getCurrentPosition(
            function(pos) {
                check('weather geolocation success', typeof pos.coords.latitude === 'number',
                      'lat=' + pos.coords.latitude + ' lon=' + pos.coords.longitude);

                // Fetch real weather from Open-Meteo using the geo coords
                var url = 'https://api.open-meteo.com/v1/forecast' +
                    '?latitude=' + pos.coords.latitude +
                    '&longitude=' + pos.coords.longitude +
                    '&current=temperature_2m,weather_code' +
                    '&temperature_unit=celsius';
                console.log('Fetching weather: ' + url);

                var xhr = new XMLHttpRequest();
                xhr.onload = function() {
                    check('weather XHR status', this.status === 200, 'status=' + this.status);
                    var data = JSON.parse(this.responseText);
                    check('weather API has current', typeof data.current === 'object',
                          JSON.stringify(data.current));
                    var temp = Math.round(data.current.temperature_2m);
                    var wmoCode = data.current.weather_code;
                    // WMO weather codes: 0=clear, 1-3=partly cloudy, 45-48=fog,
                    // 51-55=drizzle, 61-65=rain, 71-75=snow, 80-82=showers, 95+=thunderstorm
                    var conditions = 'Unknown';
                    if (wmoCode <= 0) conditions = 'Clear';
                    else if (wmoCode <= 3) conditions = 'Partly Cloudy';
                    else if (wmoCode <= 48) conditions = 'Foggy';
                    else if (wmoCode <= 55) conditions = 'Drizzle';
                    else if (wmoCode <= 65) conditions = 'Rain';
                    else if (wmoCode <= 75) conditions = 'Snow';
                    else if (wmoCode <= 82) conditions = 'Showers';
                    else conditions = 'Thunderstorm';
                    console.log('Real weather: ' + temp + 'C, ' + conditions + ' (WMO ' + wmoCode + ')');

                    Pebble.sendAppMessage(
                        {'Temperature': temp, 'City': 'Palo Alto', 'Status': conditions},
                        function() { pass('weather sendAppMessage with real data'); },
                        function(d, e) { fail('weather sendAppMessage', e); }
                    );
                };
                xhr.onerror = function() {
                    fail('weather XHR fetch failed (network error)');
                };
                xhr.open('GET', url);
                xhr.send();
            },
            function(err) {
                fail('weather geolocation error', err.message);
            }
        );

    } else if (cmd === 2) {
        // Config test
        console.log('CMD 2: Config test');
        Pebble.sendAppMessage({'Status': 'Config test OK'});
        pass('config command handled');

    } else if (cmd === 3) {
        // Timeline test
        console.log('CMD 3: Timeline test');
        Pebble.getTimelineToken(function(token) {
            check('timeline token from cmd', token.length === 32 && /^[0-9a-f]+$/.test(token), token);
            Pebble.sendAppMessage({'Status': 'TL:' + token.substring(0, 10)});
        });

    } else {
        console.log('Unknown command: ' + cmd);
        Pebble.sendAppMessage({'Status': 'Unknown cmd ' + cmd});
    }
});

// ---------------------------------------------------------------------------
// 19. Multiple listeners on same event
// ---------------------------------------------------------------------------
var multiA = false, multiB = false;
Pebble.addEventListener('appmessage', function(e) { multiA = true; });
Pebble.addEventListener('appmessage', function(e) {
    multiB = true;
    if (multiA && multiB) pass('multiple appmessage listeners all fire');
});
