"""
:Description: `pebble emu-set-location` tells the emulator's pypkjs where the phone is.
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from pebble_tool.commands.emucontrol import EmuSetLocationCommand
from pebble_tool.exceptions import ToolError


def run(monkeypatch, argv):
    sent = []

    def request(transport, opcode, payload=b''):
        sent.append((opcode, json.loads(payload)))
        return b''

    monkeypatch.setattr('pebble_tool.commands.emucontrol.phonesim.request', request)
    monkeypatch.setattr('pebble_tool.commands.base.PebbleCommand.__call__', lambda self, args: None)
    parser = argparse.ArgumentParser()
    EmuSetLocationCommand.add_parser(parser.add_subparsers())
    args = parser.parse_args(['emu-set-location'] + argv)
    cmd = EmuSetLocationCommand()
    cmd.pebble = SimpleNamespace(transport=None)
    cmd(args)
    return sent


def test_sets_a_location_west_of_greenwich(monkeypatch) -> None:
    assert run(monkeypatch, ['40.7', '-74.0']) == [(0x0e, {'latitude': 40.7, 'longitude': -74.0})]


def test_clear(monkeypatch) -> None:
    assert run(monkeypatch, ['--clear']) == [(0x0e, None)]


def test_needs_both_coordinates(monkeypatch) -> None:
    with pytest.raises(ToolError):
        run(monkeypatch, ['40.7'])
