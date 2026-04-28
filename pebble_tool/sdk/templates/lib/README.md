# ${project_name}

A Pebble package - reusable C (and optionally JS) code that other Pebble
projects can depend on.

## Publishing & using

```sh
pebble package build              # build the package into dist.zip
npm publish                       # publish to npm so other projects can install
```

In a consuming project:

```sh
pebble package install ${project_name}
```

then add `"${project_name}": "*"` under `pebble.dependencies` in that
project's `package.json`.

## Target platforms

`targetPlatforms` in `package.json` controls which watches the package is
built for. The modern Pebble hardware is **emery** (Pebble Time 2),
**gabbro** (Pebble Round 2), and **flint** (Pebble 2 Duo); the original
platforms (aplite, basalt, chalk, diorite) are included by default for
backwards compatibility.

## Project layout

```
src/c/${project_name}.c        Package C source
include/${project_name}.h      Public C header consumers will include
src/js/index.js                Optional JS shipped to consumer's PKJS bundle
package.json                   Package metadata
wscript                        Build rules
```

## Documentation

Full SDK docs and the package guide: <https://developer.repebble.com>
