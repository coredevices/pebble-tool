---
alwaysApply: true
---

## Project Overview

This is a Pebble smartwatch application written in C using the Pebble SDK.

## Supported Platforms

The app targets multiple Pebble watch models:
- aplite (Pebble classic)
- basalt (Pebble Time)
- chalk (Pebble Time Round)
- diorite (Pebble 2)
- emery (Pebble Time 2)
- flint (Pebble 2 Duo)

## Commands

```bash
# Build the app for all platforms
pebble build

# Clean build artifacts
pebble clean

# Install the app on specific emulator
pebble install --emulator basalt

# Screenshot the running emulator
pebble screenshot --scale 6 --no-open screenshot.png
```

If you need more information on the `pebble` command or a sub-command, append `--help`.

### Headless Environments

If you're running in an environment without a window server (e.g., headless Linux, Docker, CI), you must add `--vnc` to **all commands that interact with the emulator**. This includes app installs, screenshots, button presses, and any `emu-*` commands:

```bash
pebble install --emulator basalt --vnc
pebble screenshot --vnc --scale 6 --no-open screenshot.png
pebble emu-button --emulator basalt --vnc click select
```

The `--vnc` flag enables a VNC-based display backend that doesn't require X11.

## Project Structure

```
src/c/           - C source files for the watchapp
src/pkjs/        - PebbleKitJS files (currently empty)
worker_src/c/    - Worker source files (optional, not present)
resources/       - Images, fonts, and other resources (not present)
```
## Configuration

By default, this project is initialized as a watchface. To make it an app, replace "watchface": true with "watchface": false in package.json.

## Architecture

The application follows the standard Pebble app architecture:

1. **Main Entry Point**: `src/c` - The `main()` function initializes the app and starts the event loop
2. **Window Management**: Single window app with text layer for displaying button press feedback
3. **Event Handling**: Button click handlers registered via `prv_click_config_provider` for UP, DOWN, and SELECT buttons

## SDK Documentation

The full Pebble SDK documentation is available at https://developer.repebble.com.

Main Categories:
- Tutorials - Step-by-step learning (C watchface tutorial in 5 parts, advanced topics)
- Developer Guides - Comprehensive reference organized by topic

Key Sections:
- App Resources - Images, fonts, vector graphics, 256 resource limit
- User Interfaces - Layer hierarchy, TextLayer, MenuLayer, round vs rectangular displays
- Events & Services - Buttons, accelerometer, compass, health data, background workers
- Communication - Bluetooth AppMessage, PebbleKit JS/Android/iOS integration
- Graphics & Animations - Drawing APIs, property animations, vector graphics
- Debugging - App logs, GDB, common errors and solutions
- Best Practices - Multi-platform support, battery conservation, modular architecture
- Design & Interaction - Glance-first design, one-click actions, platform guidelines
- App Store Publishing - Submission requirements, assets, analytics

Key Entry Points:
- https://developer.repebble.com/tutorials/watchface-tutorial/part1 - C development start
- https://developer.repebble.com/guides/events-and-services/buttons - Button handling
- https://developer.repebble.com/guides/user-interfaces/layers - UI foundations

## Development Best Practices

- Whenever making changes, run `pebble screenshot --scale 6` and view the screenshot to make sure it's what the user requested. If not, make more changes until it does what it's supposed to.

## Emulator Button Control

Control emulator buttons programmatically with `pebble emu-button`:

```bash
# Click a button (press and release)
pebble emu-button click select

# Long press (e.g., 2 seconds to exit app)
pebble emu-button click back --duration 2000

# Repeat clicks (e.g., scroll down 5 times)
pebble emu-button click down --repeat 5

# Faster repeat interval
pebble emu-button click up --repeat 3 --interval 100
```

**Actions:**
- `click` - Press then release (use `--duration` for long press)
- `push` - Hold button down (use `release` to let go)
- `release` - Release all buttons

**Buttons:** `back`, `up`, `select`, `down`

**Best Practices:**
- Use `click` for normal navigation and selection
- Use `click --duration 2000` for long press (e.g., back button to exit)
- Use `--repeat` to scroll through menus instead of multiple commands
- After making UI changes, take a screenshot to verify the result

## AI Interaction Guidelines

- When given an image of a watchface to replicate, describe the target watchface in precise detail. Note every visual element present, as well as size, alignment, font weight, spacing, and location.

## AI Code Review Guidelines

- Once you think you've fulfilled the user's request, ask yourself if you see any issues with the current screenshot, and if there are any differences between the screenshot and the reference image or the user's description. If so, fix them.