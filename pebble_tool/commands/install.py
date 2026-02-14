
__author__ = 'katharine'

import os
import os.path
from progressbar import ProgressBar, Bar, FileTransferSpeed, Timer, Percentage

from libpebble2.communication.transports.websocket import WebsocketTransport, MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import WebSocketInstallBundle, WebSocketInstallStatus
from libpebble2.exceptions import TimeoutError
from libpebble2.services.install import AppInstaller

from .base import PebbleCommand, BaseCommand
from ..util.logs import PebbleLogPrinter, QemuLogPrinter
from ..exceptions import ToolError


class InstallCommand(PebbleCommand):
    """Installs the given app on the watch."""
    command = 'install'

    def __call__(self, args):
        emulator_platform = getattr(args, 'emulator', None)
        if emulator_platform:
            # Use libpebble3 bridge for emulator (bypasses pypkjs/libpebble2)
            BaseCommand.__call__(self, args)
            self._install_via_bridge(args)
            return

        super(InstallCommand, self).__call__(args)
        try:
            ToolAppInstaller(self.pebble, args.pbw).install()
        except IOError as e:
            if args.pbw is None:
                raise ToolError("You must either run this command from a project directory or specify the pbw "
                                "to install.")
            else:
                raise ToolError(str(e))

        # Start log printers
        log_printer = None
        qemu_log_printer = None

        if args.logs:
            log_printer = PebbleLogPrinter(self.pebble)
        if args.qemu_logs:
            qemu_log_printer = QemuLogPrinter(self.pebble)

        # If both are enabled, run them concurrently
        if log_printer and qemu_log_printer:
            import threading

            # Start QEMU log printer in background thread
            qemu_thread = threading.Thread(target=qemu_log_printer.wait)
            qemu_thread.daemon = True
            qemu_thread.start()

            # Run app log printer in main thread (so Ctrl+C works properly)
            try:
                log_printer.wait()
            except KeyboardInterrupt:
                # Clean up both log printers
                log_printer.stop()
                qemu_log_printer.stop()
                raise
        elif log_printer:
            log_printer.wait()
        elif qemu_log_printer:
            qemu_log_printer.wait()

    def _install_via_bridge(self, args):
        """Install app using libpebble3 bridge (direct QEMU TCP, no pypkjs)."""
        from pebble_tool.sdk.emulator import ensure_qemu_for_bridge
        from pebble_tool.bridge import run_bridge

        platform = args.emulator
        version = getattr(args, 'sdk', None)
        vnc_enabled = getattr(args, 'vnc', False)

        qemu_port, qemu_serial_port, version = ensure_qemu_for_bridge(
            platform, version, vnc_enabled
        )

        pbw = args.pbw or 'build/{}.pbw'.format(os.path.basename(os.getcwd()))
        pbw = os.path.abspath(pbw)

        if not os.path.exists(pbw):
            raise ToolError("PBW file not found: {}".format(pbw))

        if args.logs:
            command = 'install-and-logs'
        else:
            command = 'install'

        extra_args = []
        location = getattr(args, 'location', None)
        if location:
            extra_args.extend(['--location', location])

        print("Installing via libpebble3 bridge (QEMU port {})...".format(qemu_port))
        rc = run_bridge(command, qemu_port, pbw_path=pbw, platform=platform,
                        extra_args=extra_args or None)

        if rc != 0:
            raise ToolError("Bridge install failed (exit code {}).".format(rc))

    @classmethod
    def add_parser(cls, parser):
        parser = super(InstallCommand, cls).add_parser(parser)
        parser.add_argument('pbw', help="Path to app to install.", nargs='?', default=None)
        parser.add_argument('--logs', action="store_true", help="Enable logs")
        parser.add_argument('--qemu_logs', action="store_true", help="Enable QEMU serial logs (emulator only)")
        parser.add_argument('--location', default=None,
                            help="Geolocation for PKJS: LAT,LON (e.g. 51.5074,-0.1278) or 'auto' for IP lookup")
        return parser


class ToolAppInstaller(object):
    def __init__(self, pebble, pbw=None):
        self.pebble = pebble
        self.pbw = pbw or 'build/{}.pbw'.format(os.path.basename(os.getcwd()))
        self.progress_bar = ProgressBar(widgets=[Percentage(), Bar(marker='=', left='[', right=']'), ' ',
                                                 FileTransferSpeed(), ' ', Timer(format='%s')])

    def install(self):
        if isinstance(self.pebble.transport, WebsocketTransport):
            self._install_via_websocket(self.pebble, self.pbw)
        else:
            self._install_via_serial(self.pebble, self.pbw)

    def _install_via_serial(self, pebble, pbw):
        installer = AppInstaller(pebble, pbw)
        self.progress_bar.maxval = installer.total_size
        self.progress_bar.start()
        installer.register_handler("progress", self._handle_pp_progress)
        installer.install()
        self.progress_bar.finish()

    def _handle_pp_progress(self, sent, total_sent, total_size):
        self.progress_bar.update(total_sent)

    def _install_via_websocket(self, pebble, pbw):
        with open(pbw, 'rb') as f:
            print("Installing app...")
            pebble.transport.send_packet(WebSocketInstallBundle(pbw=f.read()), target=MessageTargetPhone())
            try:
                result = pebble.read_transport_message(MessageTargetPhone, WebSocketInstallStatus, timeout=300)
            except TimeoutError:
                raise ToolError("Timed out waiting for install confirmation.")
            if result.status != WebSocketInstallStatus.StatusCode.Success:
                raise ToolError("App install failed.")
            else:
                print("App install succeeded.")
