
__author__ = 'katharine'

import atexit
import logging
import math
import os
import sys
import threading
import time
import requests

from pebble_tool.version import __version__
from pebble_tool.sdk import sdk_manager
from pebble_tool.util.config import config
from pebble_tool.util.versions import version_to_key

logger = logging.getLogger("pebble_tool.util.updates")


class UpdateChecker(threading.Thread):
    def __init__(self, component, current_version, callback):
        self.component = component
        self.current_version = current_version
        self.callback = callback
        super(UpdateChecker, self).__init__()
        self.daemon = True
        self.start()

    def run(self):
        last_check = config.get('update-checks', {}).get(self.component, {})
        if last_check.get('timestamp', 0) < time.time() - 86400:  # minus one day
            logger.debug("Haven't looked for updates lately; checking...")
            try:
                latest = sdk_manager.request("/v1/files/{}/latest?channel={}"
                                             .format(self.component, sdk_manager.get_channel()))
            except requests.RequestException as e:
                logger.info("Update check failed: %s", e)
                return
            if not 200 <= latest.status_code < 400:
                logger.info("Update check failed: %s (%s)", latest.status_code, latest.reason)
                return

            result = latest.json()
            with config.lock:
                config.setdefault('update-checks', {})[self.component] = {
                    'timestamp': time.time(),
                    'version': result['version'],
                    'release_notes': result.get('release_notes', None)
                }
            self._check_version(result['version'], result.get('release_notes', None))
        else:
            self._check_version(last_check['version'], last_check.get('release_notes', None))

    def _check_version(self, new_version, release_notes=None):
        if version_to_key(new_version) > version_to_key(self.current_version):
            logger.debug("Found an update: %s", new_version)
            atexit.register(self.callback, new_version, release_notes)


def _print(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _handle_sdk_update(version, release_notes=None):
    # We know the SDK was new when the version check occurred, but it is possible that it's
    # been installed since then. Therefore, check again.
    if version not in sdk_manager.list_local_sdk_versions():
        _print()
        _print("A new SDK, version {0}, is available! Run `pebble sdk install {0}` to get it.".format(version))
        if release_notes is not None:
            _print(release_notes)


def _handle_tool_update(version, release_notes=None):
    _print()
    _print("An updated pebble tool, version {}, is available.".format(version))
    if release_notes is not None:
        _print(release_notes)
    if 'PEBBLE_IS_HOMEBREW' in os.environ:
        flag = ' --devel' if sdk_manager.get_channel() == 'beta' else ''
        _print("Run `brew update && brew upgrade{} pebble-sdk` to get it.".format(flag))
    else:
        _print("Head to https://developer.getpebble.com/sdk/ to get it.")


def _get_platform():
    sys_platform = sys.platform.rstrip('2')  # "linux2" on python < 3.3...
    return sys_platform + str(int(round(math.log(sys.maxsize, 2)+1)))


def wait_for_update_checks(timeout):
    now = time.time()
    end = now + timeout
    for checker in _checkers:
        now = time.time()
        if now > end:
            break
        checker.join(end - time.time())

_checkers = []


def _do_updates():
    _checkers.append(UpdateChecker("pebble-tool-{}".format(_get_platform()), __version__, _handle_tool_update))
    # Only do the SDK update check if there is actually an SDK installed.
    if sdk_manager.get_current_sdk() is not None:
        try:
            latest_sdk = max(sdk_manager.list_local_sdk_versions(), key=version_to_key)
        except ValueError:
            latest_sdk = "0"
        _checkers.append(UpdateChecker("sdk-core", latest_sdk, _handle_sdk_update))

_do_updates()
