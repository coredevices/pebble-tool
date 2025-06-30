
__author__ = 'katharine'

import os

from pebble_tool.exceptions import MissingSDK
from pebble_tool.util import get_persist_dir
from .manager import SDKManager, pebble_platforms

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

def sdk_version():
    return sdk_manager.get_current_sdk()


def get_sdk_persist_dir(platform, for_sdk_version=None):
    dir = os.path.join(get_persist_dir(), for_sdk_version or sdk_version(), platform)
    if not os.path.exists(dir):
        os.makedirs(dir)
    return dir

def add_tools_to_path():
    if sdk_version():
        os.environ['PATH'] = "{}:{}".format(os.path.join(get_persist_dir(), "SDKs", sdk_version(), "toolchain", "arm-none-eabi", "bin"), os.environ['PATH'])