import codecs
from datetime import datetime
import logging
import os
import socket
import subprocess
import sys
import threading
import time

from libpebble2.protocol.base import PebblePacket
from libpebble2.protocol.base.types import BinaryArray
from libpebble2.protocol.logs import AppLogShippingControl

from pebble_tool.util import is_debug_build

logger = logging.getLogger("pebble_tool.util.xsbug")

XSBUG_HOST = 'localhost'
XSBUG_PORT = 5002


def is_debugger_listening(host=XSBUG_HOST, port=XSBUG_PORT):
    """Return True if something (xsbug) is already accepting connections on the port."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (socket.error, OSError):
        return False


def _xsbug_app_path():
    """Locate xsbug in the active SDK's moddable-tools directory, or None."""
    from pebble_tool.sdk import sdk_version
    from pebble_tool.util import get_persist_dir
    version = sdk_version()
    if not version:
        return None
    tools_dir = os.path.join(get_persist_dir(), "SDKs", version, "toolchain", "moddable-tools")
    for name in ("xsbug.app", "xsbug", "xsbug.exe"):
        path = os.path.join(tools_dir, name)
        if os.path.exists(path):
            return path
    return None


def launch_xsbug():
    """Launch the xsbug GUI debugger if it isn't already listening.

    Opens the project's `src/embeddedjs` source folder in xsbug (if present) so
    breakpoints map to the app's JavaScript. No-op if xsbug is already up.
    Best-effort on non-macOS platforms.
    """
    if is_debugger_listening():
        return
    path = _xsbug_app_path()
    if not path:
        logger.warning("Could not find xsbug in the active SDK; please launch it manually "
                       "so the JavaScript debugger can connect.")
        return
    # Pass the JS source folder so xsbug shows it in the Files pane (xsbug's
    # onOpenFile handles a directory). Skip if it isn't there.
    js_dir = os.path.abspath(os.path.join('src', 'embeddedjs'))
    if not os.path.isdir(js_dir):
        js_dir = None
    try:
        if sys.platform == 'darwin':
            # `open -a <app>` launches the bundle detached; a trailing path is
            # delivered to xsbug's open handler.
            cmd = ["open", "-a", path] + ([js_dir] if js_dir else [])
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([path] + ([js_dir] if js_dir else []),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        print("Launching xsbug JavaScript debugger...")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Failed to launch xsbug (%s); please launch it manually.", e)


def wait_for_listener(timeout=15):
    """Block until xsbug's TCP server is accepting connections, or timeout elapses.

    Returns True if the debugger became reachable, so the Alloy app's debugger
    connection can be established as soon as it boots.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_debugger_listening():
            return True
        time.sleep(0.25)
    return is_debugger_listening()


def should_debug_moddable():
    """True iff cwd is a moddable project built with --debug (marker present).

    Used to decide whether to auto-launch xsbug and bridge the JS debugger.
    """
    if not is_debug_build():
        return False
    try:
        from pebble_tool.sdk.project import PebbleProject
        project = PebbleProject()
    except Exception:
        return False
    return getattr(project, 'project_type', None) == 'moddable'

class XsbugCtrlMessage(PebblePacket):
    class Meta:
        endpoint = 51967
        endianness = '<'

    payload = BinaryArray()


class XsbugCtrlPrinter(object):
    def __init__(self, pebble):
        self.pebble = pebble
        self.socket = None
        self.running = False
        self.read_thread = None
        self.pending = []
        self._watch_decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self._debugger_decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.handles = []
        self.handles.append(pebble.register_endpoint(XsbugCtrlMessage, self.handle_message))
        # The firmware only keeps kModdableCreationFlagDebug while an app-log listener
        # is connected (app_log_is_bt_enabled()), so enable shipping here. Without this
        # the watch strips the debug flag and never opens a debugger connection,
        # leaving xsbug blank (it only worked when run alongside `--logs`).
        pebble.send_packet(AppLogShippingControl(enable=True))

    def _connect_debugger(self):
        if self.socket is not None:
            return True
        try:
            # print("Alloy: connecting to debugger at {}:{}...".format(XSBUG_HOST, XSBUG_PORT))
            self.socket = socket.create_connection((XSBUG_HOST, XSBUG_PORT), timeout=5)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.socket.settimeout(0.5)
            print("Alloy: connected to JavaScript debugger")
            if self.pending:
                for data in self.pending:
                    self.socket.sendall(data)
                # print("Alloy: sent {} buffered message(s)".format(len(self.pending)))
                self.pending = []
            self.running = True
            self.read_thread = threading.Thread(target=self._read_from_debugger)
            self.read_thread.daemon = True
            self.read_thread.start()
            return True
        except socket.error as e:
            print("Alloy: failed to connect to debugger: {}".format(e))
            self._send_logout()
            return False

    def _read_from_debugger(self):
        # print("Alloy: read thread started, socket fd={}".format(self.socket.fileno()))
        try:
            while self.running:
                try:
                    data = self.socket.recv(4096)
                    if not data:
                        print("Alloy: debugger disconnected (recv returned empty)")
                        break
                    # print("Alloy: recv {} bytes from debugger".format(len(data)))
                    # timestamp = datetime.now().strftime("%H:%M:%S")
                    # text = self._debugger_decoder.decode(data)
                    # if text:
                    #     sys.stdout.write("[{}] xsbug debugger> {}\n".format(timestamp, text))
                    #     sys.stdout.flush()
                    # sys.stdout.flush()
                    try:
                        packet = XsbugCtrlMessage(payload=data)
                        serialized = packet.serialise()
                        # print("Alloy: sending {} bytes to watch (serialized {})".format(len(data), len(serialized)))
                        self.pebble.send_packet(packet)
                        # print("Alloy: send_packet completed")
                    except Exception as e:
                        print("Alloy: send_packet failed: {} ({})".format(e, type(e).__name__))
                except socket.timeout:
                    # sys.stdout.write(".")
                    # sys.stdout.flush()
                    continue
                except socket.error as e:
                    if self.running:
                        print("Alloy: read socket error: {}".format(e))
                    break
        except Exception as e:
            print("Alloy: read thread exception: {} ({})".format(e, type(e).__name__))
        self._send_logout()
        # print("Alloy: read thread exiting")

    def handle_message(self, packet):
        assert isinstance(packet, XsbugCtrlMessage)
        data = bytes(packet.payload)
        null_index = data.find(b'\x00')
        if null_index >= 0:
            if null_index > 0:
                self._relay_to_debugger(data[:null_index])
            self._disconnect_debugger()
            return
        self._relay_to_debugger(data)

    def _relay_to_debugger(self, data):
        # timestamp = datetime.now().strftime("%H:%M:%S")
        # text = self._watch_decoder.decode(data)
        # if text:
        #     sys.stdout.write("[{}] xsbug watch> {}\n".format(timestamp, text))
        #     sys.stdout.flush()
        if self.socket is None and not self._connect_debugger():
            self.pending.append(data)
            return
        try:
            self.socket.sendall(data)
        except socket.error as e:
            print("Alloy: failed to send to debugger: {}".format(e))
            self._close_socket()
            self._send_logout()

    def _send_logout(self):
        try:
            self.pebble.send_packet(XsbugCtrlMessage(payload=b"<logout/>"))
            # print("Alloy: sent logout to watch")
        except Exception as e:
            print("Alloy: failed to send logout: {}".format(e))

    def _disconnect_debugger(self):
        self.running = False
        self._close_socket()
        if self.read_thread:
            self.read_thread.join(timeout=2)
            self.read_thread = None
        self.pending = []
        self._watch_decoder.reset()
        self._debugger_decoder.reset()
        # print("Alloy: debug session ended, ready for new connection")

    def _close_socket(self):
        if self.socket:
            try:
                self.socket.close()
            except socket.error:
                pass
            self.socket = None

    def wait(self):
        import time
        try:
            while self.pebble.connected:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
            return
        else:
            print("Disconnected.")

    def stop(self):
        self.running = False
        for handle in self.handles:
            self.pebble.unregister_endpoint(handle)
        self._close_socket()
        if self.read_thread:
            self.read_thread.join(timeout=2)
