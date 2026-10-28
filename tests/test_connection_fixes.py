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
