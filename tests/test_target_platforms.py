"""targetPlatforms entries that ask for capabilities instead of naming a platform."""

from __future__ import annotations

from pebble_tool.sdk.manager import resolve_target_platforms

# What an SDK's pebble_sdk_platform.py gives pebble-tool: the platforms, and whether one is asked for.
SDK_PLATFORMS = '''
pebble_platforms = {
    "chalk": {"TAGS": ["chalk", "color", "round"]},
    "emery": {"TAGS": ["emery", "color", "rect", "mic"]},
    "flint": {"TAGS": ["flint", "bw", "rect", "mic"]},
    "gabbro": {"TAGS": ["gabbro", "color", "round", "mic"]},
}


def platform_matches(platform_name, selectors):
    tags = set(pebble_platforms[platform_name]["TAGS"])
    return any(set(selector.split("~")) <= tags for selector in selectors)
'''


def make_sdk(tmp_path, source, platforms=('emery', 'flint', 'gabbro')):
    tools = tmp_path / 'pebble' / 'common' / 'tools'
    tools.mkdir(parents=True)
    (tools / 'pebble_sdk_platform.py').write_text(source)
    for platform in platforms:
        (tmp_path / 'pebble' / platform).mkdir()
    return str(tmp_path)


def test_names_stay_as_written(tmp_path) -> None:
    sdk = make_sdk(tmp_path, SDK_PLATFORMS)
    assert resolve_target_platforms(['flint', 'basalt', 'emery'], sdk) == ['flint', 'basalt', 'emery']


def test_capabilities_become_the_platforms_the_sdk_has(tmp_path) -> None:
    sdk = make_sdk(tmp_path, SDK_PLATFORMS)
    # chalk is round too, but this SDK cannot build for it.
    assert resolve_target_platforms(['round'], sdk) == ['gabbro']
    assert resolve_target_platforms(['emery', 'rect~mic', 'round'], sdk) == ['emery', 'flint', 'gabbro']


def test_an_sdk_that_only_takes_names(tmp_path) -> None:
    sdk = make_sdk(tmp_path, 'pebble_platforms = {"emery": {}}\n')
    assert resolve_target_platforms(['emery', 'round'], sdk) == ['emery', 'round']
    assert resolve_target_platforms(['emery'], str(tmp_path / 'none')) == ['emery']
