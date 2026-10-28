var Compass = {
    Uncalibrated: 0,
    Calibrating: 1,
    Calibrated: 2
};

var pebble = new PebbleWebSocket(host, port);
pebble.on('open', function() {
    var samples = [];
    var use_sensors;

    var updateAccelText = _.throttle(function(accel) {
        var pebble_accel = convert_to_pebble_accel(accel);
        document.getElementById('accel-x').innerHTML = accel.x.toFixed(2);
        document.getElementById('accel-y').innerHTML = accel.y.toFixed(2);
        document.getElementById('accel-z').innerHTML = accel.z.toFixed(2);
        document.getElementById('pebble-accel-x').innerHTML = pebble_accel[0];
        document.getElementById('pebble-accel-y').innerHTML = pebble_accel[1];
        document.getElementById('pebble-accel-z').innerHTML = pebble_accel[2];
        document.getElementById('accel-x-slider').value = pebble_accel[0];
        document.getElementById('accel-y-slider').value = pebble_accel[1];
        document.getElementById('accel-z-slider').value = pebble_accel[2];
    }, 100);
    var updateHeadingText = function(heading) {
        document.getElementById('heading').innerHTML = Math.round(heading);
        document.getElementById('pebble-heading').innerHTML = convert_to_pebble_heading(heading);
    };
    var updateCompassOrientation = function(heading) {
        propeller.angle = 360 - Math.round(heading);
    };


    var convert_to_pebble_accel = function(accel) {
        return _.map(accel, function(v, k) { return (v / 0.00981).toFixed(0)|0});
    };
    var convert_to_pebble_heading = function(heading) {
        return (Math.ceil(heading * 65536 / 360)).toFixed(0)|0;
    };

    var send_queued_samples = _.throttle(function() {
        if(pebble != null) {
            pebble.emu_set_accel(samples);
        }
        samples = [];
    }, 300);


    var isReversed = /Android/i.test(navigator.userAgent);
    window.ondevicemotion = _.throttle(function(e) {
        if(!document.getElementById('use_sensors').checked) { return; }
        var accel = _.clone(e.accelerationIncludingGravity);
        if(accel.x == null) { return; }
        if(isReversed) {
            accel.x = -accel.x;
            accel.y = -accel.y;
            accel.z = -accel.z;
        }
        samples.push(convert_to_pebble_accel(accel));
        updateAccelText(accel);
        send_queued_samples();
    }, 10);
    window.ondeviceorientation = _.throttle(function(e) {
        if(!document.getElementById('use_sensors').checked) { return; }
        if(e.webkitCompassHeading !== undefined) {
            var heading = e.webkitCompassHeading;
        } else if(e.alpha !== null) {
        // Should handle when phone is flipped the other direction
            var heading = window.innerWidth < window.innerHeight ? e.alpha : e.alpha + 90;
        } else {
            return;
        }
        updateHeadingText(heading);
        updateCompassOrientation(heading);
        if(pebble != null) {
            pebble.emu_set_compass(convert_to_pebble_heading(heading), Compass.Calibrated);
        }
    }, 500);


    var normalize = function(angle) {
        angle = angle % 360;
        return angle < 0 ? -angle : 360 - angle;
    }
    var propeller = new Propeller('#compass-bg', {inertia: 0, step: 1, speed: 0, onDragStop: function() {
        document.getElementById('use_sensors').checked = false;
        var heading = normalize(this.angle);
        updateHeadingText(heading);
        if(pebble != null) {
            pebble.emu_set_compass(convert_to_pebble_heading(heading), Compass.Calibrated);
        }
    }});
    var updateAccelFromManualInput = function() {
        document.getElementById('use_sensors').checked = false;
        var accel = {
            x: parseInt(document.getElementById('accel-x-slider').value, 10)*0.00981,
            y: parseInt(document.getElementById('accel-y-slider').value, 10)*0.00981,
            z: parseInt(document.getElementById('accel-z-slider').value, 10)*0.00981,
        };
        samples.push(convert_to_pebble_accel(accel));
        updateAccelText(accel);
        send_queued_samples();
    };
    document.getElementById('accel-x-slider').addEventListener("input", updateAccelFromManualInput);
    document.getElementById('accel-y-slider').addEventListener("input", updateAccelFromManualInput);
    document.getElementById('accel-z-slider').addEventListener("input", updateAccelFromManualInput);

    var use_sensors = document.getElementById('use_sensors');
    // Browsers only give secure (HTTPS) pages motion sensors, and this page is plain HTTP, so most phones
    // won't. The compass and sliders work regardless.
    var has_sensors = window.isSecureContext !== false && !!(window.DeviceMotionEvent && window.DeviceOrientationEvent);
    var no_sensors = function(why) {
        use_sensors.checked = false;
        use_sensors.disabled = true;
        document.getElementById('state').innerHTML = "Connected: " + why + " Drag the compass and sliders instead.";
    };
    use_sensors.addEventListener("change", function() {
        // iOS asks the user before handing over motion sensors.
        if(use_sensors.checked && typeof DeviceMotionEvent.requestPermission === 'function') {
            DeviceMotionEvent.requestPermission().then(function(state) {
                if(state !== 'granted') { no_sensors("this browser won't share its motion sensors."); }
            }).catch(function() { no_sensors("this browser won't share its motion sensors."); });
        }
    });

    document.getElementById('stuff').style.display = "block";
    if(has_sensors) {
        document.getElementById('state').innerHTML = "Transmitting";
    } else {
        no_sensors("this browser only shares motion sensors with HTTPS pages.");
    }
});

pebble.on('close', function() {
    window.ondevicemotion = null;
    window.ondeviceorientation = null;
    document.getElementById('state').innerHTML = "Disconnected";
    document.getElementById('stuff').style.display = "none";
    pebble = null;
});
