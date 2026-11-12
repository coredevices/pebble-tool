"""
:Description: how long `pebble` waits for the emulator's firmware, and what ends the wait.
"""
from __future__ import annotations

import socket
import threading

import pytest

from pebble_tool.exceptions import ToolError
from pebble_tool.sdk import emulator
from pebble_tool.sdk.emulator import ManagedEmulatorTransport

VERSION_RESPONSE = b'\xfe\xed\x00\x01\x00\x05\x00\x01\x00\x10\x01\xbe\xef'


@pytest.fixture
def wait(monkeypatch):
    monkeypatch.setattr(emulator, 'FIRMWARE_BOOT_TIMEOUT', 0.5)
    monkeypatch.setattr(emulator, 'FIRMWARE_MARKER_GRACE', 0.1)
    monkeypatch.setattr(emulator, 'FIRMWARE_PROBE_INTERVAL', 0.05)
    serial, console = socket.socketpair()
    server = socket.create_server(('localhost', 0))
    transport = ManagedEmulatorTransport.__new__(ManagedEmulatorTransport)
    transport.qemu_port = server.getsockname()[1]
    yield serial, console, server, lambda: transport._wait_for_boot(serial)
    for s in (serial, console, server):
        s.close()


def _answer(server, reply):
    def run():
        client, _ = server.accept()
        with client:
            client.recv(256)
            client.sendall(reply)
            client.recv(256)
    threading.Thread(target=run, daemon=True).start()


@pytest.mark.parametrize('marker', emulator.FIRMWARE_BOOT_MARKERS)
def test_returns_on_a_boot_marker(wait, marker) -> None:
    _, console, _, run = wait
    console.sendall(b'noise ' + marker)
    run()


def test_marker_split_across_reads(wait) -> None:
    _, console, _, run = wait
    console.sendall(b'x' * 250 + emulator.FIRMWARE_BOOT_MARKERS[2])
    run()


def test_answering_watch_without_a_marker_proceeds(wait) -> None:
    _, _, server, run = wait
    _answer(server, VERSION_RESPONSE)
    run()


def test_silent_watch_times_out(wait) -> None:
    _, _, _, run = wait
    with pytest.raises(ToolError, match="didn't boot"):
        run()


def test_other_protocol_traffic_is_not_an_answer(wait) -> None:
    _, _, server, run = wait
    _answer(server, b'\xfe\xed\x00\x01\x00\x05\x00\x01\x00\x11\x01\xbe\xef')
    with pytest.raises(ToolError, match="didn't boot"):
        run()


def test_exited_emulator_fails_right_away(wait) -> None:
    _, console, _, run = wait
    console.close()
    with pytest.raises(ToolError, match='exited'):
        run()
