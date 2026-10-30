
__author__ = 'katharine'

import os
import subprocess
import logging
import sys

from pebble_tool.exceptions import (PebbleProjectException, InvalidJSONException, InvalidProjectException,
                                    OutdatedProjectException)
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.util.analytics import post_event
from pebble_tool.commands.sdk import SDKCommand

logger = logging.getLogger("pebble_tool.commands.sdk")


class SDKProjectCommand(SDKCommand):
    @property
    def waf_path(self):
        return os.path.join(self.get_sdk_path(), 'pebble', 'waf')

    def _ensure_venv_matches_pebble_tool(self, venv):
        """The SDK venv's Python must match pebble-tool's Python; otherwise
        running waf with PYTHONPATH set to pebble-tool's sys.path mixes
        bytecode from two minor versions and produces 'SRE module mismatch'
        and similar errors. Recreate the venv with uv if it drifts (e.g.
        after `brew reinstall python@3.X` or a pebble-tool upgrade)."""
        venv_python = os.path.join(venv, 'bin', 'python')
        if not os.path.exists(venv_python):
            recreate = True
        else:
            try:
                got = subprocess.check_output(
                    [venv_python, '-c',
                     'import sys; print("%d.%d" % sys.version_info[:2])'],
                    text=True).strip()
                want = "%d.%d" % sys.version_info[:2]
                recreate = (got != want)
            except subprocess.CalledProcessError:
                recreate = True
        if not recreate:
            return
        logger.warning("SDK venv at %s is stale; rebuilding with uv to match "
                       "pebble-tool's Python (%d.%d).",
                       venv, sys.version_info[0], sys.version_info[1])
        from pebble_tool.sdk.manager import _create_sdk_venv, _venv_pip_install
        import shutil as _shutil
        if os.path.exists(venv):
            _shutil.rmtree(venv)
        _create_sdk_venv(venv)
        requirements = os.path.join(self.get_sdk_path(), 'requirements.txt')
        if os.path.exists(requirements):
            _venv_pip_install(venv, '-r', requirements)

    def _waf(self, command, extra_env=None, args=None):
        command, env = self._waf_invocation(command, extra_env, args)
        subprocess.check_call(command, env=env)

    def _waf_invocation(self, command, extra_env=None, args=None):
        """The command line and environment that run `waf <command>` with the SDK's venv."""
        if args is None:
            args = []
        else:
            args = list(args)
        if self._verbosity > 0:
            v = '-' + ('v' * self._verbosity)
            args = [v] + args
        venv = os.path.join(self.get_sdk_path(), '..', '.venv')
        self._ensure_venv_matches_pebble_tool(venv)
        node_modules = os.path.join(self.get_sdk_path(), '..', 'node_modules')
        command = [os.path.join(venv, 'bin', 'python'), self.waf_path, command] + args
        logger.debug("waf command: %s", subprocess.list2cmdline(command))
        env = os.environ.copy()
        env['PYTHONHOME'] = os.path.abspath(venv)
        env['PYTHONPATH'] = ':'.join(sys.path)
        env['NODE_PATH'] = node_modules
        env['NOCLIMB'] = "1"  # This prevents waf from climbing into parent directories and executing commands
        if extra_env is not None:
            env.update(extra_env)
        return command, env

    def __call__(self, args):
        super(SDKProjectCommand, self).__call__(args)
        try:
            self.project = PebbleProject()
        except PebbleProjectException as e:
            event_map = {
                InvalidProjectException: "sdk_run_without_project",
                InvalidJSONException: "sdk_json_error",
                OutdatedProjectException: "sdk_json_error",
            }
            if type(e) in event_map:
                post_event(event_map[type(e)])
            raise
