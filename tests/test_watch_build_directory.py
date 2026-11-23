"""Which directory under build/ holds the binary a watch runs."""

from __future__ import annotations

import json
import zipfile
from types import SimpleNamespace

import pytest
from libpebble2.util.bundle import PebbleBundle

from pebble_tool.util import project_pbw, watch_build_directory

platform_module = pytest.importorskip('libpebble2.util.platform')


def _watch(platform):
    return SimpleNamespace(watch_platform=platform.name, watch_platform_info=platform,
                           watch_info=SimpleNamespace(running=SimpleNamespace(hardware_platform=0)))


def _project(tmp_path, monkeypatch, directories):
    project = tmp_path / 'app'
    (project / 'build').mkdir(parents=True)
    monkeypatch.chdir(project)
    with zipfile.ZipFile(project_pbw(), 'w') as z:
        z.writestr('appinfo.json', '{}')
        for name, platform in directories.items():
            manifest = {'application': {'name': 'pebble-app.bin'}, 'platform': platform}
            z.writestr(name + '/manifest.json', json.dumps(manifest))
            z.writestr(name + '/pebble-app.bin', b'\0' * 200)


def _fixed(name, width, height):
    return {'version': 1, 'name': name, 'abi': 'armv7m', 'features': [],
            'display': {'width': width, 'height': height, 'shape': 'rect', 'color': False},
            'limits': {'app_memory': 65536}}


RECT_BW = {'version': 1, 'name': 'rect-bw', 'abi': 'armv7m', 'flexible': True, 'requires': [],
           'display': {'shape': 'rect', 'color': False}, 'limits': {'app_memory': 65536}}


@pytest.mark.skipif(not hasattr(PebbleBundle, 'get_binary_directory'),
                    reason='needs a libpebble2 that says which directory it installs from')
def test_flexible_binary_of_a_watch_without_its_own(tmp_path, monkeypatch) -> None:
    _project(tmp_path, monkeypatch, {'flint': _fixed('flint', 144, 168), 'rect-bw': RECT_BW})
    known = platform_module.KNOWN_PLATFORMS
    assert watch_build_directory(_watch(known['flint'])) == 'flint'
    new = platform_module.Platform('new', 400, 240)
    assert watch_build_directory(_watch(new)) == 'rect-bw'


def test_without_a_pbw_it_is_the_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    watch = SimpleNamespace(watch_platform='emery', watch_platform_info=None,
                            watch_info=SimpleNamespace(running=SimpleNamespace(hardware_platform=0)))
    assert watch_build_directory(watch) == 'emery'
    assert watch_build_directory(watch, 'gabbro') == 'gabbro'
