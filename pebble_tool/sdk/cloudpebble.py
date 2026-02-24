
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

# Temporary local proxy defaults for v1/v2 development.
CP_TRANSPORT_HOST = os.environ.get('CP_TRANSPORT_HOST', 'ws://localhost:3210/tool')
CP_TRANSPORT_HOST_V2 = os.environ.get('CP_TRANSPORT_HOST_V2', 'ws://localhost:3210/tool-v2')

logger = logging.getLogger("pebble_tool.sdk.cloudpebble")


class CloudPebbleTransport(WebsocketTransport):
    def __init__(self):
        super(CloudPebbleTransport, self).__init__(None)
        self._phone_connected = False

    def connect(self):
        account = get_default_account()
        if not account.is_logged_in:
            raise ToolError("You must be logged in ('pebble login') to use the CloudPebble connection.")
        host = self._get_transport_host(account)
        print("CloudPebble proxy host: {}".format(host))
        print("CloudPebble auth mode: {}".format("firebase-v2" if self._is_firebase_account(account) else "legacy-v1"))
        self.ws = websocket.create_connection(host)
        print("Connected to CloudPebble proxy websocket.")
        self._authenticate(account)
        self._wait_for_phone()
        self._phone_connected = True

    @property
    def connected(self):
        return super(CloudPebbleTransport, self).connected and self._phone_connected

    def _is_firebase_account(self, account):
        return account.__module__.endswith("firebase_account")

    def _get_transport_host(self, account):
        if self._is_firebase_account(account):
            return CP_TRANSPORT_HOST_V2
        return CP_TRANSPORT_HOST

    def _send_v2_auth_frame(self, token):
        token_bytes = token.encode('utf8')
        token_length = len(token_bytes)
        if token_length > 65535:
            raise ToolError("Firebase token is too large for CloudPebble proxy v2 auth frame.")
        frame = bytearray(3 + token_length)
        frame[0] = 0x0A
        frame[1] = token_length // 256
        frame[2] = token_length % 256
        frame[3:] = token_bytes
        self.ws.send_binary(bytes(frame))

    def _authenticate(self, account):
        oauth = account.bearer_token
        if self._is_firebase_account(account):
            print("Sending v2 auth frame...")
            self._send_v2_auth_frame(oauth)
        else:
            print("Sending v1 auth frame...")
            self.send_packet(WebSocketProxyAuthenticationRequest(token=oauth), target=MessageTargetPhone())
        target, packet = self.read_packet()
        if isinstance(packet, WebSocketProxyAuthenticationResponse):
            if packet.status != WebSocketProxyAuthenticationResponse.StatusCode.Success:
                raise ToolError("Failed to authenticate to the CloudPebble proxy.")
            print("CloudPebble proxy authentication succeeded.")
        else:
            logger.info("Got unexpected message from proxy: %s", packet)
            raise ToolError("Unexpected message from CloudPebble proxy.")

    def _wait_for_phone(self):
        print("Waiting for phone to connect...")
        target, packet = self.read_packet()
        if isinstance(packet, WebSocketProxyConnectionStatusUpdate):
            print("CloudPebble connection status packet: {}".format(packet.status))
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
