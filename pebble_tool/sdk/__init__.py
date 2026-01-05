
__author__ = 'katharine'

import os

from pebble_tool.exceptions import MissingSDK
from pebble_tool.util import get_persist_dir
from .manager import SDKManager, get_pebble_platforms

SDK_VERSION = '3'


def sdk_path():
    path = sdk_manager.current_path
    if path is None:
        print("No SDK installed; installing the latest one...")
        sdk_manager.install_remote_sdk("latest")
        print("Installed SDK {}.".format(sdk_manager.get_current_sdk()))
        path = sdk_manager.current_path
    if not os.path.exists(os.path.join(path, 'pebble', 'waf')):
        raise MissingSDK("SDK unavailable; can't run this command.")
    return path


sdk_manager = SDKManager()


def has_rocky_tools(version=None):
    """Check if the SDK has Rocky.js tools available.

    Rocky.js requires js_tooling.wasm which is only present in certain SDK versions.
    """
    if version is None:
        version = sdk_manager.get_current_sdk()
    if version is None:
        return False
    sdk_dir = os.path.join(get_persist_dir(), "SDKs", version, "sdk-core")
    js_tooling_path = os.path.join(sdk_dir, "pebble", "common", "tools", "js_tooling.wasm")
    return os.path.exists(js_tooling_path)


def sdk_version():
    return sdk_manager.get_current_sdk()


def get_sdk_persist_dir(platform, for_sdk_version=None):
    dir = os.path.join(get_persist_dir(), for_sdk_version or sdk_version(), platform)
    if not os.path.exists(dir):
        os.makedirs(dir)
    return dir

def has_moddable_tools(version=None):
    """Check if the SDK has Moddable tools available."""
    version = version or sdk_version()
    if not version:
        return False
    moddable_tools_path = os.path.join(get_persist_dir(), "SDKs", version, "toolchain", "moddable-tools")
    mcrun_path = os.path.join(moddable_tools_path, "mcrun")
    return os.path.exists(mcrun_path)


def add_tools_to_path():
    if sdk_version():
        os.environ['PATH'] = "{}:{}".format(os.path.join(get_persist_dir(), "SDKs", sdk_version(), "toolchain", "arm-none-eabi", "bin"), os.environ['PATH'])

        # Create symlink from /tmp/pebble-sdk to persist directory
        tmp_link = "/var/tmp/pebble-sdk"
        target = get_persist_dir()
        if not (os.path.islink(tmp_link) and os.readlink(tmp_link) == target):
            if os.path.lexists(tmp_link):
                os.unlink(tmp_link)
            os.symlink(target, tmp_link)

        os.environ['PATH'] = "{}:{}".format(os.path.join(tmp_link, "SDKs", sdk_version(), "toolchain", "moddable-tools"), os.environ['PATH'])
        os.environ['MODDABLE'] = os.path.join(tmp_link, "SDKs", sdk_version(), "toolchain", "moddable")
        extra_path = os.environ.get('PEBBLE_EXTRA_PATH')
        if extra_path:
            os.environ['PATH'] = "{}:{}".format(extra_path, os.environ['PATH'])
