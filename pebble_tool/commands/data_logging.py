

import os
import os.path

from libpebble2.communication.transports.websocket import WebsocketTransport, MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import (
      WebSocketInstallBundle, WebSocketInstallStatus)
from libpebble2.exceptions import TimeoutError
from libpebble2.services.data_logging import DataLoggingService

from .base import PebbleCommand
from ..util.logs import PebbleLogPrinter
from ..exceptions import ToolError


class DataLoggingCommand(PebbleCommand):
    """Get info on or download data logging data"""
    command = 'data-logging'

    def _print_send_enable_status(self, data_logging_service):
        enabled = data_logging_service.get_send_enable()
        status = "ENABLED" if enabled else "DISABLED"
        print("Sending of sessions from watch to phone is {}".format(status))


    def __call__(self, args):
        super(DataLoggingCommand, self).__call__(args)

        data_logging_service = DataLoggingService(self.pebble)

        if args.command == 'list':
            listing = data_logging_service.list()
            if len(listing) > 0:
                for key in list(listing[0].keys()):
                    print("{:<20} ".format(key), end="")
                print()
                print("-" * 20 * len(listing[0]))

                for item in listing:
                    for value in list(item.values()):
                        print("{:<20} ".format(value), end="")
                    print()
            else:
                print("No data logging sessions found")

        elif args.command == 'download':
            was_enabled = data_logging_service.get_send_enable()
            if not was_enabled:
                data_logging_service.set_send_enable(True)
                assert data_logging_service.get_send_enable()

            session, data = data_logging_service.download(session_id=args.session_id)
            if session is None:
                print("Session {} not found".format(args.session_id))
            else:
                if data:
                    filename = args.filename
                    if filename is None:
                        filename = "session_{}.bin".format(args.session_id)
                    with open(filename, "ab") as f:
                        f.write(data)
                    print("Saved {} items of size {} to file {}".format(
                          len(data) / session.data_item_size, session.data_item_size, filename))

                else:
                    print("Session {} has no data".format(args.session_id))

            if not was_enabled:
                data_logging_service.set_send_enable(False)
                assert not data_logging_service.get_send_enable()


        elif args.command == 'get-sends-enabled':
            enabled = data_logging_service.get_send_enable()
            status = "ENABLED" if enabled else "DISABLED"
            print("Sending of sessions from watch to phone is {}".format(status))

        elif args.command == 'enable-sends':
            data_logging_service.set_send_enable(True)
            self._print_send_enable_status(data_logging_service)

        elif args.command == 'disable-sends':
            data_logging_service.set_send_enable(False)
            self._print_send_enable_status(data_logging_service)



    @classmethod
    def add_parser(cls, parser):
        parser = super(DataLoggingCommand, cls).add_parser(parser)
        parser.add_argument('command',
                            choices=['list', 'download', 'get-sends-enabled', 'enable-sends',
                            'disable-sends'],
                            help="Which action to perform. 'list': list all sessions, "
                            "'download': download session data, 'get-sends-enabled': check if "
                            "watch is set to automatically send session data to the phone, "
                            "'enable-sends': enable automatic sends of session data to phone, "
                            "'disable-sends': disable automatic sends of session data to phone")
        parser.add_argument('filename', nargs='?', type=str, help="Filename of download")
        parser.add_argument('--session-id', type=int, default=-1,
                            help="Which session to download, if the download command is specified")
        return parser
