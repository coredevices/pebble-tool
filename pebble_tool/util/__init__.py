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


def project_pbw():
    """The pbw the project in cwd last built."""
    name = os.path.basename(os.getcwd())
    debug_pbw = os.path.join('build', '{}_debug.pbw'.format(name))
    if is_debug_build() and os.path.exists(debug_pbw):
        return debug_pbw
    return os.path.join('build', '{}.pbw'.format(name))


def watch_build_directory(pebble, platform=None):
    """The directory under build/ of the binary the watch runs out of the project in cwd. It is that of
    the watch's platform, unless the pbw has a binary that suits the watch better: a flexible one, or,
    for a watch the project does not build for, the one that fits it best."""
    from libpebble2.util.bundle import PebbleBundle
    platform = platform or pebble.watch_platform
    try:
        hardware = pebble.watch_info.running.hardware_platform
        for any_fit in (False, True):
            bundle = PebbleBundle(project_pbw(), hardware=hardware, platform=pebble.watch_platform_info,
                                  any_fit=any_fit)
            try:
                directory = bundle.get_binary_directory()
            finally:
                bundle.close()
            if directory:
                return directory
    except Exception:
        pass
    return platform


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
