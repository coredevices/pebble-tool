
__author__ = 'katharine'

import os
import shutil
import subprocess
import logging
import sys

from pebble_tool.exceptions import (PebbleProjectException, InvalidJSONException, InvalidProjectException,
                                    OutdatedProjectException, ToolError)
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.util.analytics import post_event
from pebble_tool.commands.sdk import SDKCommand

logger = logging.getLogger("pebble_tool.commands.sdk")


class SDKProjectCommand(SDKCommand):
    @property
    def waf_path(self):
        return os.path.join(self.get_sdk_path(), 'pebble', 'waf')

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

    @property
    def uses_cmake(self):
        """Whether the project builds with CMake (it has a CMakeLists.txt) rather than waf."""
        return os.path.exists(os.path.join(self.project.project_dir, 'CMakeLists.txt'))

    def _cmake(self, args):
        """Run the CMake the SDK installs in its venv, with the Ninja next to it."""
        venv_bin = os.path.join(self.get_sdk_path(), '..', '.venv', 'bin')
        cmake = os.path.join(venv_bin, 'cmake')
        if not os.path.exists(cmake):
            raise ToolError("This project builds with CMake, which the current SDK does not have: "
                            "install a newer SDK.")
        command = [cmake] + list(args)
        logger.debug("cmake command: %s", subprocess.list2cmdline(command))
        env = os.environ.copy()
        env['PATH'] = os.path.abspath(venv_bin) + os.pathsep + env.get('PATH', '')
        subprocess.check_call(command, env=env)

    def _cmake_configure(self, debug=False, args=()):
        build_dir = os.path.join(self.project.project_dir, 'build')
        if os.path.exists(os.path.join(build_dir, 'c4che')):
            # What a waf build left: CMake builds in the same directory.
            shutil.rmtree(build_dir)
        self._cmake(['-S', self.project.project_dir, '-B', build_dir, '-G', 'Ninja',
                     '-DPebbleSDK_DIR=' + os.path.join(self.get_sdk_path(), 'pebble', 'cmake'),
                     '-DCMAKE_BUILD_TYPE=' + ('Debug' if debug else 'Release')] + list(args))

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
