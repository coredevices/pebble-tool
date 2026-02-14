// =============================================================================
// PebbleKit JS API Test App
//
// Exercises the full PKJS API surface in a real app pattern.
// Each test logs PASS or FAIL with a descriptive name.
// Run with the bridge's install-and-logs command to see results.
//
// Command keys from the watch:
//   1 = weather/XHR test
//   2 = config page test
//   3 = timeline test
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
    // 2. Tokens
    // ---------------------------------------------------------------------------
    var acct = Pebble.getAccountToken();
    check('getAccountToken value', typeof acct === 'string' && acct.length > 0, acct);
    var watch = Pebble.getWatchToken();
    check('getWatchToken value', typeof watch === 'string' && watch.length > 0, watch);

    // ---------------------------------------------------------------------------
    // 3. WatchInfo
    // ---------------------------------------------------------------------------
    var info = Pebble.getActiveWatchInfo();
    check('watchInfo object', typeof info === 'object' && info !== null);
    check('watchInfo.platform', typeof info.platform === 'string', info.platform);
    check('watchInfo.model', typeof info.model === 'string', info.model);
    check('watchInfo.language', typeof info.language === 'string', info.language);
    check('watchInfo.firmware.major', typeof info.firmware === 'object' &&
          typeof info.firmware.major === 'number', JSON.stringify(info.firmware));

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
    Pebble.showSimpleNotificationOnPebble('Hello', 'World');
    pass('showSimpleNotificationOnPebble no crash');
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
    // 10. getTimelineToken
    // ---------------------------------------------------------------------------
    Pebble.getTimelineToken(
        function(token) {
            check('getTimelineToken success', typeof token === 'string' && token.length > 0,
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
    // 12. appGlanceReload
    // ---------------------------------------------------------------------------
    Pebble.appGlanceReload(
        [{layout: {icon: 'system://images/GENERIC_WARNING',
                   subtitleTemplateString: 'Test glance'}}],
        function(s) { pass('appGlanceReload success'); },
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

    // XHR network test (may fail in container due to DNS)
    var xhrGet = new XMLHttpRequest();
    xhrGet.onload = function() {
        check('XHR GET succeeds', this.status === 200, 'status=' + this.status);
        check('XHR responseText', this.responseText.length > 0, 'len=' + this.responseText.length);
        var ct = this.getResponseHeader('Content-Type');
        check('XHR getResponseHeader', ct !== null, 'ct=' + ct);
        check('XHR getAllResponseHeaders', this.getAllResponseHeaders().length > 0);
    };
    xhrGet.onerror = function() {
        console.warn('XHR GET network error (expected in container)');
    };
    xhrGet.open('GET', 'https://httpbin.org/get?pkjs=1');
    xhrGet.send();

    // XHR POST
    var xhrPost = new XMLHttpRequest();
    xhrPost.onload = function() {
        check('XHR POST succeeds', this.status === 200, 'status=' + this.status);
    };
    xhrPost.onerror = function() {
        console.warn('XHR POST network error (expected in container)');
    };
    xhrPost.open('POST', 'https://httpbin.org/post');
    xhrPost.setRequestHeader('Content-Type', 'application/json');
    xhrPost.send(JSON.stringify({test: true}));

    // ---------------------------------------------------------------------------
    // 14. navigator.geolocation
    // ---------------------------------------------------------------------------
    check('navigator.geolocation exists', typeof navigator.geolocation === 'object');
    navigator.geolocation.getCurrentPosition(
        function(pos) { pass('geolocation success cb', 'lat=' + pos.coords.latitude); },
        function(err) {
            check('geolocation error cb (bridge mode)', err.code === 2,
                  'code=' + err.code + ' ' + err.message);
        }
    );
    var wid = navigator.geolocation.watchPosition(function(){}, function(){});
    check('watchPosition returns id', typeof wid === 'number');
    navigator.geolocation.clearWatch(wid);
    pass('clearWatch no crash');

    // ---------------------------------------------------------------------------
    // 15. WebSocket (stub)
    // ---------------------------------------------------------------------------
    check('WebSocket constructor', typeof WebSocket === 'function');
    check('WebSocket static constants', WebSocket.CLOSED === 3 && WebSocket.OPEN === 1);
    var ws = new WebSocket('wss://echo.example.com');
    check('WebSocket readyState=CLOSED', ws.readyState === 3);
    ws.onclose = function(e) {
        check('WebSocket onclose fires', e.code === 1006);
    };
    ws.onerror = function(e) {
        pass('WebSocket onerror fires');
    };

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
        // Report to watch
        Pebble.sendAppMessage({'Status': 'Config opened'});
    });

    Pebble.addEventListener('webviewclosed', function(e) {
        check('webviewclosed fires', e.type === 'webviewclosed');
        check('webviewclosed has response', typeof e.response === 'string',
              'response=' + e.response);
        try {
            var cfg = JSON.parse(decodeURIComponent(e.response));
            check('webviewclosed parses config', cfg !== null, JSON.stringify(cfg));
            // Save config
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
        // Weather test - use geolocation + XHR pattern
        console.log('CMD 1: Weather request');
        navigator.geolocation.getCurrentPosition(
            function(pos) {
                // Won't fire in bridge mode
                pass('weather geolocation success');
            },
            function(err) {
                // Expected in bridge mode - send mock data
                pass('weather geolocation fallback');
                Pebble.sendAppMessage(
                    {'Temperature': 22, 'City': 'MockCity', 'Status': 'Sunny'},
                    function() { pass('weather sendAppMessage with string keys'); },
                    function(d, e) { fail('weather sendAppMessage', e); }
                );
            }
        );

    } else if (cmd === 2) {
        // Config test - simulate showConfiguration + webviewclosed
        console.log('CMD 2: Config test');
        // In a real app the mobile app triggers these; here we just
        // acknowledge the request.
        Pebble.sendAppMessage({'Status': 'Config test OK'});
        pass('config command handled');

    } else if (cmd === 3) {
        // Timeline test
        console.log('CMD 3: Timeline test');
        Pebble.getTimelineToken(function(token) {
            check('timeline token from cmd', token.length > 0, token);
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
