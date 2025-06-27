
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

    def _waf(self, command, extra_env=None, args=None):
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
        subprocess.check_call(command, env=env)

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
