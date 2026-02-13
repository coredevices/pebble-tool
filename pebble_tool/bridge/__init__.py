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


def run_bridge(command, qemu_port, pbw_path=None, platform=None, extra_args=None):
    """Run the libpebble3 bridge as a subprocess (output goes to stdout/stderr).

    :param command: Bridge command name
    :param qemu_port: QEMU TCP port number
    :param pbw_path: Path to .pbw file (for install commands)
    :param platform: Pebble platform name (for install commands)
    :param extra_args: Additional command arguments
    :returns: subprocess exit code
    """
    jar = get_bridge_jar()

    cmd = ['java', '-jar', jar, command, str(qemu_port)]
    if pbw_path is not None:
        cmd.append(pbw_path)
    if platform is not None:
        cmd.append(platform)
    if extra_args:
        cmd.extend(extra_args)

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


def run_bridge_capture(command, qemu_port, extra_args=None):
    """Run the bridge and capture stdout (stderr still goes to terminal).

    :param command: Bridge command name
    :param qemu_port: QEMU TCP port number
    :param extra_args: Additional command arguments
    :returns: (exit_code, stdout_text)
    """
    jar = get_bridge_jar()

    cmd = ['java', '-jar', jar, command, str(qemu_port)]
    if extra_args:
        cmd.extend(extra_args)

    logger.info("Bridge command: %s", subprocess.list2cmdline(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
        )
        stdout, _ = process.communicate()
        return process.returncode, stdout.decode('utf-8', errors='replace')
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


def ensure_bridge_qemu(args):
    """Ensure QEMU is running for bridge mode and return (qemu_port, version).

    Common helper for all commands that need to route through the bridge
    when --emulator is specified.
    """
    from pebble_tool.sdk.emulator import ensure_qemu_for_bridge

    platform = args.emulator
    version = getattr(args, 'sdk', None)
    vnc_enabled = getattr(args, 'vnc', False)

    qemu_port, qemu_serial_port, version = ensure_qemu_for_bridge(
        platform, version, vnc_enabled
    )
    return qemu_port, version
