
__author__ = 'katharine'

import logging
import os
import websocket

from libpebble2.communication.transports.websocket import WebsocketTransport, MessageTargetPhone
from libpebble2.communication.transports.websocket.protocol import (WebSocketProxyAuthenticationRequest,
                                                                    WebSocketProxyAuthenticationResponse,
                                                                    WebSocketProxyConnectionStatusUpdate)

from pebble_tool.account import get_default_account
from pebble_tool.exceptions import ToolError

CP_TRANSPORT_HOST = os.environ.get('CP_TRANSPORT_HOST', 'wss://cloudpebble-proxy.repebble.com/tool')

logger = logging.getLogger("pebble_tool.sdk.cloudpebble")


class CloudPebbleTransport(WebsocketTransport):
    def __init__(self):
        super(CloudPebbleTransport, self).__init__(None)
        self._phone_connected = False

    def connect(self):
        account = get_default_account()
        if not account.is_logged_in:
            raise ToolError("You must be logged in ('pebble login') to use the CloudPebble connection.")
        self.ws = websocket.create_connection(CP_TRANSPORT_HOST)
        self._authenticate()
        self._wait_for_phone()
        self._phone_connected = True

    @property
    def connected(self):
        return super(CloudPebbleTransport, self).connected and self._phone_connected

    def _authenticate(self):
        oauth = get_default_account().bearer_token
        self.send_packet(WebSocketProxyAuthenticationRequest(token=oauth), target=MessageTargetPhone())
        target, packet = self.read_packet()
        if isinstance(packet, WebSocketProxyAuthenticationResponse):
            if packet.status != WebSocketProxyAuthenticationResponse.StatusCode.Success:
                raise ToolError("Failed to authenticate to the CloudPebble proxy.")
        else:
            logger.info("Got unexpected message from proxy: %s", packet)
            raise ToolError("Unexpected message from CloudPebble proxy.")

    def _wait_for_phone(self):
        print("Waiting for phone to connect...")
        target, packet = self.read_packet()
        if isinstance(packet, WebSocketProxyConnectionStatusUpdate):
            if packet.status == WebSocketProxyConnectionStatusUpdate.StatusCode.Connected:
                print("Connected.")
                return
        raise ToolError("Unexpected message when waiting for phone connection.")

    def read_packet(self):
        target, packet = super(CloudPebbleTransport, self).read_packet()
        if isinstance(packet, WebSocketProxyConnectionStatusUpdate):
            if packet.status == WebSocketProxyConnectionStatusUpdate.StatusCode.Disconnected:
                self.ws.close()
                self._phone_connected = False
        return target, packet
