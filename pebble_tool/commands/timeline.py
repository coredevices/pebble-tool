
__author__ = 'katharine'

import argparse
import datetime
import json
import uuid

from libpebble2.communication.transports.websocket import MessageTargetPhone, WebsocketTransport
from libpebble2.communication.transports.websocket.protocol import (WebSocketTimelinePin, InsertPin, DeletePin,
                                                                    WebSocketTimelineResponse)

from .base import PebbleCommand
from pebble_tool.exceptions import ToolError, PebbleProjectException
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.util.logs import PebbleLogPrinter


class InsertPinCommand(PebbleCommand):
    """Inserts a pin into the timeline."""
    command = 'insert-pin'
    valid_connections = {'phone', 'cloudpebble', 'emulator'}

    def __call__(self, args):
        super(InsertPinCommand, self).__call__(args)
        if not isinstance(self.pebble.transport, WebsocketTransport):
            raise ToolError("insert-pin only works when connected via websocket to a phone or emulator.")

        pin_id = args.id
        if args.app_uuid is not None:
            app_uuid = args.app_uuid
        else:
            try:
                app_uuid = str(PebbleProject().uuid)
            except PebbleProjectException:
                raise ToolError("You must either use this command from a pebble project or specify --app-uuid.")

        try:
            pin = json.load(args.file)
        except ValueError as e:
            raise ToolError("Failed to parse json: {}".format(e))

        if pin_id is None:
            try:
                pin_id = str(pin['id'])
            except KeyError:
                raise ToolError("Pin id not provided and not embedded in pin.")
        else:
            if pin_id != pin.get('id', pin_id):
                raise ToolError("Embedded pin id mismatches provided pin id.")
        guid = _pin_id_to_uuid(pin_id)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        pin['guid'] = str(guid)
        # The createTime field here is a lie for updates. Ideally, we would persist a uuid -> startTime mapping.
        # In practice, I don't think it really matters (at least for pypkjs' implementation).
        pin['createTime'] = now
        pin['updateTime'] = now
        pin['topicKeys'] = []
        pin['source'] = 'sdk'
        pin['dataSource'] = 'sandbox-uuid:%s' % app_uuid

        printer = PebbleLogPrinter(self.pebble)
        self.pebble.transport.send_packet(WebSocketTimelinePin(data=InsertPin(json=json.dumps(pin))),
                                          target=MessageTargetPhone())
        result = self.pebble.read_transport_message(MessageTargetPhone, WebSocketTimelineResponse).status
        printer.stop()
        if result != WebSocketTimelineResponse.Status.Succeeded:
            raise ToolError("Sending pin failed.")

    @classmethod
    def add_parser(cls, parser):
        parser = super(InsertPinCommand, cls).add_parser(parser)
        parser.add_argument('--id', type=str, default=None, help='An arbitrary string representing an ID for the pin '
                                                                 'being added')
        parser.add_argument('--app-uuid', type=str, default=None, help="The UUID of the pin's parent app.")
        parser.add_argument('file', type=argparse.FileType(), help='Filename to use for pin json. "-" means stdin.')
        return parser


class DeletePinCommand(PebbleCommand):
    """Deletes a pin from the timeline."""
    command = 'delete-pin'

    def __call__(self, args):
        super(DeletePinCommand, self).__call__(args)
        if not isinstance(self.pebble.transport, WebsocketTransport):
            raise ToolError("insert-pin only works when connected via websocket to a phone or emulator.")

        guid = _pin_id_to_uuid(args.id)
        self.pebble.transport.send_packet(WebSocketTimelinePin(data=DeletePin(uuid=str(guid))),
                                          target=MessageTargetPhone())


    @classmethod
    def add_parser(cls, parser):
        parser = super(DeletePinCommand, cls).add_parser(parser)
        parser.add_argument('--id', type=str, required=True, help="The id of the pin to delete (provided as --id to "
                                                                  "insert-pin or as the pin's id property).")
        return parser


def _pin_id_to_uuid(pin_id):
    return uuid.uuid5(uuid.NAMESPACE_DNS, '%s.pin.developer.getpebble.com' % pin_id)
