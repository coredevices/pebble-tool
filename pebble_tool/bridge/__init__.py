"""
libpebble3 bridge - Kotlin-based Pebble protocol implementation.

This module provides integration between pebble-tool and the libpebble3
Kotlin CLI bridge, which communicates directly with QEMU emulators using
the Pebble protocol (bypassing pypkjs/libpebble2).
"""

import logging
import os
import subprocess
import sys

from pebble_tool.exceptions import ToolError

logger = logging.getLogger("pebble_tool.bridge")

BRIDGE_JAR = os.path.join(os.path.dirname(__file__), 'libpebble3-bridge-all.jar')


def get_bridge_jar():
    """Return the path to the bridge JAR, raising ToolError if not found."""
    if not os.path.exists(BRIDGE_JAR):
        raise ToolError("libpebble3 bridge JAR not found at: {}".format(BRIDGE_JAR))
    return BRIDGE_JAR


def run_bridge(command, qemu_port, pbw_path=None, platform=None):
    """Run the libpebble3 bridge as a subprocess.

    :param command: Bridge command ('install', 'logs', 'install-and-logs', 'ping')
    :param qemu_port: QEMU TCP port number
    :param pbw_path: Path to .pbw file (required for install commands)
    :param platform: Pebble platform name (required for install commands)
    :returns: subprocess exit code
    """
    jar = get_bridge_jar()

    cmd = ['java', '-jar', jar, command, str(qemu_port)]
    if pbw_path is not None:
        cmd.append(pbw_path)
    if platform is not None:
        cmd.append(platform)

    logger.info("Bridge command: %s", subprocess.list2cmdline(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        process.wait()
        return process.returncode
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    except FileNotFoundError:
        raise ToolError(
            "Java not found. The libpebble3 bridge requires Java 21+. "
            "Install it with: apt install openjdk-21-jre-headless"
        )
