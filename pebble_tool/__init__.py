
__author__ = 'katharine'

import atexit
import argparse
import logging
import os
import sys
import requests.packages.urllib3 as urllib3
from pebble_tool.util import get_persist_dir
from six import text_type

from .exceptions import ToolError
from .sdk import sdk_version
from .util.analytics import wait_for_analytics, analytics_prompt
from .util.config import config
from .util.updates import wait_for_update_checks
from .util.wsl import maybe_apply_wsl_hacks
from importlib.metadata import version

__version__ = version('pebble-tool')
# Parse version into version_info tuple
parts = __version__.split('.')
__version_info__ = tuple(int(p) for p in parts[:3])

# Violating PEP8 for custom command ordering for `pebble -h`
from .commands.sdk import manage
from .commands.sdk.project import build

from .commands.base import register_children
from .commands import (install, logs, screenshot, timeline, emucontrol, ping, account, repl,
                       transcription_server, data_logging)
from .commands.sdk import create, emulator
from .commands.sdk.project import package, analyse_size, convert, debug

def run_tool(args=None):
    urllib3.disable_warnings()  # sigh. :(
    logging.basicConfig()
    maybe_apply_wsl_hacks()
    analytics_prompt()
    parser = argparse.ArgumentParser(description="Pebble Tool", prog="pebble",
                                     epilog="For help on an individual command, call that command with --help.")
    version_string = "Pebble Tool v{}".format(__version__)
    if sdk_version() is not None:
        version_string += " (active SDK: v{})".format(sdk_version())
        # Add QEMU and others to PATH
        os.environ['PATH'] = "{}:{}".format(os.path.join(get_persist_dir(), "SDKs", sdk_version(), "toolchain", "bin"), os.environ['PATH'])
        extra_path = os.environ.get('PEBBLE_EXTRA_PATH')
        if extra_path:
            os.environ['PATH'] = "{}:{}".format(extra_path, os.environ['PATH'])
    parser.add_argument("--version", action="version", version=version_string)
    register_children(parser)
    args = parser.parse_args(args)
    if not hasattr(args, 'func'):
        parser.error("no subcommand specified.")
    try:
        args.func(args)
    except ToolError as e:
        parser.exit(message=text_type(e)+"\n", status=1)
        sys.exit(1)


@atexit.register
def wait_for_cleanup():
    import time
    now = time.time()
    wait_for_analytics(2)
    wait_for_update_checks(2)
    logging.info("Spent %f seconds waiting for analytics.", time.time() - now)
    config.save()
