
__author__ = 'katharine'

from datetime import datetime
import logging
import os
import os.path
import re
import socket
import subprocess
import threading
import time
import uuid
import sourcemap
import sys

from collections import OrderedDict
from libpebble2.protocol.logs import AppLogMessage, AppLogShippingControl
from libpebble2.communication.transports.websocket import MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import WebSocketPhoneAppLog, WebSocketConnectionStatusUpdate

from pebble_tool.exceptions import PebbleProjectException, ToolError
from pebble_tool.sdk import add_tools_to_path
from pebble_tool.sdk.emulator import get_emulator_info
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


class QemuLogPrinter(object):
    """Prints logs from QEMU's serial port output using PULSE protocol."""
    
    PULSE_PROTOCOL_LOGGING = 0x03  # From pulse_protocol_registry.def
    FLAG = 0x55  # Frame delimiter
    CRC32_RESIDUE = 0xDEBB20E3  # binascii.crc32(b'\0' * 4) & 0xFFFFFFFF
    
    def __init__(self, pebble):
        """
        :param pebble: libpebble2.communication.PebbleConnection
        """
        self.pebble = pebble
        self.running = False
        self.socket = None
        self.thread = None
        self.input_buffer = bytearray()
        self.waiting_for_sync = True
        
        # Check if we're connected to an emulator
        from libpebble2.communication.transports.websocket import WebsocketTransport
        if not isinstance(self.pebble.transport, WebsocketTransport):
            raise ToolError("QEMU logs are only available when using the emulator.")
        
        # Get the serial port from emulator info
        platform = self.pebble.watch_platform
        info = get_emulator_info(platform)
        if info is None:
            raise ToolError("Could not find emulator info for platform: {}".format(platform))
        
        self.serial_port = info['qemu']['serial']
        self.platform = platform
        logger.debug("QEMU serial port: %d", self.serial_port)
    
    def _decode_transparency(self, frame_bytes):
        """Decode PULSE transparency encoding (COBS with 0x55 swapped for 0x00).
        
        In PULSE framing:
        1. Data is COBS encoded (0x00 bytes are removed)
        2. Then 0x55 (FLAG) bytes in the COBS output are replaced with 0x00
        
        To decode:
        1. Replace 0x00 bytes with 0x55 (FLAG)
        2. COBS decode
        """
        if not frame_bytes:
            return None
        
        try:
            from cobs import cobs
            
            # Step 1: Replace 0x00 with 0x55 (FLAG) - reverse the swap that was done during encoding
            frame_bytes = bytearray(frame_bytes).replace(b'\x00', bytearray([self.FLAG]))
            
            # Step 2: COBS decode
            decoded = cobs.decode(bytes(frame_bytes))
            
            return decoded
        except Exception as e:
            logger.debug("COBS decode error: %s", e)
            return None
    
    def _verify_crc32(self, frame_bytes):
        """Verify and strip CRC32 from frame.
        
        Returns the frame without CRC if valid, None otherwise.
        """
        if len(frame_bytes) <= 4:
            logger.debug("Frame too short for CRC32")
            return None
        
        # Calculate CRC32 over the entire frame (including the CRC bytes)
        import binascii
        crc = binascii.crc32(frame_bytes) & 0xFFFFFFFF
        
        # If CRC is correct, it should equal the residue
        if crc != self.CRC32_RESIDUE:
            logger.debug("CRC32 check failed: 0x%08x != 0x%08x", crc, self.CRC32_RESIDUE)
            return None
        
        # Return frame without the CRC bytes
        return frame_bytes[:-4]
    
    def _parse_pulse_message(self, frame):
        """Parse a PULSE protocol frame and extract log message.
        
        Frame structure (after COBS decode and CRC strip):
        This is actually wrapped in a transport layer:
        - Port (2 bytes, big-endian) - should be 0x5021
        - Protocol (2 bytes, big-endian) - should be 0x0003 for logging
        - Length (2 bytes, big-endian)
        - Payload (variable)
        """
        if len(frame) < 6:
            return None
        
        import struct
        
        # Parse transport header
        port = struct.unpack('>H', frame[0:2])[0]
        protocol = struct.unpack('>H', frame[2:4])[0]
        length = struct.unpack('>H', frame[4:6])[0]
        payload = frame[6:]
        
        logger.debug("Port: 0x%04x, Protocol: 0x%04x, Length: %d, Payload: %d bytes", 
                    port, protocol, length, len(payload))
        
        # Check if this is a logging message
        if protocol != self.PULSE_PROTOCOL_LOGGING:
            logger.debug("Not a logging message (protocol 0x%04x)", protocol)
            return None
        
        # The payload appears to contain a simpler format when using --nohash
        # Let's just extract the readable text from it
        
        # Try to find strings in the payload
        try:
            # Decode and look for the actual log message
            payload_str = payload.decode('utf-8', errors='replace')
            
            # Look for patterns like "NL:xxxx `message`" 
            import re
            
            # Extract backtick-enclosed strings
            strings = re.findall(r'`([^`]+)`', payload_str)
            if strings:
                return ' '.join(strings)
            
            # If no backticks, try to extract readable text
            # Remove null bytes and non-printable characters
            cleaned = ''.join(c for c in payload_str if c.isprintable() and c not in '\x00\r')
            if cleaned and len(cleaned) > 3:
                return cleaned.strip()
                
        except Exception as e:
            logger.debug("Error parsing payload: %s", e)
        
        return None
    
    def _process_frame(self, frame):
        """Process a complete PULSE frame."""
        if not frame:
            return
        
        # Decode transparency (COBS with 0x55/0x00 swap)
        decoded = self._decode_transparency(frame)
        if decoded is None:
            return
        
        # Skip CRC32 verification for now and just strip the last 4 bytes
        if len(decoded) <= 4:
            return
        datagram = decoded[:-4]
        
        # Parse the message
        log_line = self._parse_pulse_message(datagram)
        if log_line:
            # Filter out "DIS" messages (they're just noise)
            if log_line.strip() == "DIS":
                return
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write("[{}] qemu> {}\n".format(timestamp, log_line))
            sys.stdout.flush()
    
    def _connect(self):
        """Connect to the QEMU serial port."""
        max_retries = 10
        for i in range(max_retries):
            try:
                self.socket = socket.create_connection(('localhost', self.serial_port), timeout=5)
                logger.info("Connected to QEMU serial port on localhost:%d", self.serial_port)
                return True
            except socket.error as e:
                if i < max_retries - 1:
                    logger.debug("Failed to connect to QEMU serial port (attempt %d/%d): %s", 
                               i + 1, max_retries, e)
                    time.sleep(0.5)
                else:
                    logger.error("Could not connect to QEMU serial port after %d attempts", max_retries)
                    return False
        return False
    
    def _read_loop(self):
        """Continuously read from the serial port and print output.
        
        The PULSE protocol uses FLAG bytes (0x55) as frame delimiters.
        Frame format: FLAG + COBS_encoded_data + FLAG
        """
        while self.running:
            try:
                if self.socket is None:
                    break
                
                # Read available data
                data = self.socket.recv(1024)
                if not data:
                    # Connection closed
                    logger.debug("QEMU serial connection closed")
                    break
                
                # Process byte by byte to extract frames
                for byte in bytearray(data):
                    if self.waiting_for_sync:
                        if byte == self.FLAG:
                            self.waiting_for_sync = False
                    else:
                        if byte == self.FLAG:
                            # End of frame - process it if we have data
                            if self.input_buffer:
                                frame = bytes(self.input_buffer)
                                
                                # Process the frame
                                try:
                                    self._process_frame(frame)
                                except Exception as e:
                                    logger.debug("Error processing PULSE frame: %s", e)
                                
                                self.input_buffer = bytearray()
                        else:
                            # Accumulate frame data
                            self.input_buffer.append(byte)
                
            except socket.timeout:
                # Timeout is OK, just continue
                continue
            except socket.error as e:
                if self.running:
                    logger.debug("Socket error in QEMU log reader: %s", e)
                break
            except Exception as e:
                logger.error("Unexpected error in QEMU log reader: %s", e)
                break
        
        # Clean up
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
    
    def wait(self):
        """Start reading logs and wait until interrupted."""
        if not self._connect():
            print("Could not connect to QEMU serial port. Is the emulator running?")
            return
        
        print("Listening for QEMU serial output (Ctrl+C to stop)...")
        self.running = True
        
        # Start reading in a background thread
        self.thread = threading.Thread(target=self._read_loop)
        self.thread.daemon = True
        self.thread.start()
        
        try:
            # Wait for the thread or keyboard interrupt
            while self.running and self.thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nStopping QEMU log output...")
            self.stop()
    
    def stop(self):
        """Stop reading logs."""
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        if self.thread:
            self.thread.join(timeout=2)
