"""Where an SDK keeps the headers of a platform."""

from __future__ import annotations

import os

from pebble_tool.sdk.manager import sdk_include_path

# What an SDK whose platforms share their headers gives pebble-tool.
SDK_PLATFORMS = '''
import os

pebble_platforms = {"emery": {"ABI": "armv7m"}, "slate": {"ABI": "armv7m"}, "aplite": {"ABI": "armv7m"}}


def sdk_component(sdk_root, platform_name, component):
    for directory in (platform_name, pebble_platforms[platform_name]["ABI"]):
        path = os.path.join(sdk_root, directory, component)
        if os.path.exists(path):
            return path
    return None
'''


def make_sdk(tmp_path, source, directories):
    tools = tmp_path / 'pebble' / 'common' / 'tools'
    tools.mkdir(parents=True)
    (tools / 'pebble_sdk_platform.py').write_text(source)
    for directory in directories:
        (tmp_path / 'pebble' / directory).mkdir(parents=True)
    return str(tmp_path)


def test_platform_with_its_own_headers(tmp_path) -> None:
    sdk = make_sdk(tmp_path, SDK_PLATFORMS, ['aplite/include', 'armv7m/include'])
    assert sdk_include_path(sdk, 'aplite') == os.path.join(sdk, 'pebble', 'aplite', 'include')


def test_platform_with_the_headers_of_its_abi(tmp_path) -> None:
    sdk = make_sdk(tmp_path, SDK_PLATFORMS, ['slate', 'armv7m/include'])
    assert sdk_include_path(sdk, 'slate') == os.path.join(sdk, 'pebble', 'armv7m', 'include')


def test_sdk_that_only_has_headers_per_platform(tmp_path) -> None:
    sdk = make_sdk(tmp_path, 'pebble_platforms = {"emery": {}}\n', ['emery/include'])
    assert sdk_include_path(sdk, 'emery') == os.path.join(sdk, 'pebble', 'emery', 'include')
    # A platform it does not have: the path it would be at, for the caller to find missing.
    assert sdk_include_path(sdk, 'slate') == os.path.join(sdk, 'pebble', 'slate', 'include')


def test_platform_the_sdk_does_not_know(tmp_path) -> None:
    sdk = make_sdk(tmp_path, SDK_PLATFORMS, ['armv7m/include'])
    assert sdk_include_path(sdk, 'granite') == os.path.join(sdk, 'pebble', 'granite', 'include')
