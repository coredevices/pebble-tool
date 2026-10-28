"""
Tests for the CompileCommandsCommand.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import types

import pytest

from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.commands.sdk.project.compile_commands import CompileCommandsCommand
from pebble_tool.exceptions import ToolError

WAF_HELP = """usage: waf [commands] [options]

Main commands (example: ./waf build -j4)
  build    : executes the build
{extra}  clean    : cleans the project
  configure: configures the project
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    os.makedirs(tmp_path / 'src' / 'c')
    (tmp_path / 'src' / 'c' / 'main.c').write_text('int main(void) { return 0; }\n')
    os.makedirs(tmp_path / 'src' / 'mrsh' / 'include')
    monkeypatch.chdir(tmp_path)

    def sdk_project_call(self, args):
        self._verbosity = 0
        self.project = types.SimpleNamespace(project_dir=str(tmp_path),
                                             target_platforms=['basalt', 'emery'])

    monkeypatch.setattr(SDKProjectCommand, '__call__', sdk_project_call)
    monkeypatch.setattr(CompileCommandsCommand, 'get_sdk_path', lambda self: '/sdk/sdk-core')
    return tmp_path


def _run(argv=()):
    parser = argparse.ArgumentParser()
    CompileCommandsCommand.add_parser(parser.add_subparsers())
    args = parser.parse_args(['compile-commands'] + list(argv))
    return CompileCommandsCommand(), args


class TestFromBuild:
    def test_copies_the_database_waf_clangdb_writes(self, project, monkeypatch):
        entry = {"directory": str(project / 'build'), "file": str(project / 'lib' / 'x.cpp'),
                 "arguments": ["/tc/arm-none-eabi-g++", "-c", str(project / 'lib' / 'x.cpp')]}
        calls = []

        def waf(self, command, extra_env=None, args=None):
            calls.append([command] + list(args or []))
            if command == 'clangdb':
                os.makedirs(project / 'build', exist_ok=True)
                (project / 'build' / 'compile_commands.json').write_text(json.dumps([entry]))

        monkeypatch.setattr(CompileCommandsCommand, '_waf', waf)
        monkeypatch.setattr(CompileCommandsCommand, '_sdk_has_clangdb', lambda self: True)
        command, args = _run()
        command(args)

        assert calls == [['configure'], ['clangdb', '--clangdb-platform=emery']]
        assert json.loads((project / 'compile_commands.json').read_text()) == [entry]

    def test_passes_the_requested_platform(self, project, monkeypatch):
        calls = []

        def waf(self, command, extra_env=None, args=None):
            calls.append([command] + list(args or []))
            os.makedirs(project / 'build', exist_ok=True)
            (project / 'build' / 'compile_commands.json').write_text('[]')

        monkeypatch.setattr(CompileCommandsCommand, '_waf', waf)
        monkeypatch.setattr(CompileCommandsCommand, '_sdk_has_clangdb', lambda self: True)
        command, args = _run(['--platform', 'basalt'])
        command(args)

        assert calls[-1] == ['clangdb', '--clangdb-platform=basalt']

    def test_a_failing_clangdb_is_an_error(self, project, monkeypatch):
        def waf(self, command, extra_env=None, args=None):
            if command == 'clangdb':
                raise subprocess.CalledProcessError(1, command)

        monkeypatch.setattr(CompileCommandsCommand, '_waf', waf)
        monkeypatch.setattr(CompileCommandsCommand, '_sdk_has_clangdb', lambda self: True)
        command, args = _run()
        with pytest.raises(ToolError):
            command(args)
        assert not (project / 'compile_commands.json').exists()


class TestSdkSupport:
    @pytest.mark.parametrize('extra, supported', [
        ('  clangdb  : \n', True),
        ('  clangdb  : writes build/compile_commands.json\n', True),
        ('', False),
    ])
    def test_reads_the_commands_waf_lists(self, project, monkeypatch, extra, supported):
        monkeypatch.setattr(CompileCommandsCommand, '_waf_invocation',
                            lambda self, command, extra_env=None, args=None: (['waf', command], {}))
        monkeypatch.setattr(subprocess, 'run', lambda *k, **kw: subprocess.CompletedProcess(
            k[0], 0, stdout=WAF_HELP.format(extra=extra)))
        assert CompileCommandsCommand()._sdk_has_clangdb() is supported


class TestFallback:
    def test_reconstructs_when_the_sdk_has_no_clangdb(self, project, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(CompileCommandsCommand, '_waf',
                            lambda self, command, extra_env=None, args=None: calls.append(command))
        monkeypatch.setattr(CompileCommandsCommand, '_sdk_has_clangdb', lambda self: False)
        monkeypatch.setattr(CompileCommandsCommand, '_find_compiler', lambda self: '/tc/gcc')
        monkeypatch.setattr(CompileCommandsCommand, '_waf_env', lambda self, platform: {
            'CFLAGS': ['-std=c99', '-std=c23', '-DFOO_TEST'],
            'DEFINES': ['PBL_PLATFORM_EMERY'],
            'INCLUDES': ['src/mrsh/include', 'emery'],
        })
        command, args = _run()
        command(args)

        assert calls == ['configure']
        assert 'needs an SDK newer than 4.33.1' in capsys.readouterr().out
        db = json.loads((project / 'compile_commands.json').read_text())
        assert [e['file'] for e in db] == [str(project / 'src' / 'c' / 'main.c')]
        arguments = db[0]['arguments']
        assert arguments[:4] == ['/tc/gcc', '-std=c99', '-std=c23', '-DFOO_TEST']
        assert '-DPBL_PLATFORM_EMERY' in arguments
        assert '-I' + str(project / 'src' / 'mrsh' / 'include') in arguments
        assert '-I' + str(project / 'build' / 'emery') in arguments
        assert '-I' + str(project / 'build' / 'emery' / 'src') in arguments

    def test_project_includes_resolve_against_the_project(self, project):
        dirs = CompileCommandsCommand()._include_dirs(
            str(project), 'emery', {'INCLUDES': ['src/mrsh/include', 'src/c', 'missing']})
        assert dirs.count(str(project / 'src' / 'mrsh' / 'include')) == 1
        # src/c is already a default include dir, so it isn't listed twice.
        assert dirs.count(str(project / 'src' / 'c')) == 1
        assert str(project / 'missing') not in dirs
        assert str(project / 'build' / 'missing') in dirs
