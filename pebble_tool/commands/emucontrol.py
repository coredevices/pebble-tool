
__author__ = 'cherie'

import argparse
from libpebble2.communication.transports.websocket import MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import AppConfigCancelled, AppConfigResponse, AppConfigSetup
from libpebble2.communication.transports.websocket.protocol import WebSocketPhonesimAppConfig
from libpebble2.communication.transports.websocket.protocol import WebSocketPhonesimConfigResponse, WebSocketRelayQemu
from libpebble2.communication.transports.qemu.protocol import *
from libpebble2.communication.transports.qemu import MessageTargetQemu, QemuTransport
import math
import os

from .base import PebbleCommand
from ..exceptions import ToolError
from pebble_tool.sdk.emulator import ManagedEmulatorTransport
from pebble_tool.util.browser import BrowserController


def send_data_to_qemu(transport, data):
    try:
        if isinstance(transport, ManagedEmulatorTransport):
            packet = QemuPacket(data=data)
            packet.serialise()
            transport.send_packet(WebSocketRelayQemu(protocol=packet.protocol, data=data.serialise()),
                                  target=MessageTargetPhone())
        elif isinstance(transport, QemuTransport):
            transport.send_packet(data, target=MessageTargetQemu())
        else:
            raise ToolError("This command can only be run with an emulator.")
    except IOError as e:
        raise ToolError(str(e))


class EmuAccelCommand(PebbleCommand):
    """Emulates accelerometer events."""
    command = 'emu-accel'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuAccelCommand, self).__call__(args)
        if args.motion == 'custom' and args.file is not None:
            samples = []
            for line in args.file:
                line = line.strip()
                if line:
                    sample = []
                    for x in line.split(','):
                        sample.append(int(x))
                    samples.append(QemuAccelSample(x=sample[0], y=sample[1], z=sample[2]))
        elif args.motion != 'custom':
            samples = {
                'tilt-left': [QemuAccelSample(x=-500, y=0, z=-900),
                              QemuAccelSample(x=-900, y=0, z=-500),
                              QemuAccelSample(x=-1000, y=0, z=0)],
                'tilt-right': [QemuAccelSample(x=500, y=0, z=-900),
                               QemuAccelSample(x=900, y=0, z=-500),
                               QemuAccelSample(x=1000, y=0, z=0)],
                'tilt-forward': [QemuAccelSample(x=0, y=500, z=-900),
                                 QemuAccelSample(x=0, y=900, z=-500),
                                 QemuAccelSample(x=0, y=1000, z=0)],
                'tilt-back': [QemuAccelSample(x=0, y=-500, z=-900),
                              QemuAccelSample(x=0, y=-900, z=-500),
                              QemuAccelSample(x=0, y=-1000, z=0)],
                'gravity+x': [QemuAccelSample(x=1000, y=0, z=0)],
                'gravity-x': [QemuAccelSample(x=-1000, y=0, z=0)],
                'gravity+y': [QemuAccelSample(x=0, y=1000, z=0)],
                'gravity-y': [QemuAccelSample(x=0, y=-1000, z=0)],
                'gravity+z': [QemuAccelSample(x=0, y=0, z=1000)],
                'gravity-z': [QemuAccelSample(x=0, y=0, z=-1000)],
                'none': [QemuAccelSample(x=0, y=0, z=0)]
            }[args.motion]
        else:
            raise ToolError("No accel filename or motion specified.")

        max_accel_samples = 255
        if len(samples) > max_accel_samples:
            raise ToolError("Cannot send {} samples. The max number of accel samples that can be sent at a time is "
                            "{}.".format(len(samples), max_accel_samples))
        accel_input = QemuAccel(samples=samples)
        send_data_to_qemu(self.pebble.transport, accel_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuAccelCommand, cls).add_parser(parser)
        parser.add_argument('motion',
                            choices=['tilt-left', 'tilt-right', 'tilt-forward', 'tilt-back', 'gravity+x',
                                     'gravity-x', 'gravity+y', 'gravity-y', 'gravity+z', 'gravity-z', 'none',
                                     'custom'],
                            help="The type of accelerometer motion to send to the emulator. If using an accel file, "
                                 "specify 'custom' and then specify the filename using the '--file' option")
        parser.add_argument('file', nargs='?', type=argparse.FileType('r'), default=None,
                            help="Filename of the file containing custom accel data. Each line of this text file "
                                 "should contain the comma-separated x, y, and z readings. (e.g. '-24, -88, -1032')")
        return parser


class EmuAppConfigCommand(PebbleCommand):
    """Shows the app configuration page, if one exists."""
    command = 'emu-app-config'
    valid_connections = {'emulator'}

    def __call__(self, args):
        super(EmuAppConfigCommand, self).__call__(args)
        try:
            if isinstance(self.pebble.transport, ManagedEmulatorTransport):
                self.pebble.transport.send_packet(WebSocketPhonesimAppConfig(config=AppConfigSetup()),
                                                  target=MessageTargetPhone())
                response = self.pebble.read_transport_message(MessageTargetPhone, WebSocketPhonesimConfigResponse)
            else:
                raise ToolError("App config is only supported over phonesim connections.")
        except IOError as e:
            raise ToolError(str(e))

        if args.file:
            config_url = "file://{}".format(os.path.realpath(os.path.expanduser(args.file)))
        else:
            config_url = response.config.data

        browser = BrowserController()
        browser.open_config_page(config_url, self.handle_config_close)

    def handle_config_close(self, query):
        if query == '':
            self.pebble.transport.send_packet(WebSocketPhonesimAppConfig(config=AppConfigCancelled()),
                                              target=MessageTargetPhone())
        else:
            self.pebble.transport.send_packet(WebSocketPhonesimAppConfig(config=AppConfigResponse(data=query)),
                                              target=MessageTargetPhone())

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuAppConfigCommand, cls).add_parser(parser)
        parser.add_argument('--file', help="Name of local file to use for settings page in lieu of URL specified in JS")
        return parser


class EmuBatteryCommand(PebbleCommand):
    """Sets the emulated battery level and charging state."""
    command = 'emu-battery'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuBatteryCommand, self).__call__(args)
        battery_input = QemuBattery(percent=args.percent, charging=args.charging)
        send_data_to_qemu(self.pebble.transport, battery_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuBatteryCommand, cls).add_parser(parser)
        parser.add_argument('--percent', type=int, default=80,
                            help="Set the percentage battery remaining (0 to 100) on the emulator")
        parser.add_argument('--charging', action='store_true', help="Set the Pebble emulator to charging mode")
        return parser


class EmuBluetoothConnectionCommand(PebbleCommand):
    """Sets the emulated Bluetooth connectivity state."""
    command = 'emu-bt-connection'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuBluetoothConnectionCommand, self).__call__(args)
        connected = args.connected == 'yes'
        bt_input = QemuBluetoothConnection(connected=connected)
        send_data_to_qemu(self.pebble.transport, bt_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuBluetoothConnectionCommand, cls).add_parser(parser)
        parser.add_argument('--connected', choices=['no', 'yes'], default='yes',
                            help="Set the emulator BT connection status")
        return parser


class EmuCompassCommand(PebbleCommand):
    """Sets the emulated compass heading and calibration state."""
    command = 'emu-compass'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuCompassCommand, self).__call__(args)
        calibrated = QemuCompass.Calibration.Complete
        if args.uncalibrated:
            calibrated = QemuCompass.Calibration.Uncalibrated
        elif args.calibrating:
            calibrated = QemuCompass.Calibration.Refining
        elif args.calibrated:
            pass

        try:
            max_angle_radians = 0x10000
            max_angle_degrees = 360
            heading = math.ceil(args.heading % 360 * max_angle_radians / max_angle_degrees)
        except TypeError:
            heading = None

        compass_input = QemuCompass(heading=heading, calibrated=calibrated)
        send_data_to_qemu(self.pebble.transport, compass_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuCompassCommand, cls).add_parser(parser)
        parser.add_argument('--heading', type=int, default=0, help="Set the emulator compass heading (0 to 359)")
        calib_options = parser.add_mutually_exclusive_group()
        calib_options.add_argument('--uncalibrated', action='store_true', help="Set compass to uncalibrated")
        calib_options.add_argument('--calibrating', action='store_true', help="Set compass to calibrating mode")
        calib_options.add_argument('--calibrated', action='store_true', help="Set compass to calibrated")
        return parser


class EmuControlCommand(PebbleCommand):
    """Control emulator interactively"""
    command = 'emu-control'
    valid_connections = {'emulator'}

    def __call__(self, args):
        super(EmuControlCommand, self).__call__(args)
        browser = BrowserController()
        browser.serve_sensor_page(self.pebble.transport.pypkjs_port, args.port)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuControlCommand, cls).add_parser(parser)
        parser.add_argument('--port', type=int, help="Specific port to use for launching the sensor page")
        return parser


class EmuTapCommand(PebbleCommand):
    """Emulates a tap."""
    command = 'emu-tap'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuTapCommand, self).__call__(args)
        direction = 1 if args.direction.endswith('+') else -1

        if args.direction.startswith('x'):
            axis = QemuTap.Axis.X
        elif args.direction.startswith('y'):
            axis = QemuTap.Axis.Y
        elif args.direction.startswith('z'):
            axis = QemuTap.Axis.Z
        else:
            raise ToolError("Nice try, but Pebble doesn't operate in 4-D space.")

        tap_input = QemuTap(axis=axis, direction=direction)
        send_data_to_qemu(self.pebble.transport, tap_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuTapCommand, cls).add_parser(parser)
        parser.add_argument('--direction', choices=['x+', 'x-', 'y+', 'y-', 'z+', 'z-'], default='x+',
                            help="Set the direction of the accel tap in the emulator")
        return parser


class EmuTimeFormatCommand(PebbleCommand):
    """Sets the emulated time format (12h or 24h)."""
    command = 'emu-time-format'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuTimeFormatCommand, self).__call__(args)
        if args.format == "24h":
            is_24_hour = True
        elif args.format == "12h":
            is_24_hour = False
        else:
            raise ToolError("Invalid time format.")
        time_format_input = QemuTimeFormat(is_24_hour=is_24_hour)
        send_data_to_qemu(self.pebble.transport, time_format_input)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuTimeFormatCommand, cls).add_parser(parser)
        parser.add_argument('--format', choices=['12h', '24h'],
                            help="Set the time format of the emulator")
        return parser


class EmuSetTimelinePeekCommand(PebbleCommand):
    command = 'emu-set-timeline-quick-view'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuSetTimelinePeekCommand, self).__call__(args)
        peek = (args.state == 'on')
        send_data_to_qemu(self.pebble.transport, QemuTimelinePeek(enabled=peek))

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuSetTimelinePeekCommand, cls).add_parser(parser)
        parser.add_argument('state', choices=['on', 'off'], help="Set whether a timeline quick view is visible.")


class EmuSetContentSizeCommand(PebbleCommand):
    command = 'emu-set-content-size'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuSetContentSizeCommand, self).__call__(args)
        sizes = {
            'small': QemuContentSize.ContentSize.Small,
            'medium': QemuContentSize.ContentSize.Medium,
            'large': QemuContentSize.ContentSize.Large,
            'x-large': QemuContentSize.ContentSize.ExtraLarge,
        }
        if self.pebble.firmware_version < (4, 2, 0):
            raise ToolError("Content size is only supported by firmware version 4.2 or later.")
        if isinstance(self.pebble.transport, ManagedEmulatorTransport):
            platform = self.pebble.transport.platform
            if platform == 'emery':
                if args.size == 'small':
                    raise ToolError("Emery does not support the 'small' content size.")
            else:
                if args.size == 'x-large':
                    raise ToolError("Only Emery supports the 'x-large' content size.")
        send_data_to_qemu(self.pebble.transport, QemuContentSize(size=sizes[args.size]))

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuSetContentSizeCommand, cls).add_parser(parser)
        parser.add_argument('size', choices=['small', 'medium', 'large', 'x-large'], help="Set the content size.")