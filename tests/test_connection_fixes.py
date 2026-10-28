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
