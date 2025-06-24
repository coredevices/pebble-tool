
__author__ = 'katharine'

from datetime import datetime
import logging
import os
import os.path
import re
import subprocess
import time
import uuid
import sourcemap
import sys

from collections import OrderedDict
from libpebble2.protocol.logs import AppLogMessage, AppLogShippingControl
from libpebble2.communication.transports.websocket import MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import WebSocketPhoneAppLog, WebSocketConnectionStatusUpdate

from pebble_tool.exceptions import PebbleProjectException
from pebble_tool.sdk import add_tools_to_path
from pebble_tool.sdk.project import PebbleProject
from colorama import Fore, Back, Style
from sourcemap.exceptions import SourceMapDecodeError


logger = logging.getLogger("pebble_tool.util.logs")


class PebbleLogPrinter(object):
    colour_scheme = OrderedDict([
        # LOG_LEVEL_DEBUG_VERBOSE
        (255, Fore.CYAN),
        # LOG_LEVEL_DEBUG
        (200, Fore.MAGENTA),
        # LOG_LEVEL_INFO
        (100, ""),
        # LOG_LEVEL_WARNING
        (50, Style.BRIGHT + Fore.RED),
        # LOG_LEVEL_ERROR
        (1, Back.RED + Style.BRIGHT + Fore.WHITE),
        # LOG_LEVEL_ALWAYS
        (0, None)])
    phone_colour = None

    def __init__(self, pebble, force_colour=None):
        """
        :param pebble: libpebble2.communication.PebbleConnection
        :param force_colour: Bool
        """
        self.pebble = pebble
        self.print_with_colour = force_colour if force_colour is not None else sys.stdout.isatty()
        pebble.send_packet(AppLogShippingControl(enable=True))
        self.handles = []
        self.handles.append(pebble.register_endpoint(AppLogMessage, self.handle_watch_log))
        self.handles.append(pebble.register_transport_endpoint(MessageTargetPhone, WebSocketPhoneAppLog,
                                                               self.handle_phone_log))
        self.handles.append(pebble.register_transport_endpoint(MessageTargetPhone, WebSocketConnectionStatusUpdate,
                                                               self.handle_connection))
        self.sourcemap = self._load_js_sourcemap()
        add_tools_to_path()

    def _load_js_sourcemap(self):
        sourcemap_path = "build/pebble-js-app.js.map"
        if not os.path.exists(sourcemap_path):
            return None
        with open(sourcemap_path) as sourcemap_file:
            try:
                return sourcemap.load(sourcemap_file)
            except SourceMapDecodeError as e:
                logger.warning('Found %s, but failed to parse it: %s', sourcemap_path, str(e))
                return None

    def _sourcemap_translate_js_log(self, logstr):
        if not self.sourcemap:
            return logstr

        def sourcemap_replacer(matchobj):
            d = matchobj.groupdict()
            line = int(d['line'])
            column = int(d['column']) if d['column'] is not None else 0
            try:
                token = self.sourcemap.lookup(line - 1, column)  # sourcemap wants zero-based lines numbers!
                return "{}:{}:{}".format(token.src, token.src_line + 1, token.src_col)
            except IndexError:
                return "???:?:?"

        try:
            return re.sub(r"(:?file://[\w\/\.-]+)?pebble-js-app\.js((:?\:)(?P<line>\d+))((:?\:)(?P<column>\d+))?",
                          sourcemap_replacer, logstr, flags=re.MULTILINE)
        except IndexError:
            return logstr


    def _print(self, packet, message):
        colour = self._get_colour(packet)
        message_string = message
        if colour:
            message_string = colour + message_string + Style.RESET_ALL
        sys.stdout.write(message_string + '\n')
        sys.stdout.flush()

    def _get_colour(self, packet):
        colour = None
        if self.print_with_colour:
            if isinstance(packet, WebSocketPhoneAppLog):
                colour = self.phone_colour
            else:
                # Select the next lowest level if the exact level is not in the color scheme
                colour = next(self.colour_scheme[level] for level in self.colour_scheme if packet.level >= level)
        return colour

    def wait(self):
        try:
            while self.pebble.connected:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
            return
        else:
            print("Disconnected.")

    def stop(self):
        for handle in self.handles:
            self.pebble.unregister_endpoint(handle)
        self.pebble.send_packet(AppLogShippingControl(enable=False))

    def handle_watch_log(self, packet):
        assert isinstance(packet, AppLogMessage)

        # We do actually know the original timestamp of the log (it's in packet.timestamp), but if we
        # use it that it meshes oddly with the JS logs, which must use the user's system time.
        self._print(packet, "[{}] {}:{}> {}".format(datetime.now().strftime("%H:%M:%S"), packet.filename,
                                                    packet.line_number, packet.message))
        self._maybe_handle_crash(packet)

    def handle_phone_log(self, packet):
        assert isinstance(packet, WebSocketPhoneAppLog)
        logstr = self._sourcemap_translate_js_log(packet.payload)
        self._print(packet, "[{}] pkjs> {}".format(datetime.now().strftime("%H:%M:%S"), logstr))

    def handle_connection(self, packet):
        assert isinstance(packet, WebSocketConnectionStatusUpdate)
        if packet.status == WebSocketConnectionStatusUpdate.StatusCode.Connected:
            self.pebble.send_packet(AppLogShippingControl(enable=True))

    def _maybe_handle_crash(self, packet):
        result = re.search(r"(App|Worker) fault! {([0-9a-f-]{36})} PC: (\S+) LR: (\S+)", packet.message)
        if result is None:
            return
        crash_uuid = uuid.UUID(result.group(2))
        try:
            project = PebbleProject()
        except PebbleProjectException:
            self._print(packet, "Crashed, but no active project available to desym.")
            return
        if crash_uuid != project.uuid:
            self._print(packet, "An app crashed, but it wasn't the active project.")
            return
        self._handle_crash(packet, result.group(1).lower(), result.group(3), result.group(4))

    def _handle_crash(self, packet, process, pc, lr):

        platform = self.pebble.watch_platform
        if platform == 'unknown':
            app_elf_path = "build/pebble-{}.elf".format(process)
        else:
            app_elf_path = "build/{}/pebble-{}.elf".format(platform, process)

        if not os.path.exists(app_elf_path):
            self._print(packet, "Could not look up debugging symbols.")
            self._print(packet, "Could not find ELF file: {}".format(app_elf_path))
            self._print(packet, "Please try rebuilding your project")
            return

        self._print(packet, self._format_register("Program Counter (PC)", pc, app_elf_path))
        self._print(packet, self._format_register("Link Register (LR)", lr, app_elf_path))

    def _format_register(self, name, address_str, elf_path):
        try:
            address = int(address_str, 16)
        except ValueError:
            result = '???'
        else:
            if address > 0x20000:
                result = '???'
            else:
                try:
                    result = subprocess.check_output(["arm-none-eabi-addr2line", address_str, "--exe",
                                                      elf_path]).strip()
                except OSError:
                    return "(lookup failed: toolchain not found)"
                except subprocess.CalledProcessError:
                    return "???"
        return "{:24}: {:10} {}".format(name, address_str, result)
