"""
:Description: `pebble plugins` lists and removes plugins over the emulator's pypkjs websocket.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import websocket

from pebble_tool.commands import plugins as plugins_module
from pebble_tool.commands.plugins import PluginsCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.sdk.emulator import ManagedEmulatorTransport

LISTED = [
    {'uuid': '00000000-0000-0000-0001-000000000002', 'name': 'Weather', 'kind': 'builtin',
     'sources': ['weather/location'], 'actions': []},
    {'uuid': '2c4b7f10-9a3d-4e6b-8f21-5d0c7e9a1b34', 'name': 'Stocks', 'kind': 'plugin',
     'sources': ['finance/stock'], 'actions': []},
]


class FakeSocket:
    """pypkjs, as far as one command goes: a broadcast log line first, then the reply."""

    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def send_binary(self, data):
        self.sent.append(data)
        self.queue = [bytes([0x02]) + b'some log line', self.replies[data[1]](data[2:].decode())]

    def recv(self):
        if not self.queue:
            raise websocket.WebSocketTimeoutException()
        return self.queue.pop(0)

    def close(self):
        pass


def ok(result):
    return bytes([0x0d, 0x00]) + json.dumps(result).encode()


@pytest.fixture
def command(monkeypatch):
    sockets = []

    def connect(replies):
        def create_connection(url, timeout):
            assert url == 'ws://localhost:9000/'
            sockets.append(FakeSocket(replies))
            return sockets[-1]
        monkeypatch.setattr(plugins_module.websocket, 'create_connection', create_connection)
        cmd = PluginsCommand()
        transport = ManagedEmulatorTransport.__new__(ManagedEmulatorTransport)
        transport.pypkjs_port = 9000
        cmd.pebble = SimpleNamespace(transport=transport)
        return cmd, sockets
    return connect


def test_list_puts_installed_plugins_first(command, capsys) -> None:
    cmd, _ = command({0x02: lambda body: ok(LISTED)})
    plugins_module.print_plugins(cmd._request(plugins_module.LIST))
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == 'Stocks  2c4b7f10-9a3d-4e6b-8f21-5d0c7e9a1b34  (plugin)'
    assert lines[2] == 'Weather  00000000-0000-0000-0001-000000000002  (built in)'


def test_remove_by_name(command) -> None:
    removed = []

    def remove(body):
        removed.append(body)
        return ok({'removed': 'Stocks'})

    cmd, _ = command({0x02: lambda body: ok(LISTED), 0x03: remove})
    assert cmd._request(plugins_module.REMOVE, cmd._resolve('stocks')) == {'removed': 'Stocks'}
    assert removed == ['2c4b7f10-9a3d-4e6b-8f21-5d0c7e9a1b34']


def test_refusal_is_a_tool_error(command) -> None:
    cmd, _ = command({0x03: lambda body: bytes([0x0d, 0x01]) + b'Weather is built into the phone app'})
    with pytest.raises(ToolError, match='built into the phone app'):
        cmd._request(plugins_module.REMOVE, 'Weather')


def test_old_pypkjs_that_never_answers(command) -> None:
    cmd, _ = command({0x02: lambda body: bytes([0x02]) + b'another log line'})
    with pytest.raises(ToolError, match='too old'):
        cmd._request(plugins_module.LIST)
