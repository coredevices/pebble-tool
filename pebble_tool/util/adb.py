__author__ = 'katharine'

import atexit
import logging
import re
import subprocess

from pebble_tool.exceptions import ToolError

ACTION = 'coredevices.coreapp.DEV_CONNECTION'
COMPONENT = 'coredevices.coreapp/coredevices.coreapp.debug.DevConnectionReceiver'

_RESULT_RE = re.compile(r'result=(-?\d+)(?:, data="(.*?)")?', re.DOTALL)
_PORT_RE = re.compile(r'^\s*(\d+)\s*$', re.MULTILINE)

logger = logging.getLogger("pebble_tool.util.adb")


def _adb(device, *args):
    command = ['adb'] + list(device) + list(args)
    logger.debug("Running %s", command)
    try:
        return subprocess.check_output(command, stderr=subprocess.STDOUT).decode('utf-8', 'replace')
    except OSError:
        raise ToolError("Couldn't run adb; make sure the Android platform-tools are installed "
                        "and adb is on your PATH.")
    except subprocess.CalledProcessError as e:
        raise ToolError("adb failed: {}".format(e.output.decode('utf-8', 'replace').strip()))


def start_dev_connection(device):
    """Ask the Pebble app to start its LAN dev connection; returns the port it's listening on."""
    output = _adb(device, 'shell', 'am', 'broadcast', '-a', ACTION, '-n', COMPONENT)
    match = _RESULT_RE.search(output)
    if match is None:
        raise ToolError("Couldn't understand the response from the Pebble app: {}".format(output.strip()))

    code, data = match.group(1), match.group(2)
    if code != '0':
        raise ToolError("The Pebble app couldn't start a developer connection: {}"
                        .format(data or "error {}".format(code)))
    # An unhandled broadcast also reports result=0, so the absence of result data means nothing
    # received it.
    if not data:
        raise ToolError("The Pebble app didn't respond to the developer connection broadcast. "
                        "Make sure it's installed and up to date.")
    try:
        return int(data)
    except ValueError:
        raise ToolError("The Pebble app reported an invalid port: {!r}".format(data))


def forward(device, port):
    """Tunnel a port on this machine through to `port` on the device; returns the local port."""
    output = _adb(device, 'forward', 'tcp:0', 'tcp:{}'.format(port))
    match = _PORT_RE.search(output)
    if match is None:
        raise ToolError("Couldn't work out which port adb forwarded: {}".format(output.strip()))
    local_port = int(match.group(1))
    atexit.register(_remove_forward, device, local_port)
    return local_port


def _remove_forward(device, local_port):
    try:
        _adb(device, 'forward', '--remove', 'tcp:{}'.format(local_port))
    except ToolError:
        pass
