__author__ = 'katharine'

from six import with_metaclass

import argparse
import logging
import os
import time

from libpebble2.communication import PebbleConnection
from libpebble2.communication.transports.qemu import QemuTransport
from libpebble2.communication.transports.websocket import WebsocketTransport
from libpebble2.communication.transports.serial import SerialTransport
from libpebble2.exceptions import ConnectionError
from libpebble2.protocol.system import TimeMessage, SetUTC

from pebble_tool.exceptions import ToolError
from pebble_tool.sdk import pebble_platforms, sdk_version
from pebble_tool.sdk.emulator import ManagedEmulatorTransport, get_all_emulator_info
from pebble_tool.sdk.cloudpebble import CloudPebbleTransport
from pebble_tool.util.analytics import post_event

_CommandRegistry = []


class SelfRegisteringCommand(type):
    def __init__(cls, name, bases, dct):
        if hasattr(cls, 'command') and cls.command is not None:
            _CommandRegistry.append(cls)
        super(SelfRegisteringCommand, cls).__init__(name, bases, dct)


class BaseCommand(with_metaclass(SelfRegisteringCommand)):
    command = None
    has_subcommands = False

    @classmethod
    def add_parser(cls, parser):
        if hasattr(cls, 'epilog'):
            epilog = cls.epilog
        elif cls.has_subcommands:
            epilog = "For help on an individual subcommand, call that command with --help."
        else:
            epilog = None
        parser = parser.add_parser(cls.command, parents=cls._shared_parser(), help=cls.__doc__, epilog=epilog,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.set_defaults(func=lambda x: cls()(x))
        return parser

    @classmethod
    def _shared_parser(cls):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('-v', action='count', default=0, help="Degree of verbosity (use more v for more verbosity)")
        return [parser]

    def __call__(self, args):
        self._set_debugging(args.v)
        post_event("invoke_command_{}".format(self.command))

    def _set_debugging(self, level):
        self._verbosity = level
        if level is not None:
            if level == 1:
                verbosity = logging.INFO
            elif level >= 2:
                verbosity = logging.DEBUG
            else:
                verbosity = logging.WARNING
            logging.getLogger().setLevel(verbosity)


class PebbleCommand(BaseCommand):
    connection_handlers = set()

    @classmethod
    def register_connection_handler(cls, impl):
        cls.connection_handlers.add(impl)

    @classmethod
    def _shared_parser(cls):
        parser = argparse.ArgumentParser(add_help=False)
        handlers = cls.valid_connection_handlers()
        # Having a group inside a mutually exclusive group breaks --help unless there are is
        # something for it to be mutually exlusive with.
        if len(handlers) > 1:
            group = parser.add_mutually_exclusive_group()
        else:
            group = parser
        for handler_impl in handlers:
            handler_impl.add_argument_handler(group)
        return super(PebbleCommand, cls)._shared_parser() + [parser]

    @classmethod
    def valid_connection_handlers(cls):
        valid_connections = getattr(cls, 'valid_connections', None)
        if not valid_connections:
            return cls.connection_handlers

        return set([handler for handler in cls.connection_handlers if handler.name in valid_connections])

    def __call__(self, args):
        super(PebbleCommand, self).__call__(args)
        try:
            self.pebble = self._connect(args)
        except ConnectionError as e:
            raise ToolError(str(e))

    def _connect(self, args):
        self._set_debugging(args.v)
        for handler_impl in self.valid_connection_handlers():
            if handler_impl.is_selected(args):
                break
        else:
            # No selected transport, fallback to a running emulator if available
            if PebbleTransportEmulator.get_running_emulators():
                handler_impl = PebbleTransportEmulator
            else:
                raise ToolError("No pebble connection specified.")

        transport = handler_impl.get_transport(args)
        connection = PebbleConnection(transport, **self._get_debug_args())
        connection.connect()
        connection.run_async()
        handler_impl.post_connect(connection)
        return connection

    def _get_debug_args(self):
        args = {}
        if self._verbosity >= 3:
            args['log_packet_level'] = logging.DEBUG
        if self._verbosity >= 4:
            args['log_protocol_level'] = logging.DEBUG
        return args


class SelfRegisteringTransportConfiguration(type):
    def __init__(cls, name, bases, dct):
        if hasattr(cls, 'name') and cls.name is not None:
            PebbleCommand.register_connection_handler(cls)
            super(SelfRegisteringTransportConfiguration, cls).__init__(name, bases, dct)


class PebbleTransportConfiguration(with_metaclass(SelfRegisteringTransportConfiguration)):
    transport_class = None
    env_var = None
    name = None

    @classmethod
    def _config_env_var(cls):
        env_var_name = cls.env_var if cls.env_var else 'PEBBLE_%s' % cls.name.upper()
        return os.environ.get(env_var_name)

    @classmethod
    def is_selected(cls, args):
        return getattr(args, cls.name, None) or cls._config_env_var()

    @classmethod
    def _connect_args(cls, args):
        arg_val = getattr(args, cls.name, None)
        if arg_val:
            return (arg_val,)

        env_val = cls._config_env_var()
        if env_val:
            return (env_val,)

    @classmethod
    def get_transport(cls, args):
        return cls.transport_class(*cls._connect_args(args))

    @classmethod
    def add_argument_handler(cls):
        raise NotImplementedError

    @classmethod
    def post_connect(cls, connection):
        pass


class PebbleTransportSerial(PebbleTransportConfiguration):
    transport_class = SerialTransport
    env_var = 'PEBBLE_BT_SERIAL'
    name = 'serial'

    @classmethod
    def add_argument_handler(cls, parser):
        parser.add_argument('--serial', type=str, help="Connected directly, given a path to a serial device.")


class PebbleTransportPhone(PebbleTransportConfiguration):
    transport_class = WebsocketTransport
    name = 'phone'

    @classmethod
    def _connect_args(cls, args):
        phone, = super(PebbleTransportPhone, cls)._connect_args(args)
        parts = phone.split(':')
        ip = parts[0]
        if len(parts) == 2:
            port = int(parts[1])
        else:
            port = 9000

        return ("ws://{}:{}/".format(ip, port),)

    @classmethod
    def add_argument_handler(cls, parser):
        parser.add_argument('--phone', metavar='phone_ip',
                            help="When using the developer connection, your phone's IP or hostname. "
                                 "Equivalent to PEBBLE_PHONE.")


class PebbleTransportQemu(PebbleTransportConfiguration):
    transport_class = QemuTransport
    name = 'qemu'

    @classmethod
    def _connect_args(cls, args):
        phone, = super(PebbleTransportQemu, cls)._connect_args(args)
        parts = phone.split(':')
        ip = parts[0]
        if len(parts) == 2:
            port = int(parts[1])
        else:
            port = 12344

        return (ip, port,)

    @classmethod
    def add_argument_handler(cls, parser):
        parser.add_argument('--qemu', nargs='?', const='localhost:12344', metavar='host',
                            help="Use this option to connect directly to a QEMU instance. "
                                 "Equivalent to PEBBLE_QEMU.")


class PebbleTransportCloudPebble(PebbleTransportConfiguration):
    transport_class = CloudPebbleTransport
    name = 'cloudpebble'

    @classmethod
    def _connect_args(cls, args):
        return ()

    @classmethod
    def add_argument_handler(cls, parser):
        parser.add_argument('--cloudpebble', action='store_true',
                           help="Use this option to connect to your phone via"
                                " the CloudPebble connection. Equivalent to "
                                "PEBBLE_CLOUDPEBBLE.")


class PebbleTransportEmulator(PebbleTransportConfiguration):
    transport_class = ManagedEmulatorTransport
    name = 'emulator'

    @classmethod
    def get_running_emulators(cls):
        running = []
        for platform, sdks in list(get_all_emulator_info().items()):
            for sdk in sdks:
                if ManagedEmulatorTransport.is_emulator_alive(platform, sdk):
                    running.append((platform, sdk))
        return running

    @classmethod
    def _connect_args(cls, args):
        emulator_platform = getattr(args, 'emulator', None)
        emulator_sdk = getattr(args, 'sdk', None)
        if emulator_platform:
            return emulator_platform, emulator_sdk
        elif 'PEBBLE_EMULATOR' in os.environ:
            emulator_platform = os.environ['PEBBLE_EMULATOR']
            if emulator_platform not in pebble_platforms:
                raise ToolError("PEBBLE_EMULATOR is set to '{}', which is not a valid platform "
                                "(pick from {})".format(emulator_platform, ', '.join(pebble_platforms)))
            emulator_sdk = os.environ.get('PEBBLE_EMULATOR_VERSION', sdk_version())
        else:
            running = cls.get_running_emulators()
            if len(running) == 1:
                emulator_platform, emulator_sdk = running[0]
            elif len(running) > 1:
                raise ToolError("Multiple emulators are running; you must specify which to use.")

        return (emulator_platform, emulator_sdk)

    @classmethod
    def post_connect(cls, connection):
        # Make sure the timezone is set usefully.
        if connection.firmware_version.major >= 3:
            ts = time.time()
            tz_offset = -time.altzone if time.localtime(ts).tm_isdst and time.daylight else -time.timezone
            tz_offset_minutes = tz_offset // 60
            tz_name = "UTC%+d" % (tz_offset_minutes / 60)
            connection.send_packet(TimeMessage(message=SetUTC(unix_time=int(ts), utc_offset=tz_offset_minutes, tz_name=tz_name)))

    @classmethod
    def add_argument_handler(cls, parser):
        emu_group = parser.add_argument_group()
        emu_group.add_argument('--emulator', type=str, help="Launch an emulator. Equivalent to PEBBLE_EMULATOR.",
                           choices=pebble_platforms)
        emu_group.add_argument('--sdk', type=str, help="SDK version to launch. Defaults to the active SDK"
                                                   " (currently {})".format(sdk_version()))

def register_children(parser):
    subparsers = parser.add_subparsers(title="command")
    for command in _CommandRegistry:
        command.add_parser(subparsers)
