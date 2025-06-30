# Pebble Tool

The command-line tool for the Pebble SDK.

## About

We've been working on porting the Pebble SDK from Python 2 to Python 3. This involves the following:
1. The command-line tool to build and install Pebble apps (this repository)
2. The SDK code in PebbleOS (https://github.com/coredevices/PebbleOS/tree/main/sdk). This isn't yet ready, so pebble-tool currently uses a patched version of the pre-built SDK to enable it to run in Python 3.
3. pypkjs (https://github.com/coredevices/pypkjs), which allows PebbleKitJS code to run in the QEMU emulator

## Changes

The project has been ported from Python 2 to 3. Dependencies have been updated as well.

The previous version of pebble-tool was designed to be downloaded as part of a large tar file that contained the toolchain, QEMU binary, and an executable for pebble-tool. Users had to configure a virtualenv, add the binaries to PATH, and decide where to install pebble-tool to their system. This version is instead installed through pip/uv.

Toolchain (arm-none-eabi) and QEMU binary installation are handled as part of `pebble sdk install` rather than bundled with the pebble-tool download.

## Installation

With `pipx`:
```shell
pipx install pypkjs
pipx install pebble-tool
```

With `uv`:
```shell
uv tool install pypkjs
uv tool install pebble-tool
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