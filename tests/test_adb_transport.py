"""
Tests for the adb transport helpers.
"""
from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from pebble_tool.exceptions import ToolError
from pebble_tool.util import adb


def _adb_returning(output):
    return mock.patch.object(adb, '_adb', return_value=output)


class TestStartDevConnection:
    def test_returns_port(self):
        with _adb_returning('Broadcasting: Intent { act=... }\n'
                            'Broadcast completed: result=0, data="9000"\n'):
            assert adb.start_dev_connection([]) == 9000

    def test_no_data_means_nothing_received_it(self):
        # An unhandled broadcast also reports result=0, so missing data is how we spot an app
        # without the receiver.
        with _adb_returning('Broadcast completed: result=0\n'):
            with pytest.raises(ToolError, match="didn't respond"):
                adb.start_dev_connection([])

    def test_failure_surfaces_app_message(self):
        with _adb_returning('Broadcast completed: result=1, data="no watch connected"\n'):
            with pytest.raises(ToolError, match="no watch connected"):
                adb.start_dev_connection([])

    def test_failure_without_message(self):
        with _adb_returning('Broadcast completed: result=2\n'):
            with pytest.raises(ToolError, match="error 2"):
                adb.start_dev_connection([])

    def test_unparseable_output(self):
        with _adb_returning('something else entirely\n'):
            with pytest.raises(ToolError, match="Couldn't understand"):
                adb.start_dev_connection([])

    def test_non_numeric_port(self):
        with _adb_returning('Broadcast completed: result=0, data="banana"\n'):
            with pytest.raises(ToolError, match="invalid port"):
                adb.start_dev_connection([])

    def test_passes_device_flag_through(self):
        with mock.patch.object(adb, '_adb',
                               return_value='Broadcast completed: result=0, data="9000"') as m:
            adb.start_dev_connection(['-s', 'ABC123'])
        assert m.call_args[0][0] == ['-s', 'ABC123']
        assert 'am' in m.call_args[0]


class TestForward:
    def test_returns_local_port(self):
        with _adb_returning('41234\n'):
            with mock.patch.object(adb, 'atexit'):
                assert adb.forward([], 9000) == 41234

    def test_registers_cleanup(self):
        with _adb_returning('41234\n'):
            with mock.patch.object(adb, 'atexit') as at:
                adb.forward([], 9000)
        at.register.assert_called_once_with(adb._remove_forward, [], 41234)

    def test_unparseable_output(self):
        with _adb_returning('error: no devices/emulators found\n'):
            with pytest.raises(ToolError, match="which port"):
                adb.forward([], 9000)


class TestAdbInvocation:
    def test_missing_binary(self):
        with mock.patch('subprocess.check_output', side_effect=OSError):
            with pytest.raises(ToolError, match="on your PATH"):
                adb._adb([], 'devices')

    def test_command_failure_includes_output(self):
        error = subprocess.CalledProcessError(1, 'adb', output=b'device unauthorized')
        with mock.patch('subprocess.check_output', side_effect=error):
            with pytest.raises(ToolError, match="device unauthorized"):
                adb._adb([], 'devices')
