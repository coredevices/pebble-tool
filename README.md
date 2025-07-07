# Pebble Tool

The command-line tool for the Pebble SDK.

## About

The Pebble SDK now runs in Python 3. This includes:
1. The command-line tool to build and install apps and watchfaces (this repository).
2. The SDK code in PebbleOS (https://github.com/coredevices/PebbleOS/tree/main/sdk). This isn't fully working yet, so pebble-tool currently uses a patched version of the existing SDK core (version 4.3) that has been modified for Python 3.
3. pypkjs (https://github.com/coredevices/pypkjs), which allows PebbleKitJS code to run in the QEMU emulator.

Previously, the Pebble SDK was installed by downloading a tar file containing pebble-tool, the toolchain, and executables for PebbleOS QEMU and pebble-tool. Users had to decide where to extract the file, add the binaries to their PATH, and configure a virtualenv.

Now, pebble-tool is a standalone command-line tool that can be installed through pip/uv. The toolchain (arm-none-eabi) and QEMU binary are no longer bundled, but instead installed when `pebble sdk install` is run.

## Installation

```shell
uv tool install pebble-tool
```

Install dependencies (MacOS):
```shell
brew update
brew install glib
brew install pixman
```

Install dependencies (Linux):
```shell
sudo apt-get install libsdl1.2debian libfdt1
```

## Usage

Install the latest SDK:
```shell
pebble sdk install latest
```

Create a new project (for example, called myproject):
```shell
pebble new-project myproject
```

`cd` into the folder you just created, then compile it:
```shell
pebble build
```

Install the app/watchface on an emulator for the Pebble Time:
```shell
pebble install --emulator basalt
```

Install the app/watchface on your phone (replace IP with your phone's IP shown in the Pebble app):
```shell
pebble install --phone IP
```

## Troubleshooting

If you run into issues, try uninstalling and re-installing. You can remove the latest SDK with
```shell
pebble sdk uninstall 4.4
```

You can also delete pebble-tool's entire data directory, located at ~/.pebble-sdk on Linux and ~/Library/Application Support/Pebble SDK on Mac.