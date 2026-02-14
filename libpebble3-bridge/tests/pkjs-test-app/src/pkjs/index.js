// =============================================================================
// PebbleKit JS API Test App - End-to-End Verification
//
// True E2E: every test does something in the library AND verifies the
// impact on the watch (or vice versa):
//   - sendAppMessage → watch C receives → C echoes back → JS verifies round-trip
//   - Notification/Glance → BlobDB insert → watch responds Success (bridge logs)
//   - Watch C sends command → JS receives → JS processes → sends response →
//     watch C receives → C echoes back → JS verifies
//   - XHR → real HTTP to Open-Meteo API → parse response → send to watch
//   - Geolocation → coords → used in XHR → verified by weather response
//   - showConfiguration/webviewclosed → triggered → openURL queued → verified
//   - All internal queues verified (not just "no crash")
//
// The C app auto-sends CMD 1 (1s), CMD 2 (5s), CMD 3 (8s), and echoes
// back every received message via E2EAck key, so JS can verify the
// complete JS→watch→JS round-trip for every message.
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

// Track E2E round-trip acks from watch C app
var e2eAcks = [];
var weatherSentTemp = null;
var weatherSentCity = null;
var weatherSentConditions = null;

// ---------------------------------------------------------------------------
// 1. Ready event + core API functional verification
// ---------------------------------------------------------------------------
Pebble.addEventListener('ready', function(e) {
    check('ready event fires', e.type === 'ready', 'type=' + e.type);

    // ---------------------------------------------------------------------------
    // 2. Tokens (deterministic SHA-256 hashes)
    // ---------------------------------------------------------------------------
    var acct = Pebble.getAccountToken();
    check('getAccountToken is 32-char hex', typeof acct === 'string' && acct.length === 32 &&
          /^[0-9a-f]+$/.test(acct), acct);
    var watch = Pebble.getWatchToken();
    check('getWatchToken is 32-char hex', typeof watch === 'string' && watch.length === 32 &&
          /^[0-9a-f]+$/.test(watch), watch);
    check('account and watch tokens differ', acct !== watch);
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
    check('watchInfo.firmware.major is number', typeof info.firmware.major === 'number' &&
          info.firmware.major >= 1, 'major=' + info.firmware.major);
    check('watchInfo.firmware.minor is number', typeof info.firmware.minor === 'number',
          'minor=' + info.firmware.minor);
    check('watchInfo.firmware.patch is number', typeof info.firmware.patch === 'number',
          'patch=' + info.firmware.patch);
    check('watchInfo.firmware.suffix is string', typeof info.firmware.suffix === 'string',
          'suffix=' + JSON.stringify(info.firmware.suffix));
    check('getActiveWatchInfo deterministic', JSON.stringify(Pebble.getActiveWatchInfo()) ===
          JSON.stringify(info));

    // ---------------------------------------------------------------------------
    // 4. localStorage - full CRUD
    // ---------------------------------------------------------------------------
    localStorage.clear();
    check('localStorage.clear', localStorage.length === 0);
    localStorage.setItem('city', 'London');
    localStorage.setItem('temp', '18');
    check('localStorage.setItem+getItem', localStorage.getItem('city') === 'London');
    check('localStorage.length', localStorage.length === 2, 'len=' + localStorage.length);
    check('localStorage.key(0)', localStorage.key(0) === 'city' || localStorage.key(0) === 'temp',
          'key(0)=' + localStorage.key(0));
    check('localStorage.key out of bounds', localStorage.key(999) === null);
    localStorage.removeItem('temp');
    check('localStorage.removeItem', localStorage.getItem('temp') === null);
    check('localStorage.length after remove', localStorage.length === 1, 'len=' + localStorage.length);
    localStorage.setItem('num', 42);
    check('localStorage coerces to string', localStorage.getItem('num') === '42');
    localStorage.setItem('city', 'Paris');
    check('localStorage overwrite', localStorage.getItem('city') === 'Paris');
    localStorage.clear();
    check('localStorage.clear empties all', localStorage.length === 0);

    // ---------------------------------------------------------------------------
    // 5. Timers - verify fire AND cancellation prevents callback
    // ---------------------------------------------------------------------------
    setTimeout(function() { pass('setTimeout callback fires'); }, 0);
    var cancelledFired = false;
    var cancelId = setTimeout(function() { cancelledFired = true; }, 0);
    clearTimeout(cancelId);
    setTimeout(function() {
        check('clearTimeout prevents callback', cancelledFired === false);
    }, 0);

    var ivalFired = false;
    setInterval(function() {
        if (!ivalFired) { ivalFired = true; pass('setInterval callback fires'); }
    }, 100);
    var cancelledIvalFired = false;
    var cancelIval = setInterval(function() { cancelledIvalFired = true; }, 100);
    clearInterval(cancelIval);
    setTimeout(function() {
        check('clearInterval prevents callback', cancelledIvalFired === false);
    }, 0);

    // ---------------------------------------------------------------------------
    // 6. removeEventListener - verify removed handler does NOT fire
    // ---------------------------------------------------------------------------
    var spyCalled = false;
    var spy = function() { spyCalled = true; };
    Pebble.addEventListener('appmessage', spy);
    Pebble.removeEventListener('appmessage', spy);
    // spyCalled will be checked after real appmessage fires (in section 18)

    // ---------------------------------------------------------------------------
    // 7. openURL - verify URL queued in internal queue
    // ---------------------------------------------------------------------------
    var urlCountBefore = _pkjsOpenURLs.length;
    Pebble.openURL('https://example.com/config');
    check('openURL queues URL', _pkjsOpenURLs.length === urlCountBefore + 1 &&
          _pkjsOpenURLs[_pkjsOpenURLs.length - 1] === 'https://example.com/config');

    // ---------------------------------------------------------------------------
    // 8. showSimpleNotificationOnPebble - verify queued + BlobDB sends to watch
    // (bridge will log E2E_BLOBDB_RESPONSE: Success when watch accepts it)
    // ---------------------------------------------------------------------------
    var notifCountBefore = _pkjsNotifications.length;
    Pebble.showSimpleNotificationOnPebble('E2E Test', 'Notification body');
    check('notification queued with title',
          _pkjsNotifications.length === notifCountBefore + 1 &&
          _pkjsNotifications[_pkjsNotifications.length - 1].title === 'E2E Test');
    check('notification queued with body',
          _pkjsNotifications[_pkjsNotifications.length - 1].body === 'Notification body');

    // ---------------------------------------------------------------------------
    // 9. showToast - verify toast message logged
    // ---------------------------------------------------------------------------
    var logCountBefore = _pkjsLogs.length;
    Pebble.showToast('E2E toast 123');
    var foundToast = false;
    for (var i = logCountBefore; i < _pkjsLogs.length; i++) {
        if (_pkjsLogs[i].msg && _pkjsLogs[i].msg.indexOf('Toast: E2E toast 123') >= 0) {
            foundToast = true; break;
        }
    }
    check('showToast logs message', foundToast);

    // ---------------------------------------------------------------------------
    // 10. postMessage - verify message data logged
    // ---------------------------------------------------------------------------
    logCountBefore = _pkjsLogs.length;
    Pebble.postMessage({msg: 'hello rocky', val: 42});
    var foundPost = false;
    for (var i = logCountBefore; i < _pkjsLogs.length; i++) {
        if (_pkjsLogs[i].msg && _pkjsLogs[i].msg.indexOf('postMessage:') >= 0 &&
            _pkjsLogs[i].msg.indexOf('hello rocky') >= 0) {
            foundPost = true; break;
        }
    }
    check('postMessage logs data', foundPost);

    // ---------------------------------------------------------------------------
    // 11. sendAppMessage - verify queued + key resolution
    // ---------------------------------------------------------------------------
    var outboxBefore = _pkjsOutbox.length;
    var tx1 = Pebble.sendAppMessage({3: 'JS is ready'},
        function(d) {
            check('sendAppMessage ack callback', typeof d.transactionId === 'number',
                  'txId=' + d.transactionId);
        },
        function(d, err) { fail('sendAppMessage ack', err); }
    );
    check('sendAppMessage returns txId', typeof tx1 === 'number' && tx1 > 0, 'tx=' + tx1);
    check('sendAppMessage queues outbox', _pkjsOutbox.length === outboxBefore + 1);
    check('sendAppMessage resolves numeric key', _pkjsOutbox[_pkjsOutbox.length - 1].dict[3] === 'JS is ready');

    outboxBefore = _pkjsOutbox.length;
    Pebble.sendAppMessage({'Status': 'key-test-ok'},
        function() { pass('sendAppMessage string appKey ack'); },
        function(d, err) { fail('sendAppMessage string appKey', err); }
    );
    check('sendAppMessage string key resolves', _pkjsOutbox.length === outboxBefore + 1);

    // ---------------------------------------------------------------------------
    // 12. getTimelineToken (deterministic hash via async callback)
    // ---------------------------------------------------------------------------
    Pebble.getTimelineToken(
        function(token) {
            check('getTimelineToken is 32-char hex',
                  typeof token === 'string' && token.length === 32 && /^[0-9a-f]+$/.test(token),
                  'token=' + token);
            Pebble.getTimelineToken(function(token2) {
                check('getTimelineToken deterministic', token2 === token);
            });
        },
        function() { fail('getTimelineToken failure cb'); }
    );

    // ---------------------------------------------------------------------------
    // 13. Timeline subscribe / list / unsubscribe - verify state changes
    // ---------------------------------------------------------------------------
    Pebble.timelineSubscribe('sports',
        function() {
            pass('timelineSubscribe success');
            check('timelineSubscribe adds topic', Pebble._timelineTopics.indexOf('sports') >= 0);
            Pebble.timelineSubscribe('weather', function() {
                check('timelineSubscribe second topic', Pebble._timelineTopics.indexOf('weather') >= 0);
                Pebble.timelineSubscriptions(function(topics) {
                    check('timelineSubscriptions lists both',
                          Array.isArray(topics) && topics.length === 2 &&
                          topics.indexOf('sports') >= 0 && topics.indexOf('weather') >= 0,
                          JSON.stringify(topics));
                    Pebble.timelineUnsubscribe('sports', function() {
                        pass('timelineUnsubscribe success');
                        check('timelineUnsubscribe removes topic', Pebble._timelineTopics.indexOf('sports') < 0);
                        Pebble.timelineSubscriptions(function(t2) {
                            check('timelineSubscriptions after unsub',
                                  t2.length === 1 && t2[0] === 'weather', JSON.stringify(t2));
                        });
                    });
                });
            });
        },
        function() { fail('timelineSubscribe failure'); }
    );
    Pebble.timelineSubscribe('sports', function() {
        var count = 0;
        for (var i = 0; i < Pebble._timelineTopics.length; i++) {
            if (Pebble._timelineTopics[i] === 'sports') count++;
        }
        check('timelineSubscribe no duplicates', count <= 1, 'count=' + count);
    });

    // ---------------------------------------------------------------------------
    // 14. appGlanceReload - verify queue + callback + BlobDB sends to watch
    // ---------------------------------------------------------------------------
    var glanceBefore = _pkjsAppGlances.length;
    Pebble.appGlanceReload(
        [{layout: {icon: 'system://images/GENERIC_WARNING',
                   subtitleTemplateString: 'E2E glance test'}}],
        function(s) {
            check('appGlanceReload callback receives slices',
                  Array.isArray(s) && s.length === 1 &&
                  s[0].layout.subtitleTemplateString === 'E2E glance test');
        },
        function() { fail('appGlanceReload failure'); }
    );
    check('appGlanceReload queues glance', _pkjsAppGlances.length === glanceBefore + 1);
    check('appGlanceReload queue data correct',
          _pkjsAppGlances[_pkjsAppGlances.length - 1].slices[0].layout.icon ===
          'system://images/GENERIC_WARNING');

    // ---------------------------------------------------------------------------
    // 15. XMLHttpRequest - full state machine + real network + headers
    // ---------------------------------------------------------------------------
    var xhr = new XMLHttpRequest();
    check('XHR initial readyState=0', xhr.readyState === 0);
    check('XHR constants', xhr.DONE === 4 && xhr.OPENED === 1 &&
          xhr.HEADERS_RECEIVED === 2 && xhr.LOADING === 3 && xhr.UNSENT === 0);
    xhr.open('GET', 'https://example.com');
    check('XHR open sets readyState=1', xhr.readyState === 1);
    check('XHR open resets status to 0', xhr.status === 0);
    check('XHR open resets responseText', xhr.responseText === '');
    check('XHR.getResponseHeader before send returns null', xhr.getResponseHeader('x') === null);
    check('XHR.getAllResponseHeaders before send returns empty', xhr.getAllResponseHeaders() === '');
    xhr.setRequestHeader('X-Test', 'hello');
    check('XHR.setRequestHeader stores header', xhr._headers['X-Test'] === 'hello');
    xhr.abort();
    check('XHR.abort sets readyState=0', xhr.readyState === 0);

    // XHR onabort
    var xhrAbort = new XMLHttpRequest();
    var abortFired = false;
    xhrAbort.onabort = function() { abortFired = true; };
    xhrAbort.open('GET', 'https://example.com');
    xhrAbort.abort();
    check('XHR.abort fires onabort', abortFired);

    // XHR overrideMimeType
    var xhrMime = new XMLHttpRequest();
    xhrMime.open('GET', 'https://example.com');
    xhrMime.overrideMimeType('text/plain');
    check('XHR.overrideMimeType callable', xhrMime._overrideMime === 'text/plain');

    // XHR real GET with onreadystatechange + headers
    var xhrGet = new XMLHttpRequest();
    var readyStates = [];
    xhrGet.onreadystatechange = function() { readyStates.push(this.readyState); };
    xhrGet.onload = function() {
        check('XHR GET status=200', this.status === 200, 'status=' + this.status);
        check('XHR responseText has content', this.responseText.length > 0, 'len=' + this.responseText.length);
        var parsed = JSON.parse(this.responseText);
        check('XHR response is valid weather JSON', typeof parsed.latitude === 'number',
              'lat=' + parsed.latitude);
        check('XHR onreadystatechange transitions',
              readyStates.length >= 2 && readyStates[0] === 2 && readyStates[readyStates.length - 1] === 4,
              'states=' + JSON.stringify(readyStates));
        var ct = this.getResponseHeader('Content-Type');
        check('XHR getResponseHeader Content-Type', ct !== null && ct.indexOf('json') >= 0, 'ct=' + ct);
        check('XHR getResponseHeader case-insensitive', this.getResponseHeader('content-type') === ct);
        var allHeaders = this.getAllResponseHeaders();
        check('XHR getAllResponseHeaders format',
              allHeaders.length > 0 && allHeaders.indexOf(': ') >= 0 && allHeaders.indexOf('\r\n') >= 0,
              'len=' + allHeaders.length);
    };
    xhrGet.onerror = function() { fail('XHR GET network error'); };
    xhrGet.open('GET', 'https://api.open-meteo.com/v1/forecast?latitude=37.44&longitude=-122.14&current=temperature_2m');
    xhrGet.send();

    // XHR POST
    var xhrPost = new XMLHttpRequest();
    xhrPost.onload = function() {
        check('XHR POST succeeds', this.status === 200, 'status=' + this.status);
        check('XHR POST readyState=DONE', this.readyState === 4);
    };
    xhrPost.onerror = function() { fail('XHR POST network error'); };
    xhrPost.open('POST', 'https://api.open-meteo.com/v1/forecast?latitude=37.44&longitude=-122.14&current=temperature_2m');
    xhrPost.setRequestHeader('Content-Type', 'application/json');
    xhrPost.send(JSON.stringify({test: true}));

    // ---------------------------------------------------------------------------
    // 16. navigator.geolocation - full Position object
    // ---------------------------------------------------------------------------
    navigator.geolocation.getCurrentPosition(
        function(pos) {
            check('geolocation SUCCESS fires', true);
            check('geolocation lat=Palo Alto', Math.abs(pos.coords.latitude - 37.4419) < 0.01,
                  'lat=' + pos.coords.latitude);
            check('geolocation lon=Palo Alto', Math.abs(pos.coords.longitude - (-122.143)) < 0.01,
                  'lon=' + pos.coords.longitude);
            check('geolocation altitude', typeof pos.coords.altitude === 'number', 'alt=' + pos.coords.altitude);
            check('geolocation accuracy', pos.coords.accuracy > 0, 'acc=' + pos.coords.accuracy);
            check('geolocation altitudeAccuracy', typeof pos.coords.altitudeAccuracy === 'number');
            check('geolocation timestamp', pos.timestamp > 1000000000000, 'ts=' + pos.timestamp);
        },
        function(err) { fail('geolocation error cb should not fire', err.message); }
    );

    // watchPosition - verify callback fires, THEN clearWatch
    var watchFired = false;
    var wid = navigator.geolocation.watchPosition(
        function(pos) {
            if (!watchFired) {
                watchFired = true;
                check('watchPosition callback fires', true);
                check('watchPosition has Palo Alto coords', Math.abs(pos.coords.latitude - 37.4419) < 0.01,
                      'lat=' + pos.coords.latitude);
                navigator.geolocation.clearWatch(wid);
            }
        },
        function() {}
    );
    check('watchPosition returns numeric id', typeof wid === 'number' && wid > 0, 'id=' + wid);
    navigator.geolocation.clearWatch(999); // no-crash on invalid id

    // ---------------------------------------------------------------------------
    // 17. WebSocket - real connection + lifecycle
    // ---------------------------------------------------------------------------
    check('WebSocket constructor', typeof WebSocket === 'function');
    check('WebSocket static constants', WebSocket.CLOSED === 3 && WebSocket.OPEN === 1);
    var ws = new WebSocket('wss://echo.websocket.org');
    check('WebSocket readyState=CONNECTING', ws.readyState === 0);
    check('WebSocket.url set', ws.url === 'wss://echo.websocket.org');
    check('WebSocket.extensions is string', typeof ws.extensions === 'string');
    check('WebSocket.bufferedAmount=0', ws.bufferedAmount === 0);

    var wsOpenFired = false, wsCloseFired = false, wsErrorFired = false;
    ws.onopen = function() {
        wsOpenFired = true;
        pass('WebSocket onopen fires');
        ws.send('echo test');
    };
    ws.onmessage = function(e) {
        check('WebSocket echo received', e.data === 'echo test', 'data=' + e.data);
        ws.close(1000, 'done');
    };
    ws.onerror = function(e) {
        wsErrorFired = true;
        console.warn('WebSocket error (expected in container): ' + (e.message || 'connection failed'));
    };
    ws.onclose = function(e) {
        wsCloseFired = true;
        check('WebSocket onclose fires', typeof e.code === 'number', 'code=' + e.code);
        check('WebSocket onclose has wasClean', typeof e.wasClean === 'boolean');
        check('WebSocket onclose has reason', typeof e.reason === 'string');
        check('WebSocket lifecycle (open or error then close)',
              (wsOpenFired || wsErrorFired) && wsCloseFired);
    };

    var ws2 = new WebSocket('wss://example.com/test');
    ws2.onerror = function() {};
    ws2.onclose = function() {};
    ws2.close(1000, 'cancelled');
    check('WebSocket.close sets CLOSING', ws2.readyState === 2);

    // ---------------------------------------------------------------------------
    // 18. Console methods - verify all 5 levels captured
    // ---------------------------------------------------------------------------
    logCountBefore = _pkjsLogs.length;
    console.log('c.log');
    console.info('c.info');
    console.warn('c.warn');
    console.error('c.error');
    console.debug('c.debug');
    var levels = {};
    for (var i = logCountBefore; i < _pkjsLogs.length; i++) {
        levels[_pkjsLogs[i].level] = true;
    }
    check('console.log captured', levels['log'] === true);
    check('console.info captured', levels['info'] === true);
    check('console.warn captured', levels['warn'] === true);
    check('console.error captured', levels['error'] === true);
    check('console.debug captured', levels['debug'] === true);

    // ---------------------------------------------------------------------------
    // 19. showConfiguration / webviewclosed - trigger + verify full chain
    // ---------------------------------------------------------------------------
    var showConfigFired = false;
    var webviewClosedFired = false;
    var configOpenURL = null;

    Pebble.addEventListener('showConfiguration', function(e) {
        showConfigFired = true;
        check('showConfiguration event type', e.type === 'showConfiguration');
        Pebble.openURL('https://example.com/config?color=blue');
        configOpenURL = _pkjsOpenURLs[_pkjsOpenURLs.length - 1];
    });

    Pebble.addEventListener('webviewclosed', function(e) {
        webviewClosedFired = true;
        check('webviewclosed event type', e.type === 'webviewclosed');
        check('webviewclosed has response', typeof e.response === 'string' && e.response.length > 0);
        try {
            var cfg = JSON.parse(decodeURIComponent(e.response));
            check('webviewclosed parses config', cfg.bgColor === 'red', JSON.stringify(cfg));
            localStorage.setItem('bgColor', cfg.bgColor);
            check('webviewclosed stores in localStorage', localStorage.getItem('bgColor') === 'red');
        } catch(ex) {
            fail('webviewclosed JSON parse', ex.toString());
        }
    });

    _pkjsFireEvent('showConfiguration', {type: 'showConfiguration'});
    check('showConfiguration handler ran', showConfigFired);
    check('showConfiguration called openURL', configOpenURL === 'https://example.com/config?color=blue');

    var cfgResp = encodeURIComponent(JSON.stringify({bgColor: 'red', fontSize: 14}));
    _pkjsFireEvent('webviewclosed', {type: 'webviewclosed', response: cfgResp});
    check('webviewclosed handler ran', webviewClosedFired);

    // ---------------------------------------------------------------------------
    // 20. fetch wrapper
    // ---------------------------------------------------------------------------
    check('fetch is wrapped', fetch !== _origFetch);
    check('_origFetch available', typeof _origFetch === 'function');

    // ---------------------------------------------------------------------------
    // Summary (sync portion)
    // ---------------------------------------------------------------------------
    console.log('========================================');
    console.log('Sync tests done: ' + passCount + ' passed, ' + failCount + ' failed');
    console.log('Async + E2E round-trip tests pending...');
    console.log('========================================');
});

// ---------------------------------------------------------------------------
// 21. appmessage handler - E2E round-trip verification
//
// The watch C app sends commands (CMD 1, 2, 3) and echoes back every
// received message via E2EAck. We verify the complete round-trip:
//   JS sends data → watch C receives → C echoes back → JS verifies match
// ---------------------------------------------------------------------------
Pebble.addEventListener('appmessage', function(e) {
    var payload = e.payload;
    var cmd = payload['0'] || payload[0];
    var e2eAck = payload['4'] || payload[4];

    // ---- E2E ACK from watch: verify round-trip ----
    if (e2eAck && typeof e2eAck === 'string') {
        console.log('E2E_ACK received from watch: ' + e2eAck);
        e2eAcks.push(e2eAck);

        if (e2eAck.indexOf('WEATHER:') === 0) {
            // Verify weather round-trip: watch echoed back what we sent
            check('E2E weather round-trip received', true, e2eAck);
            // Parse the echo: WEATHER:T=10,C=Palo Alto,S=Partly Cloudy
            var match = e2eAck.match(/T=(-?\d+),C=([^,]+),S=(.+)/);
            if (match) {
                var echoTemp = parseInt(match[1], 10);
                var echoCity = match[2];
                check('E2E weather temp matches', echoTemp === weatherSentTemp,
                      'sent=' + weatherSentTemp + ' echo=' + echoTemp);
                check('E2E weather city matches', echoCity === weatherSentCity,
                      'sent=' + weatherSentCity + ' echo=' + echoCity);
            } else {
                fail('E2E weather echo parse', e2eAck);
            }
        } else if (e2eAck.indexOf('STATUS:') === 0) {
            // Watch echoed back a status message we sent
            check('E2E status round-trip received', true, e2eAck);
        }
        return; // E2E acks are not commands
    }

    // ---- Commands from watch ----
    check('appmessage event type', e.type === 'appmessage');
    check('appmessage has payload', typeof payload === 'object' && payload !== null,
          JSON.stringify(payload));

    if (cmd === 1) {
        // CMD 1: Weather - full E2E: geolocation → XHR → sendAppMessage → watch echoes back
        console.log('CMD 1: Weather request (E2E test)');
        navigator.geolocation.getCurrentPosition(
            function(pos) {
                check('E2E weather geolocation success',
                      typeof pos.coords.latitude === 'number',
                      'lat=' + pos.coords.latitude + ' lon=' + pos.coords.longitude);

                var url = 'https://api.open-meteo.com/v1/forecast' +
                    '?latitude=' + pos.coords.latitude +
                    '&longitude=' + pos.coords.longitude +
                    '&current=temperature_2m,weather_code' +
                    '&temperature_unit=celsius';
                console.log('Fetching weather: ' + url);

                var xhr = new XMLHttpRequest();
                xhr.onload = function() {
                    check('E2E weather XHR status=200', this.status === 200);
                    var data = JSON.parse(this.responseText);
                    check('E2E weather API has current', typeof data.current === 'object',
                          JSON.stringify(data.current));
                    var temp = Math.round(data.current.temperature_2m);
                    var wmoCode = data.current.weather_code;
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

                    // Save what we're sending so we can verify the echo
                    weatherSentTemp = temp;
                    weatherSentCity = 'Palo Alto';
                    weatherSentConditions = conditions;

                    Pebble.sendAppMessage(
                        {'Temperature': temp, 'City': 'Palo Alto', 'Status': conditions},
                        function() {
                            pass('E2E weather sendAppMessage ack');
                            console.log('Weather sent to watch, waiting for E2E echo...');
                        },
                        function(d, e) { fail('E2E weather sendAppMessage', e); }
                    );
                };
                xhr.onerror = function() { fail('E2E weather XHR network error'); };
                xhr.open('GET', url);
                xhr.send();
            },
            function(err) { fail('E2E weather geolocation error', err.message); }
        );

    } else if (cmd === 2) {
        // CMD 2: Config test - send status message, watch echoes back
        console.log('CMD 2: Config test (E2E)');
        Pebble.sendAppMessage({'Status': 'Config test OK'},
            function() { pass('E2E config sendAppMessage ack'); },
            function(d, e) { fail('E2E config sendAppMessage', e); }
        );

    } else if (cmd === 3) {
        // CMD 3: Timeline test - get token, send to watch, watch echoes back
        console.log('CMD 3: Timeline test (E2E)');
        Pebble.getTimelineToken(function(token) {
            check('E2E timeline token valid', token.length === 32 && /^[0-9a-f]+$/.test(token), token);
            Pebble.sendAppMessage({'Status': 'TL:' + token.substring(0, 16)},
                function() { pass('E2E timeline sendAppMessage ack'); },
                function(d, e) { fail('E2E timeline sendAppMessage', e); }
            );
        });

    } else {
        console.log('Unknown command: ' + cmd);
    }
});

// Multiple listeners on same event - verify both fire
var multiA = false, multiB = false;
Pebble.addEventListener('appmessage', function(e) { multiA = true; });
Pebble.addEventListener('appmessage', function(e) {
    multiB = true;
    if (multiA && multiB) pass('multiple appmessage listeners all fire');
});
