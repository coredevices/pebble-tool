import atexit
import logging
import sys
import threading
import time

import requests
from colorama import Fore, Style

from pebble_tool.util.config import config
from pebble_tool.util.versions import version_to_key

logger = logging.getLogger("pebble_tool.util.update_notify")

_CACHE_TTL = 6 * 3600  # 6 hours
_checkers = []


class _UpdateThread(threading.Thread):
    def __init__(self, component, current_version, check_fn, notify_fn):
        super().__init__(daemon=True)
        self.component = component
        self.current_version = current_version
        self.check_fn = check_fn
        self.notify_fn = notify_fn
        self.start()

    def run(self):
        try:
            cached = config.get('update-notify', {}).get(self.component, {})
            if cached.get('timestamp', 0) > time.time() - _CACHE_TTL:
                latest = cached.get('version')
            else:
                latest = self.check_fn()
                if latest:
                    with config.lock:
                        config.setdefault('update-notify', {})[self.component] = {
                            'timestamp': time.time(),
                            'version': latest,
                        }
            if latest and version_to_key(latest) > version_to_key(self.current_version):
                atexit.register(self.notify_fn, latest, self.current_version)
        except Exception as e:
            logger.debug("Update check failed for %s: %s", self.component, e)


def _check_tool():
    resp = requests.get("https://pypi.org/pypi/pebble-tool/json", timeout=5)
    if resp.status_code == 200:
        return resp.json()["info"]["version"]
    return None


def _check_sdk():
    from pebble_tool.sdk import sdk_manager
    sdks = sdk_manager.list_remote_sdks()
    if not sdks:
        return None
    versions = [s['version'] for s in sdks if 'version' in s]
    if not versions:
        return None
    return max(versions, key=version_to_key)


def _notify_tool(new_version, current_version):
    print(file=sys.stderr)
    print("{}{}A new pebble-tool is available: v{} (current: v{}){}".format(
        Style.BRIGHT, Fore.YELLOW, new_version, current_version, Style.RESET_ALL), file=sys.stderr)
    print("{}  Update with: uv tool upgrade pebble-tool{}".format(
        Fore.YELLOW, Style.RESET_ALL), file=sys.stderr)


def _notify_sdk(new_version, current_version):
    print(file=sys.stderr)
    print("{}{}A new SDK is available: v{} (current: v{}){}".format(
        Style.BRIGHT, Fore.YELLOW, new_version, current_version, Style.RESET_ALL), file=sys.stderr)
    print("{}  Update with: pebble sdk install latest{}".format(
        Fore.YELLOW, Style.RESET_ALL), file=sys.stderr)


def check_for_updates():
    from importlib.metadata import version as pkg_version
    from pebble_tool.sdk import sdk_manager

    tool_version = pkg_version('pebble-tool')
    _checkers.append(_UpdateThread("pebble-tool", tool_version, _check_tool, _notify_tool))

    current_sdk = sdk_manager.get_current_sdk()
    if current_sdk is not None:
        _checkers.append(_UpdateThread("sdk-core", current_sdk, _check_sdk, _notify_sdk))


def wait_for_update_notify(timeout):
    end = time.time() + timeout
    for t in _checkers:
        remaining = end - time.time()
        if remaining <= 0:
            break
        t.join(remaining)
