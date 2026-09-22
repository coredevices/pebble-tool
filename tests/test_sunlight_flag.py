import argparse

from pebble_tool.commands.base import PebbleTransportEmulator
from pebble_tool.sdk.emulator import ManagedEmulatorTransport


def _parse_emulator_args(argv):
    parser = argparse.ArgumentParser()
    PebbleTransportEmulator.add_argument_handler(parser, parser)
    return parser.parse_args(argv)


def test_sunlight_defaults_to_off():
    args = _parse_emulator_args(['--emulator', 'basalt'])
    assert args.sunlight is False
    assert PebbleTransportEmulator._connect_args(args)[-1] is False


def test_sunlight_flag_propagates_to_connect_args():
    args = _parse_emulator_args(['--emulator', 'basalt', '--sunlight'])
    assert args.sunlight is True
    platform, version, vnc_enabled, sunlight_enabled = PebbleTransportEmulator._connect_args(args)
    assert platform == 'basalt'
    assert vnc_enabled is False
    assert sunlight_enabled is True


def test_sunlight_device_globals_new_sdk():
    assert ManagedEmulatorTransport._sunlight_device_globals('basalt', True) == [
        '-global', 'pebble-snowy-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('chalk', True) == [
        '-global', 'pebble-snowy-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('emery', True) == [
        '-global', 'pebble-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('gabbro', True) == [
        '-global', 'pebble-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('flint', True) == [
        '-global', 'pebble-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('aplite', True) == []
    assert ManagedEmulatorTransport._sunlight_device_globals('diorite', True) == []


def test_sunlight_device_globals_legacy_sdk():
    assert ManagedEmulatorTransport._sunlight_device_globals('emery', False) == [
        '-global', 'pebble-snowy-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('gabbro', False) == [
        '-global', 'pebble-snowy-display.sunlight=true']
    assert ManagedEmulatorTransport._sunlight_device_globals('flint', False) == []


def test_transport_stores_sunlight_flag():
    transport = ManagedEmulatorTransport('basalt', 'test-version', False, True)
    assert transport.sunlight_enabled is True
    transport = ManagedEmulatorTransport('basalt', 'test-version')
    assert transport.sunlight_enabled is False
