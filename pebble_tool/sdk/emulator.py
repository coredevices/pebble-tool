
__author__ = 'katharine'

from six import iteritems

import bz2
import errno
import json
import logging
import os
import os.path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from libpebble2.communication.transports.websocket import WebsocketTransport
from libpebble2.exceptions import ConnectionError

from pebble_tool.account import get_default_account
from pebble_tool.exceptions import MissingEmulatorError, ToolError
from pebble_tool.util.analytics import post_event
from . import sdk_path, get_sdk_persist_dir, sdk_manager

logger = logging.getLogger("pebble_tool.sdk.emulator")
black_hole = open(os.devnull, 'w')


def get_emulator_info_path():
    return os.path.join(tempfile.gettempdir(), 'pb-emulator.json')


def get_all_emulator_info():
    try:
        with open(get_emulator_info_path()) as f:
            return json.load(f)
    except (OSError, IOError):
        return {}


def get_emulator_info(platform, version=None):
    info = get_all_emulator_info().get(platform, None)

    # If we have nothing for the platform, it's None
    if info is None:
        return None

    # If a specific version was requested, return that directly.
    if version is not None:
        return info.get(version, None)

    # If a version wasn't requested, look for one that's alive.
    # If exactly one is alive, return that.
    alive = []
    for sdk_version, sdk_info in iteritems(info):
        if ManagedEmulatorTransport.is_emulator_alive(platform, sdk_version):
            alive.append(sdk_version)
        else:
            # Clean up dead entries that are left hanging around.
            update_emulator_info(platform, sdk_version, None)
    if len(alive) > 1:
        raise ToolError("There are multiple {} emulators (versions {}) running. You must specify a version."
                        .format(platform, ', '.join(alive)))
    elif len(alive) == 0:
        return None
    else:
        return info[alive[0]]


def update_emulator_info(platform, version, new_content):
    try:
        with open(get_emulator_info_path()) as f:
            content = json.load(f)
    except (OSError, IOError):
        content = {}

    if new_content is None:
        del content.get(platform, {version: None})[version]
    else:
        content.setdefault(platform, {})[version] = new_content
    with open(get_emulator_info_path(), 'w') as f:
        json.dump(content, f, indent=4)


class ManagedEmulatorTransport(WebsocketTransport):
    def __init__(self, platform, version=None):
        self.platform = platform
        self.version = version
        self._find_ports()
        super(ManagedEmulatorTransport, self).__init__('ws://localhost:{}/'.format(self.pypkjs_port))

    def connect(self):
        self._spawn_processes()
        for i in range(10):
            time.sleep(0.5)
            try:
                super(ManagedEmulatorTransport, self).connect()
            except ConnectionError:
                continue
            else:
                return
        super(ManagedEmulatorTransport, self).connect()

    def _find_ports(self):
        info = get_emulator_info(self.platform, self.version)
        qemu_running = False
        if info is not None:
            self.version = info['version']
            if self._is_pid_running(info['qemu']['pid']):
                qemu_running = True
                self.qemu_port = info['qemu']['port']
                self.qemu_serial_port = info['qemu']['serial']
                self.qemu_pid = info['qemu']['pid']
                self.qemu_gdb_port = info['qemu'].get('gdb', None)
            else:
                self.qemu_pid = None

            if self._is_pid_running(info['pypkjs']['pid']):
                if qemu_running:
                    self.pypkjs_port = info['pypkjs']['port']
                    self.pypkjs_pid = info['pypkjs']['pid']
                else:
                    logger.info("pypkjs is alive, but qemu is not, so we're killing it.")
                    os.kill(info['pypkjs']['pid'], signal.SIGKILL)
                    self.pypkjs_pid = None
            else:
                self.pypkjs_pid = None
        else:
            self.qemu_pid = None
            self.pypkjs_pid = None

        if self.qemu_pid is None:
            self.qemu_port = self._choose_port()
            self.qemu_serial_port = self._choose_port()
            self.qemu_gdb_port = self._choose_port()

        if self.pypkjs_pid is None:
            self.pypkjs_port = self._choose_port()

    def _spawn_processes(self):
        if self.version is None:
            sdk_path()  # Force an SDK to be installed.
            self.version = sdk_manager.get_current_sdk()
        if self.qemu_pid is None:
            logger.info("Spawning QEMU.")
            self._spawn_qemu()
        else:
            logger.info("QEMU is already running.")

        if self.pypkjs_pid is None:
            logger.info("Spawning pypkjs.")
            self._spawn_pypkjs()
        else:
            logger.info("pypkjs is already running.")

        self._save_state()

    def _save_state(self):
        d = {
            'qemu': {
                'pid': self.qemu_pid,
                'port': self.qemu_port,
                'serial': self.qemu_serial_port,
                'gdb': self.qemu_gdb_port,
            },
            'pypkjs': {
                'pid': self.pypkjs_pid,
                'port': self.pypkjs_port,
            },
            'version': self.version,
        }
        update_emulator_info(self.platform, self.version, d)


    def _spawn_qemu(self):
        qemu_bin = os.environ.get('PEBBLE_QEMU_PATH', 'qemu-pebble')
        qemu_micro_flash = os.path.join(sdk_manager.path_for_sdk(self.version), 'pebble', self.platform, 'qemu',
                                        "qemu_micro_flash.bin")
        qemu_spi_flash = self._get_spi_path()

        for path in (qemu_micro_flash, qemu_spi_flash):
            if not os.path.exists(path):
                raise MissingEmulatorError("Can't launch emulator: missing required file at {}".format(path))

        command = [
            qemu_bin,
            "-rtc", "base=localtime",
            "-serial", "null",
            "-serial", "tcp::{},server,nowait".format(self.qemu_port),
            "-serial", "tcp::{},server".format(self.qemu_serial_port),
            "-pflash", qemu_micro_flash,
            "-gdb", "tcp::{},server,nowait".format(self.qemu_gdb_port),
        ]

        platform_args = {
            'emery': [
                '-machine', 'pebble-robert-bb',
                '-cpu', 'cortex-m4',
                '-pflash', qemu_spi_flash,
            ],
            'diorite': [
                '-machine', 'pebble-silk-bb',
                '-cpu', 'cortex-m4',
                '-mtdblock', qemu_spi_flash,
            ],
            'chalk': [
                '-machine', 'pebble-s4-bb',
                '-cpu', 'cortex-m4',
                '-pflash', qemu_spi_flash,
            ],
            'basalt': [
                '-machine', 'pebble-snowy-bb',
                '-cpu', 'cortex-m4',
                '-pflash', qemu_spi_flash,
            ],
            'aplite': [
                '-machine', 'pebble-bb2',
                '-cpu', 'cortex-m3',
                '-mtdblock', qemu_spi_flash,
            ]
        }

        command.extend(platform_args[self.platform])

        # Dylibs for Apple Silicon Macs
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.path.join(sdk_manager.root_path_for_sdk(self.version), 'toolchain', 'lib')

        logger.info("Qemu command: %s", subprocess.list2cmdline(command))
        process = subprocess.Popen(command, stdout=self._get_output(), stderr=self._get_output())
        time.sleep(0.2)
        if process.poll() is not None:
            try:
                subprocess.check_output(command, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as e:
                raise MissingEmulatorError("Couldn't launch emulator:\n{}".format(e.output.strip()))
        self.qemu_pid = process.pid
        self._wait_for_qemu()

    def _wait_for_qemu(self):
        logger.info("Waiting for the firmware to boot.")
        for i in range(60):
            time.sleep(0.2)
            try:
                s = socket.create_connection(('localhost', self.qemu_serial_port))
            except socket.error:
                logger.debug("QEMU not ready yet.")
                pass
            else:
                break
        else:
            post_event("qemu_launched", success=False, reason="qemu_launch_timeout")
            raise ToolError("Emulator launch timed out.")
        received = b''
        while True:
            try:
                received += s.recv(256)
            except socket.error as e:
                # Ignore "Interrupted system call"
                if e.errno != errno.EINTR:
                    raise
            if b"<SDK Home>" in received or b"<Launcher>" in received or b"Ready for communication" in received:
                break
        s.close()
        post_event("qemu_launched", success=True)
        logger.info("Firmware booted.")

    def _copy_spi_image(self, path):
        sdk_qemu_spi_flash = os.path.join(sdk_path(), 'pebble', self.platform, 'qemu', 'qemu_spi_flash.bin.bz2')
        if not os.path.exists(sdk_qemu_spi_flash):
            raise MissingEmulatorError("Your SDK does not support the Pebble Emulator.")
        else:
            try:
                os.makedirs(os.path.dirname(path))
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise

            # Copy the compressed file.
            with bz2.BZ2File(sdk_qemu_spi_flash) as from_file:
                with open(path, 'wb') as to_file:
                    while True:
                        data = from_file.read(512)
                        if not data:
                            break
                        to_file.write(data)

    def _get_spi_path(self):
        platform = self.platform

        if sdk_manager.get_current_sdk() == 'tintin':
            sdk_qemu_spi_flash = os.path.join(sdk_manager.path_for_sdk(self.version), 'pebble', platform, 'qemu',
                                              'qemu_spi_flash.bin')
            return sdk_qemu_spi_flash

        path = os.path.join(get_sdk_persist_dir(platform, self.version), 'qemu_spi_flash.bin')
        if not os.path.exists(path):
            self._copy_spi_image(path)
        return path

    def _spawn_pypkjs(self):
        layout_file = os.path.join(sdk_manager.path_for_sdk(self.version), 'pebble', self.platform, 'qemu',
                                   "layouts.json")

        command = [
            sys.executable, "-m", "pypkjs",
            "--qemu", "localhost:{}".format(self.qemu_port),
            "--port", str(self.pypkjs_port),
            "--persist", get_sdk_persist_dir(self.platform, self.version),
            "--layout", layout_file,
            '--debug',
        ]

        account = get_default_account()
        if account.is_logged_in:
            command.extend(['--oauth', account.bearer_token])
        if logger.getEffectiveLevel() <= logging.DEBUG:
            command.append('--debug')
        logger.info("pypkjs command: %s", subprocess.list2cmdline(command))
        process = subprocess.Popen(command, stdout=self._get_output(), stderr=self._get_output())
        time.sleep(0.5)
        if process.poll() is not None:
            try:
                subprocess.check_output(command, stderr=subprocess.STDOUT, text=True)
            except subprocess.CalledProcessError as e:
                raise MissingEmulatorError("Couldn't launch pypkjs:\n{}".format(e.output.strip()))
        self.pypkjs_pid = process.pid

    def _get_output(self):
        if logger.getEffectiveLevel() <= logging.DEBUG:
            return None
        else:
            return black_hole

    @classmethod
    def _choose_port(cls):
        sock = socket.socket()
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    @classmethod
    def _is_pid_running(cls, pid):
        # PBL-21228: This isn't going to work on Windows.
        try:
            os.kill(pid, 0)
        except OSError as e:
            if e.errno == 3:
                return False
            else:
                raise
        return True

    @classmethod
    def is_emulator_alive(cls, platform, version=None):
        info = get_emulator_info(platform, version or sdk_manager.get_current_sdk())
        if info is None:
            return False
        return cls._is_pid_running(info['pypkjs']['pid']) and cls._is_pid_running(info['pypkjs']['pid'])
