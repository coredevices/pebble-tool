"""
:Description: connecting to watches and emulators, and what pebble-tool says when that goes wrong.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from types import SimpleNamespace

import pytest

from pebble_tool.commands import base
from pebble_tool.commands.install import ToolAppInstaller
from pebble_tool.commands.sdk.manage import SDKManager
from pebble_tool.exceptions import ToolError
from pebble_tool.util import browser as browser_module
from pebble_tool.util.browser import BrowserController

EMERY_EMULATOR = 0xf5


def test_command_without_subcommand_prints_its_help(capsys) -> None:
    parser = argparse.ArgumentParser()
    SDKManager.add_parser(parser.add_subparsers())
    args = parser.parse_args(['sdk'])
    args.func(args)
    out = capsys.readouterr().out
    assert out.startswith('usage:') and 'install' in out


class Connection:
    def __init__(self, transport):
        self.transport = transport
        self.firmware_version = SimpleNamespace(major=4)
        self.sent = []

    def send_packet(self, packet):
        self.sent.append(packet)


@pytest.mark.parametrize('spawned', [True, False])
def test_emulator_gets_the_host_time_only_when_launched(spawned) -> None:
    connection = Connection(SimpleNamespace(spawned_qemu=spawned))
    base.PebbleTransportEmulator.post_connect(connection)
    assert len(connection.sent) == (1 if spawned else 0)


@pytest.mark.parametrize('spawned', [True, False])
def test_qemu_gets_the_host_time_only_when_pypkjs_is_launched(spawned) -> None:
    connection = Connection(SimpleNamespace(spawned_pypkjs=spawned))
    base.PebbleTransportQemu.post_connect(connection)
    assert len(connection.sent) == (1 if spawned else 0)


def test_qemu_without_pypkjs_keeps_its_time() -> None:
    connection = Connection(SimpleNamespace())
    base.PebbleTransportQemu.post_connect(connection)
    assert connection.sent == []


def test_silent_serial_port_explains_bluetooth_classic() -> None:
    message = base.PebbleTransportSerial.no_answer_message(SimpleNamespace(serial='/dev/cu.PebbleTimeA17A'))
    assert '/dev/cu.PebbleTimeA17A' in message
    assert 'Bluetooth Classic' in message and '--phone' in message


def make_pbw(tmp_path, platforms, name='app.pbw'):
    path = tmp_path / name
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('appinfo.json', json.dumps({'uuid': '6b1c3a52-0d4e-4f5a-9c33-2f0e7a1d8b40'}))
        for platform in platforms:
            z.writestr('{}/manifest.json'.format(platform), json.dumps({'manifestVersion': 2}))
    return str(path)


EMERY_WATCH = SimpleNamespace(watch_info=SimpleNamespace(running=SimpleNamespace(hardware_platform=EMERY_EMULATOR)),
                              watch_platform='emery')


def test_install_refuses_a_pbw_without_a_build_for_the_watch(tmp_path) -> None:
    with pytest.raises(ToolError) as e:
        ToolAppInstaller._check_platform(EMERY_WATCH, make_pbw(tmp_path, ['aplite', 'chalk']))
    assert str(e.value) == ('This pbw has no emery build (it has aplite, chalk). Add "emery" to targetPlatforms '
                            'in package.json and rebuild.')


def test_install_takes_a_pbw_with_a_build_for_the_watch(tmp_path) -> None:
    ToolAppInstaller._check_platform(EMERY_WATCH, make_pbw(tmp_path, ['emery', 'gabbro']))


def test_install_leaves_a_plugin_only_pbw_to_the_phone(tmp_path) -> None:
    ToolAppInstaller._check_platform(EMERY_WATCH, make_pbw(tmp_path, []))
