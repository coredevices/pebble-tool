__author__ = 'katharine'

import os
import os.path
import platform

# Marker written by `pebble build --debug`; tells `pebble install`/`pebble logs`
# that the current project's last build was a debug build.
DEBUG_BUILD_MARKER = os.path.join('build', '.pebble_debug')


def is_debug_build():
    """True if the project in cwd was last built with `pebble build --debug`."""
    return os.path.exists(DEBUG_BUILD_MARKER)


def get_persist_dir():
    if platform.system() == 'Darwin':
        dir = os.path.expanduser("~/Library/Application Support/Pebble SDK")
    else:
        legacy_dir = os.path.expanduser("~/.pebble-sdk")
        if os.path.exists(legacy_dir):
            dir = legacy_dir
        else:
            # Follow the XDG Base Directory specification for new installations.
            data_home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser("~/.local/share")
            dir = os.path.join(data_home, "pebble-sdk")
    if not os.path.exists(dir):
        os.makedirs(dir)
    return dir
