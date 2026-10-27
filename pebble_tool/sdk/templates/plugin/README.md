# ${display_name}

A Pebble plugin: JavaScript that runs on the phone and serves data (sources) and commands
(actions) to watchfaces and apps, through `Pebble.subscribeToSource` and `Pebble.invokeAction`
in their PebbleKit JS.

* `package.json` declares what the plugin serves, under `pebble.plugin`.
* `src/plugin/index.js` answers the requests.

Build it with `pebble build`, and try it in the emulator with
`pebble install --emulator emery` alongside an app that uses it.
