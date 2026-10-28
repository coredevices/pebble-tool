"""
Tests for projects that build with CMake: template choice, build and clean.
"""
from __future__ import annotations

import argparse
import os
import types

import pytest

from pebble_tool.commands.sdk import create
from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.commands.sdk.project.build import BuildCommand, CleanCommand
from pebble_tool.commands.sdk.project.compile_commands import CompileCommandsCommand
from pebble_tool.exceptions import ToolError


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / 'CMakeLists.txt').write_text('')
    monkeypatch.chdir(tmp_path)

    def sdk_project_call(self, args):
        self._verbosity = 0
        self.project = types.SimpleNamespace(project_dir=str(tmp_path), project_type='native',
                                             dependencies={}, target_platforms=['basalt', 'emery'])

    monkeypatch.setattr(SDKProjectCommand, '__call__', sdk_project_call)
    monkeypatch.setattr(SDKProjectCommand, 'get_sdk_path', lambda self: '/sdk/sdk-core')
    return tmp_path


def _parse(command_class, argv):
    parser = argparse.ArgumentParser()
    command_class.add_parser(parser.add_subparsers())
    return command_class(), parser.parse_args([command_class.command] + list(argv))


def _record_cmake(monkeypatch, project):
    calls = []

    def cmake(self, args):
        calls.append(list(args))
        if args[0] == '-S':
            os.makedirs(project / 'build', exist_ok=True)

    monkeypatch.setattr(SDKProjectCommand, '_cmake', cmake)
    return calls


@pytest.mark.parametrize('argv, build_type', [([], 'Release'), (['--debug'], 'Debug')])
def test_build_configures_and_builds_with_cmake(project, monkeypatch, argv, build_type):
    calls = _record_cmake(monkeypatch, project)
    monkeypatch.setattr(BuildCommand, '_waf', lambda *a, **k: pytest.fail("waf ran"))
    monkeypatch.setattr('pebble_tool.commands.sdk.project.build.post_event', lambda *a, **k: None)
    command, args = _parse(BuildCommand, argv)
    command(args)

    build = str(project / 'build')
    assert calls == [
        ['-S', str(project), '-B', build, '-G', 'Ninja',
         '-DPebbleSDK_DIR=/sdk/sdk-core/pebble/cmake', '-DCMAKE_BUILD_TYPE=' + build_type],
        ['--build', build],
    ]


def test_build_replaces_a_waf_build_directory(project, monkeypatch):
    os.makedirs(project / 'build' / 'c4che')
    _record_cmake(monkeypatch, project)
    monkeypatch.setattr('pebble_tool.commands.sdk.project.build.post_event', lambda *a, **k: None)
    command, args = _parse(BuildCommand, [])
    command(args)
    assert not (project / 'build' / 'c4che').exists()


def test_clean_removes_the_build_directory(project, monkeypatch):
    os.makedirs(project / 'build')
    monkeypatch.setattr(CleanCommand, '_waf', lambda *a, **k: pytest.fail("waf ran"))
    command, args = _parse(CleanCommand, [])
    command(args)
    assert not (project / 'build').exists()


def test_compile_commands_points_at_cmake(project):
    command, args = _parse(CompileCommandsCommand, [])
    with pytest.raises(ToolError, match='Unsupported for CMake projects'):
        command(args)


@pytest.mark.parametrize('has_cmake, build_file', [(True, 'CMakeLists.txt'), (False, 'wscript')])
def test_new_project_builds_with_what_the_sdk_has(tmp_path, monkeypatch, has_cmake, build_file):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(create.NewProjectCommand, 'get_sdk_path', lambda self: '/sdk/sdk-core')
    monkeypatch.setattr(create, 'sdk_has_cmake', lambda: has_cmake)
    monkeypatch.setattr(create, 'sdk_version', lambda: '4.33.1')
    monkeypatch.setattr(create, 'post_event', lambda *a, **k: None)
    monkeypatch.setattr('pebble_tool.commands.base.BaseCommand.__call__', lambda self, args: None)
    command, args = _parse(create.NewProjectCommand, ['face'])
    command.sdk = None
    command(args)

    files = set(os.listdir(tmp_path / 'face'))
    assert files & {'CMakeLists.txt', 'wscript'} == {build_file}
