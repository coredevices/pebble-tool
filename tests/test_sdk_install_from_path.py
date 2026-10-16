"""
:Description: `pebble sdk install <file>` takes the toolchain from a tarball next to the SDK file.
"""
from __future__ import annotations

import io
import json
import os
import tarfile

import pytest

from pebble_tool.sdk import manager as manager_module
from pebble_tool.sdk.manager import SDKManager


def _write_tar(path, files):
    with tarfile.open(path, 'w:gz') as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def _sdk_core(path):
    manifest = {'requirements': [], 'version': 'test-sdk', 'type': 'sdk-core', 'channel': ''}
    _write_tar(path, {
        'sdk-core/manifest.json': json.dumps(manifest).encode(),
        'sdk-core/requirements.txt': b'',
    })


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(manager_module.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(manager_module.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(manager_module.subprocess, 'check_call', lambda *args, **kwargs: None)

    def no_network(*args, **kwargs):
        raise AssertionError("tried to download the toolchain")

    monkeypatch.setattr(manager_module.requests, 'head', no_network)
    monkeypatch.setattr(manager_module.requests, 'get', no_network)


@pytest.mark.parametrize('toolchain_platform', ['mac-arm64', 'mac'])
def test_toolchain_next_to_the_sdk_file(tmp_path, offline, toolchain_platform) -> None:
    dist = tmp_path / 'dist'
    dist.mkdir()
    _sdk_core(dist / 'sdk-core.tar.gz')
    _write_tar(dist / f'toolchain-{toolchain_platform}.tar.gz', {
        f'toolchain-{toolchain_platform}/arm-none-eabi/bin/arm-none-eabi-gcc': b'gcc',
    })
    sdks = tmp_path / 'SDKs'

    SDKManager(sdk_dir=str(sdks)).install_from_path(str(dist / 'sdk-core.tar.gz'))

    gcc = sdks / 'test-sdk' / 'toolchain' / 'arm-none-eabi' / 'bin' / 'arm-none-eabi-gcc'
    assert gcc.read_bytes() == b'gcc'


def test_arch_specific_toolchain_wins(tmp_path) -> None:
    for name in ('toolchain-mac.tar.gz', 'toolchain-mac-arm64.tar.gz'):
        (tmp_path / name).write_bytes(b'')
    assert SDKManager._local_toolchain(str(tmp_path), 'mac', 'arm64') == (
        os.path.join(str(tmp_path), 'toolchain-mac-arm64.tar.gz'), 'mac-arm64')
    assert SDKManager._local_toolchain(str(tmp_path), 'linux', 'x86_64') is None
    assert SDKManager._local_toolchain(None, 'mac', 'arm64') is None
