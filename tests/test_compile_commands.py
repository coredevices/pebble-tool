"""
Tests for the CompileCommandsCommand.
"""
from __future__ import annotations

import os

from pebble_tool.commands.sdk.project.compile_commands import CompileCommandsCommand


class TestFallback:
    def test_project_includes_resolve_against_the_project(self, tmp_path, monkeypatch):
        os.makedirs(tmp_path / 'src' / 'c')
        os.makedirs(tmp_path / 'src' / 'mrsh' / 'include')
        monkeypatch.setattr(CompileCommandsCommand, 'get_sdk_path', lambda self: '/sdk/sdk-core')
        dirs = CompileCommandsCommand()._include_dirs(
            str(tmp_path), 'emery', {'INCLUDES': ['src/mrsh/include', 'src/c', 'missing']})
        assert dirs.count(str(tmp_path / 'src' / 'mrsh' / 'include')) == 1
        # src/c is already a default include dir, so it isn't listed twice.
        assert dirs.count(str(tmp_path / 'src' / 'c')) == 1
        assert str(tmp_path / 'missing') not in dirs
        assert str(tmp_path / 'build' / 'missing') in dirs
