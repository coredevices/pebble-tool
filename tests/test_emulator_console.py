"""
:Description: pypkjs is told where the console of the firmware is, to talk to the firmware there.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pebble_tool.commands import base
from pebble_tool.sdk import emulator
from pebble_tool.util import logs


@pytest.fixture
def pypkjs_commands(monkeypatch, tmp_path):
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(pid=1234, poll=lambda: None)

    monkeypatch.setattr(emulator.subprocess, 'Popen', popen)
    monkeypatch.setattr(emulator.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(emulator.sdk_manager, 'path_for_sdk', lambda version: str(tmp_path))
    monkeypatch.setattr(emulator, 'get_sdk_persist_dir', lambda platform, version: str(tmp_path))
    monkeypatch.setattr(emulator, 'get_default_account', lambda: SimpleNamespace(is_logged_in=False))
    monkeypatch.setattr(emulator, 'pypkjs_takes_console', lambda: True)
    return commands


def option(command, name):
    return command[command.index(name) + 1] if name in command else None


def test_emulator_gives_pypkjs_its_console(pypkjs_commands) -> None:
    transport = object.__new__(emulator.ManagedEmulatorTransport)
    transport.platform, transport.version = 'emery', '4.9'
    transport.qemu_port, transport.qemu_serial_port, transport.pypkjs_port = 1001, 1002, 1003
    transport._spawn_pypkjs()
    command, = pypkjs_commands
    assert option(command, '--qemu') == 'localhost:1001'
    assert option(command, '--console') == 'localhost:1002'


def test_emulator_keeps_its_console_from_a_pypkjs_that_cannot_take_it(pypkjs_commands, monkeypatch) -> None:
    monkeypatch.setattr(emulator, 'pypkjs_takes_console', lambda: False)
    transport = object.__new__(emulator.ManagedEmulatorTransport)
    transport.platform, transport.version = 'emery', '4.9'
    transport.qemu_port, transport.qemu_serial_port, transport.pypkjs_port = 1001, 1002, 1003
    transport._spawn_pypkjs()
    command, = pypkjs_commands
    assert '--console' not in command


@pytest.mark.parametrize('installed, takes', [('3.0.4', False), ('3.1.0', True), ('3.2.1', True)])
def test_pypkjs_takes_the_console_from_3_1_0(monkeypatch, installed, takes) -> None:
    monkeypatch.setattr(emulator, 'version', lambda name: installed)
    assert emulator.pypkjs_takes_console() is takes


@pytest.mark.parametrize('console', ['localhost:1002', None])
def test_external_qemu_gives_pypkjs_the_console_it_was_given(pypkjs_commands, monkeypatch, console) -> None:
    monkeypatch.setattr(emulator.ExternalQemuTransport, 'get_existing_pypkjs_info',
                        classmethod(lambda cls, host, port: None))
    monkeypatch.setattr(emulator.ExternalQemuTransport, '_kill_existing_pypkjs', lambda self: None)
    monkeypatch.setattr(emulator.ExternalQemuTransport, '_write_state_file', lambda self: None)
    transport = emulator.ExternalQemuTransport('localhost', 1001, 'emery', '4.9', console=console)
    transport._spawn_pypkjs()
    command, = pypkjs_commands
    assert option(command, '--qemu') == 'localhost:1001'
    assert option(command, '--console') == console


def test_external_qemu_refuses_a_console_its_pypkjs_cannot_take(pypkjs_commands, monkeypatch) -> None:
    monkeypatch.setattr(emulator, 'pypkjs_takes_console', lambda: False)
    monkeypatch.setattr(emulator.ExternalQemuTransport, 'get_existing_pypkjs_info',
                        classmethod(lambda cls, host, port: None))
    monkeypatch.setattr(emulator.ExternalQemuTransport, '_kill_existing_pypkjs', lambda self: None)
    monkeypatch.setattr(emulator.ExternalQemuTransport, '_write_state_file', lambda self: None)
    transport = emulator.ExternalQemuTransport('localhost', 1001, 'emery', '4.9', console='localhost:1002')
    with pytest.raises(emulator.ToolError):
        transport._spawn_pypkjs()
    assert not pypkjs_commands


@pytest.mark.parametrize('argument, environment, expected', [
    ('localhost:1002', None, 'localhost:1002'),
    (None, 'localhost:1004', 'localhost:1004'),
    ('localhost:1002', 'localhost:1004', 'localhost:1002'),
    (None, None, None),
])
def test_qemu_console_comes_from_the_option_or_the_environment(monkeypatch, argument, environment, expected) -> None:
    created = []
    monkeypatch.setattr(base, 'ExternalQemuTransport',
                        lambda *args, **kwargs: created.append((args, kwargs)))
    if environment is None:
        monkeypatch.delenv('PEBBLE_QEMU_CONSOLE', raising=False)
    else:
        monkeypatch.setenv('PEBBLE_QEMU_CONSOLE', environment)
    args = SimpleNamespace(qemu='localhost:1001', pypkjs=True, platform='emery', sdk=None, qemu_console=argument)
    base.PebbleTransportQemu.get_transport(args)
    (positional, keywords), = created
    assert positional == ('localhost', 1001, 'emery', None)
    assert keywords == {'console': expected}


class SilentSocket:
    """A connection nothing comes from, as when another client has the port."""

    def __init__(self, printer, timeouts):
        self.printer, self.timeouts = printer, timeouts

    def recv(self, size):
        self.timeouts -= 1
        if self.timeouts == 0:
            self.printer.running = False
        raise logs.socket.timeout()

    def close(self):
        pass


@pytest.mark.parametrize('console, said', [(True, 1), (False, 0)])
def test_qemu_logs_say_when_pypkjs_may_have_the_console(capsys, console, said) -> None:
    printer = object.__new__(logs.QemuLogPrinter)
    printer.console_may_be_taken, printer.received = console, False
    printer.running, printer.waiting_for_sync, printer.input_buffer = True, True, bytearray()
    printer.socket = SilentSocket(printer, timeouts=3)
    printer._read_loop()
    assert capsys.readouterr().out.count('pypkjs is using the port') == said


def test_emulator_info_says_pypkjs_was_given_the_console(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(emulator, 'pypkjs_takes_console', lambda: True)
    monkeypatch.setattr(emulator, 'update_emulator_info', lambda platform, version, info: saved.append(info))
    transport = object.__new__(emulator.ManagedEmulatorTransport)
    transport.platform, transport.version = 'emery', '4.9'
    transport.qemu_pid = transport.qemu_port = transport.qemu_serial_port = 1
    transport.qemu_gdb_port = transport.qemu_monitor_port = transport.pypkjs_pid = transport.pypkjs_port = 1
    transport.vnc_enabled = transport.sunlight_enabled = False
    transport.websockify_pid = None
    transport._save_state()
    info, = saved
    assert info['pypkjs']['console'] is True
