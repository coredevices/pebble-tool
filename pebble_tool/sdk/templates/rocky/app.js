// https://developer.pebble.com/docs/pebblekit-js/Pebble/#on
Pebble.on('message', function(event) {
  console.log('Message received from watch:', event.data);
});
