
__author__ = 'cherie'

import datetime
import time

from libpebble2.communication.transports.websocket import MessageTargetPhone, WebsocketTransport
from libpebble2.communication.transports.websocket.protocol import AppConfigCancelled, AppConfigResponse, AppConfigSetup
from libpebble2.communication.transports.websocket.protocol import WebSocketPhonesimAppConfig
from libpebble2.communication.transports.websocket.protocol import WebSocketPhonesimConfigResponse, WebSocketRelayQemu
from libpebble2.communication.transports.qemu.protocol import *
from libpebble2.communication.transports.qemu import MessageTargetQemu, QemuTransport
from libpebble2.protocol.system import TimeMessage, SetUTC
import math
import os
import struct

from .base import PebbleCommand
from ..exceptions import ToolError
from pebble_tool.sdk.emulator import ManagedEmulatorTransport
from pebble_tool.util.browser import BrowserController


def send_data_to_qemu(transport, data):
    try:
        if isinstance(transport, WebsocketTransport):
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
            try:
                accel_file = open(args.file)
            except OSError as e:
                raise ToolError("Failed to open {}: {}".format(args.file, e))
            with accel_file:
                for line in accel_file:
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
        parser.add_argument('file', nargs='?', default=None,
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


class EmuSetTimeCommand(PebbleCommand):
    """Sets the emulated watch time."""
    command = 'emu-set-time'
    valid_connections = {'qemu', 'emulator'}

    @classmethod
    def _send_time_packet(cls, pebble, ts, use_utc):
        if use_utc:
            pebble.send_packet(TimeMessage(message=SetUTC(unix_time=ts, utc_offset=0, tz_name="UTC")))
            return

        is_dst = time.localtime(ts).tm_isdst and time.daylight
        tz_offset_seconds = -time.altzone if is_dst else -time.timezone
        tz_offset_minutes = int(tz_offset_seconds // 60)
        tz_name = "UTC{:+d}".format(int(tz_offset_minutes // 60))
        pebble.send_packet(
            TimeMessage(
                message=SetUTC(
                    unix_time=ts,
                    utc_offset=tz_offset_minutes,
                    tz_name=tz_name,
                )
            )
        )

    @classmethod
    def _parse_input_time(cls, raw_value, use_utc):
        value = raw_value.strip()
        if value.isdigit():
            return int(value)

        try:
            hour, minute, second = [int(part) for part in value.split(":", 2)]
        except (TypeError, ValueError):
            raise ToolError("Invalid time '{}'. Use HH:MM:SS or Unix UTC seconds.".format(raw_value))

        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ToolError("Invalid time '{}'. Use HH:MM:SS or Unix UTC seconds.".format(raw_value))

        if use_utc:
            now = datetime.datetime.utcnow()
            dt = datetime.datetime(now.year, now.month, now.day, hour, minute, second, tzinfo=datetime.timezone.utc)
        else:
            now = datetime.datetime.now()
            dt = datetime.datetime(now.year, now.month, now.day, hour, minute, second)
        return int(dt.timestamp())

    def __call__(self, args):
        super(EmuSetTimeCommand, self).__call__(args)
        ts = self._parse_input_time(args.time, args.utc)
        self._send_time_packet(self.pebble, ts, args.utc)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuSetTimeCommand, cls).add_parser(parser)
        parser.add_argument(
            "time",
            help="Target time as HH:MM:SS (today) or Unix UTC seconds.",
        )
        parser.add_argument(
            "--utc",
            action="store_true",
            default=False,
            help="Interpret HH:MM:SS as UTC (default: local time). Ignored for Unix seconds.",
        )
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
        return parser


class EmuButtonCommand(PebbleCommand):
    """Press buttons on the emulator."""
    command = 'emu-button'
    valid_connections = {'qemu', 'emulator'}

    BUTTON_MAP = {
        'back': QemuButton.Button.Back,
        'up': QemuButton.Button.Up,
        'select': QemuButton.Button.Select,
        'down': QemuButton.Button.Down,
    }

    def __call__(self, args):
        super(EmuButtonCommand, self).__call__(args)

        # Handle 'release' action (no buttons needed)
        if args.action == 'release':
            send_data_to_qemu(self.pebble.transport, QemuButton(state=0))
            return

        # Validate buttons provided for click/push
        if not args.buttons:
            raise ToolError("At least one button required for '{}' action.".format(args.action))

        # Validate button names and calculate bitmask
        state = 0
        for btn in args.buttons:
            if btn not in self.BUTTON_MAP:
                raise ToolError("Invalid button '{}'. Valid buttons: back, up, select, down".format(btn))
            state |= self.BUTTON_MAP[btn]

        # Execute action with optional repeat
        for i in range(args.repeat):
            if i > 0:
                time.sleep(args.interval / 1000.0)

            if args.action == 'push':
                send_data_to_qemu(self.pebble.transport, QemuButton(state=state))
            elif args.action == 'click':
                send_data_to_qemu(self.pebble.transport, QemuButton(state=state))
                time.sleep(args.duration / 1000.0)
                send_data_to_qemu(self.pebble.transport, QemuButton(state=0))

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuButtonCommand, cls).add_parser(parser)
        parser.add_argument('action', choices=['click', 'push', 'release'],
                            help="Action: click (press+release), push (hold down), release (let go)")
        parser.add_argument('buttons', nargs='*', metavar='BUTTON',
                            help="Button(s): back, up, select, down")
        parser.add_argument('--duration', '-d', type=int, default=100,
                            help="Duration in ms for click (default: 100)")
        parser.add_argument('--repeat', '-n', type=int, default=1,
                            help="Number of times to repeat (default: 1)")
        parser.add_argument('--interval', '-i', type=int, default=200,
                            help="Interval in ms between repeats (default: 200)")
        return parser


# Health value injection (QEMU health protocols). These protocols are not part of
# libpebble2's QemuPacket union, so the payloads are framed and sent with an explicit
# protocol id rather than via send_data_to_qemu().

# QEMU protocol ids (must match src/fw/drivers/qemu/qemu_serial.h)
QEMU_PROTOCOL_HEALTH_METRIC = 12
QEMU_PROTOCOL_HEART_RATE = 13

# QemuHealthMetric ids (must match QemuHealthMetric in qemu_serial.h)
HEALTH_METRIC_STEPS = 0
HEALTH_METRIC_ACTIVE_SECONDS = 1
HEALTH_METRIC_RESTING_CALORIES = 2
HEALTH_METRIC_ACTIVE_CALORIES = 3
HEALTH_METRIC_DISTANCE_METERS = 4
HEALTH_METRIC_SLEEP_TOTAL_SECONDS = 5
HEALTH_METRIC_SLEEP_RESTFUL_SECONDS = 6

# HRMQuality values (must match HRMQuality in include/pbl/services/hrm/hrm_manager.h)
HRM_QUALITY = {
    'off-wrist': -1,
    'worst': 0,
    'poor': 1,
    'acceptable': 2,
    'good': 3,
    'excellent': 4,
}


def send_raw_to_qemu(transport, protocol, payload):
    """Send a pre-serialised QEMU payload using an explicit protocol id.

    Used for QEMU protocols that aren't registered in libpebble2's QemuPacket union.
    """
    try:
        if isinstance(transport, WebsocketTransport):
            transport.send_packet(WebSocketRelayQemu(protocol=protocol, data=payload),
                                  target=MessageTargetPhone())
        elif isinstance(transport, QemuTransport):
            transport.send_packet(payload, target=MessageTargetQemu(protocol=protocol, raw=True))
        else:
            raise ToolError("This command can only be run with an emulator.")
    except IOError as e:
        raise ToolError(str(e))


def send_health_metric(transport, metric_id, value):
    # QemuProtocolHealthMetricHeader: uint8_t metric; int32_t value (big-endian)
    try:
        payload = struct.pack('>Bi', metric_id, int(value))
    except struct.error:
        raise ToolError("Value {} is out of range (must fit in a signed 32-bit integer)".format(value))
    send_raw_to_qemu(transport, QEMU_PROTOCOL_HEALTH_METRIC, payload)


class EmuStepsCommand(PebbleCommand):
    """Sets the step count for the current day in the emulator."""
    command = 'emu-steps'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuStepsCommand, self).__call__(args)
        if args.count < 0:
            raise ToolError("Step count must not be negative.")
        send_health_metric(self.pebble.transport, HEALTH_METRIC_STEPS, args.count)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuStepsCommand, cls).add_parser(parser)
        parser.add_argument('count', type=int, help="Step count to set for the current day")
        return parser


class EmuDistanceCommand(PebbleCommand):
    """Sets the distance walked (meters) for the current day in the emulator."""
    command = 'emu-distance'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuDistanceCommand, self).__call__(args)
        if args.meters < 0:
            raise ToolError("Distance must not be negative.")
        send_health_metric(self.pebble.transport, HEALTH_METRIC_DISTANCE_METERS, args.meters)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuDistanceCommand, cls).add_parser(parser)
        parser.add_argument('meters', type=int, help="Distance in meters to set for the current day")
        return parser


class EmuCaloriesCommand(PebbleCommand):
    """Sets the active (and optionally resting) calories for the current day in the emulator."""
    command = 'emu-calories'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuCaloriesCommand, self).__call__(args)
        if args.active < 0 or (args.resting is not None and args.resting < 0):
            raise ToolError("Calories must not be negative.")
        send_health_metric(self.pebble.transport, HEALTH_METRIC_ACTIVE_CALORIES, args.active)
        if args.resting is not None:
            send_health_metric(self.pebble.transport, HEALTH_METRIC_RESTING_CALORIES, args.resting)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuCaloriesCommand, cls).add_parser(parser)
        parser.add_argument('active', type=int, help="Active calories (kcal) for the current day")
        parser.add_argument('--resting', type=int, default=None,
                            help="Resting calories (kcal) for the current day")
        return parser


class EmuActiveTimeCommand(PebbleCommand):
    """Sets the active time (minutes) for the current day in the emulator."""
    command = 'emu-active-time'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuActiveTimeCommand, self).__call__(args)
        if args.minutes < 0:
            raise ToolError("Active time must not be negative.")
        # The firmware metric is in seconds; the user provides minutes.
        send_health_metric(self.pebble.transport, HEALTH_METRIC_ACTIVE_SECONDS, args.minutes * 60)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuActiveTimeCommand, cls).add_parser(parser)
        parser.add_argument('minutes', type=int, help="Active time in minutes for the current day")
        return parser


class EmuSleepCommand(PebbleCommand):
    """Sets the total (and optionally restful) sleep minutes for the current day in the emulator."""
    command = 'emu-sleep'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuSleepCommand, self).__call__(args)
        if args.total < 0 or (args.restful is not None and args.restful < 0):
            raise ToolError("Sleep duration must not be negative.")
        # The firmware metric is in seconds; the user provides minutes.
        send_health_metric(self.pebble.transport, HEALTH_METRIC_SLEEP_TOTAL_SECONDS, args.total * 60)
        if args.restful is not None:
            send_health_metric(self.pebble.transport, HEALTH_METRIC_SLEEP_RESTFUL_SECONDS,
                               args.restful * 60)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuSleepCommand, cls).add_parser(parser)
        parser.add_argument('total', type=int, help="Total sleep duration in minutes")
        parser.add_argument('--restful', type=int, default=None,
                            help="Restful (deep) sleep duration in minutes")
        return parser


class EmuHeartRateCommand(PebbleCommand):
    """Injects a heart rate reading into the emulator (emery board only)."""
    command = 'emu-heart-rate'
    valid_connections = {'qemu', 'emulator'}

    def __call__(self, args):
        super(EmuHeartRateCommand, self).__call__(args)
        if not 0 <= args.bpm <= 255:
            raise ToolError("Heart rate (bpm) must be between 0 and 255.")
        quality = HRM_QUALITY[args.quality]
        # QemuProtocolHeartRateHeader: uint8_t bpm; int8_t quality
        payload = struct.pack('>Bb', args.bpm, quality)
        send_raw_to_qemu(self.pebble.transport, QEMU_PROTOCOL_HEART_RATE, payload)

    @classmethod
    def add_parser(cls, parser):
        parser = super(EmuHeartRateCommand, cls).add_parser(parser)
        parser.add_argument('bpm', type=int, help="Heart rate in beats per minute")
        parser.add_argument('--quality', choices=sorted(HRM_QUALITY.keys()), default='excellent',
                            help="Signal quality of the reading (default: excellent)")
        return parser
