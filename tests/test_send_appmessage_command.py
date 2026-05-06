"""
Tests for the SendAppMessageCommand.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from pebble_tool.commands.appmessage import SendAppMessageCommand
from pebble_tool.exceptions import ToolError
from libpebble2.services.appmessage import Int32, Uint32, CString, ByteArray


class TestParseKeyValue:
    """Tests for SendAppMessageCommand._parse_key_value"""

    def test_decimal_key(self):
        key, value = SendAppMessageCommand._parse_key_value("1=42", "int")
        assert key == 1
        assert value == "42"

    def test_value_with_equals(self):
        key, value = SendAppMessageCommand._parse_key_value("5=foo=bar", "string")
        assert key == 5
        assert value == "foo=bar"

    def test_empty_value(self):
        key, value = SendAppMessageCommand._parse_key_value("1=", "string")
        assert key == 1
        assert value == ""

    def test_invalid_missing_equals(self):
        with pytest.raises(ToolError, match="Invalid --int entry"):
            SendAppMessageCommand._parse_key_value("1", "int")

    def test_invalid_key(self):
        with pytest.raises(ToolError, match="Invalid key"):
            SendAppMessageCommand._parse_key_value("notanint=42", "int")

    def test_flag_name_in_error(self):
        with pytest.raises(ToolError, match="--uint"):
            SendAppMessageCommand._parse_key_value("bad=value", "uint")


class TestIntConversion:
    """Tests for --int flag value conversion logic in __call__"""

    def test_decimal(self):
        key, value_str = SendAppMessageCommand._parse_key_value("1=42", "int")
        assert Int32(int(value_str)).value == 42

    def test_negative(self):
        key, value_str = SendAppMessageCommand._parse_key_value("1=-10", "int")
        assert Int32(int(value_str)).value == -10

    def test_invalid_value(self):
        key, value_str = SendAppMessageCommand._parse_key_value("1=notanumber", "int")
        with pytest.raises(ValueError):
            int(value_str)


class TestUintConversion:
    """Tests for --uint flag value conversion logic"""

    def test_decimal(self):
        key, value_str = SendAppMessageCommand._parse_key_value("3=100", "uint")
        assert Uint32(int(value_str)).value == 100

    def test_invalid_value(self):
        key, value_str = SendAppMessageCommand._parse_key_value("1=notanumber", "uint")
        with pytest.raises(ValueError):
            int(value_str)


class TestStringConversion:
    """Tests for --string flag value conversion logic"""

    def test_simple(self):
        key, value_str = SendAppMessageCommand._parse_key_value("2=hello", "string")
        assert CString(value_str).value == "hello"

    def test_empty(self):
        key, value_str = SendAppMessageCommand._parse_key_value("2=", "string")
        assert CString(value_str).value == ""


class TestBytesConversion:
    """Tests for --bytes flag value conversion logic"""

    def test_hex_uppercase(self):
        key, value_str = SendAppMessageCommand._parse_key_value("4=DEADBEEF", "bytes")
        assert ByteArray(bytes.fromhex(value_str)).value == bytes.fromhex("DEADBEEF")

    def test_hex_lowercase(self):
        key, value_str = SendAppMessageCommand._parse_key_value("4=deadbeef", "bytes")
        assert ByteArray(bytes.fromhex(value_str)).value == b'\xde\xad\xbe\xef'

    def test_invalid_value(self):
        key, value_str = SendAppMessageCommand._parse_key_value("1=ZZZZ", "bytes")
        with pytest.raises(ValueError):
            bytes.fromhex(value_str)


class TestParseBytesFile:
    """Tests for SendAppMessageCommand._parse_bytes_file"""

    def test_reads_file_contents(self):
        data = b'\xDE\xAD\xBE\xEF'
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            path = f.name
        try:
            key, value = SendAppMessageCommand._parse_bytes_file("4={}".format(path))
            assert key == 4
            assert isinstance(value, ByteArray)
            assert value.value == data
        finally:
            os.unlink(path)

    def test_filepath_with_equals(self):
        """File paths containing '=' should be handled correctly."""
        data = b'\x00'
        with tempfile.NamedTemporaryFile(suffix='=test.bin', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            key, value = SendAppMessageCommand._parse_bytes_file("1={}".format(path))
            assert key == 1
            assert value.value == data
        finally:
            os.unlink(path)

    def test_invalid_missing_equals(self):
        with pytest.raises(ToolError, match="Invalid --bytes-file entry"):
            SendAppMessageCommand._parse_bytes_file("1")

    def test_invalid_key(self):
        with pytest.raises(ToolError, match="Invalid key"):
            SendAppMessageCommand._parse_bytes_file("notanint=/some/path")

    def test_file_not_found(self):
        with pytest.raises(ToolError, match="Could not read bytes file"):
            SendAppMessageCommand._parse_bytes_file("1=/nonexistent/path/file.bin")
