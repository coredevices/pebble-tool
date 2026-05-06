import uuid as uuid_module

from libpebble2.services.appmessage import AppMessageService, Int32, Uint32, CString, ByteArray

from .base import PebbleCommand
from ..exceptions import ToolError, PebbleProjectException
from ..sdk.project import PebbleProject


class SendAppMessageCommand(PebbleCommand):
    """Sends an App Message key-value dictionary to the running watchapp."""
    command = 'send-app-message'
    valid_connections = {'emulator', 'qemu', 'phone', 'serial', 'cloudpebble'}

    @classmethod
    def _parse_key_value(cls, entry, flag_name):
        """Parse KEY=VALUE entry, validate key is integer. Return (int_key, str_value)."""
        try:
            key_str, value_str = entry.split('=', 1)
        except ValueError:
            raise ToolError(
                "Invalid --{} entry '{}'. Expected format: KEY=VALUE".format(flag_name, entry)
            )

        try:
            key = int(key_str)
        except ValueError:
            raise ToolError(
                "Invalid key '{}' in --{} entry '{}'. Key must be an integer.".format(key_str, flag_name, entry)
            )

        return key, value_str

    @classmethod
    def _parse_bytes_file(cls, entry):
        """Parse a KEY=FILEPATH entry, read the file, and return (int_key, ByteArray)."""
        try:
            key_str, filepath = entry.split('=', 1)
        except ValueError:
            raise ToolError(
                "Invalid --bytes-file entry '{}'. Expected format: KEY=FILEPATH".format(entry)
            )

        try:
            key = int(key_str)
        except ValueError:
            raise ToolError(
                "Invalid key '{}' in --bytes-file entry '{}'. Key must be an integer.".format(key_str, entry)
            )

        try:
            with open(filepath, 'rb') as fh:
                data = fh.read()
        except OSError as e:
            raise ToolError("Could not read bytes file '{}': {}".format(filepath, e))

        return key, ByteArray(data)

    def __call__(self, args):
        super(SendAppMessageCommand, self).__call__(args)
        int_entries = args.int_entries or []
        uint_entries = args.uint_entries or []
        string_entries = args.string_entries or []
        bytes_entries = args.bytes_entries or []
        bytes_files = args.bytes_file or []

        if not any([int_entries, uint_entries, string_entries, bytes_entries, bytes_files]):
            raise ToolError("At least one typed flag (--int, --uint, --string, --bytes) or --bytes-file entry is required.")

        dictionary = {}

        for entry in int_entries:
            key, value_str = self._parse_key_value(entry, 'int')
            try:
                dictionary[key] = Int32(int(value_str))
            except ValueError:
                raise ToolError("Invalid int value '{}' in --int entry '{}'.".format(value_str, entry))

        for entry in uint_entries:
            key, value_str = self._parse_key_value(entry, 'uint')
            try:
                dictionary[key] = Uint32(int(value_str))
            except ValueError:
                raise ToolError("Invalid uint value '{}' in --uint entry '{}'.".format(value_str, entry))

        for entry in string_entries:
            key, value_str = self._parse_key_value(entry, 'string')
            dictionary[key] = CString(value_str)

        for entry in bytes_entries:
            key, value_str = self._parse_key_value(entry, 'bytes')
            try:
                dictionary[key] = ByteArray(bytes.fromhex(value_str))
            except ValueError:
                raise ToolError("Invalid hex bytes value '{}' in --bytes entry '{}'.".format(value_str, entry))

        for entry in bytes_files:
            key, typed_value = self._parse_bytes_file(entry)
            dictionary[key] = typed_value

        if args.app_uuid is not None:
            app_uuid = args.app_uuid
        else:
            try:
                app_uuid = str(PebbleProject().uuid)
            except PebbleProjectException:
                raise ToolError("You must either use this command from a pebble project or specify --app-uuid.")

        try:
            target_uuid = uuid_module.UUID(app_uuid)
        except ValueError:
            raise ToolError("Invalid UUID format: '{}'".format(app_uuid))

        service = AppMessageService(self.pebble)
        try:
            service.send_message(target_uuid, dictionary)
        except IOError as e:
            raise ToolError(str(e))
        finally:
            service.shutdown()

    @classmethod
    def add_parser(cls, parser):
        parser = super(SendAppMessageCommand, cls).add_parser(parser)
        parser.add_argument(
            '--int', nargs='+', dest='int_entries', metavar='KEY=VALUE',
            help="Send a signed 32-bit integer, e.g. --int 1=42 2=-10"
        )
        parser.add_argument(
            '--uint', nargs='+', dest='uint_entries', metavar='KEY=VALUE',
            help="Send an unsigned 32-bit integer, e.g. --uint 1=100"
        )
        parser.add_argument(
            '--string', nargs='+', dest='string_entries', metavar='KEY=VALUE',
            help="Send a C string, e.g. --string 2=hello"
        )
        parser.add_argument(
            '--bytes', nargs='+', dest='bytes_entries', metavar='KEY=HEXVALUE',
            help="Send raw bytes as hex, e.g. --bytes 3=DEADBEEF"
        )
        parser.add_argument(
            '--bytes-file', nargs='+', metavar='KEY=FILEPATH',
            help="Send raw bytes from a file, e.g. --bytes-file 4=data.bin"
        )
        parser.add_argument(
            '--app-uuid', type=str, default=None,
            help="UUID of the target watchapp."
        )
        return parser
