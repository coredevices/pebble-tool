import websocket

from ..exceptions import ToolError
from pebble_tool.sdk.emulator import ManagedEmulatorTransport


def request(transport, opcode, payload=b''):
    """Send one command to the emulator's pypkjs and return the payload of its reply.

    Uses a websocket of its own: libpebble2's connection doesn't know pypkjs' newer opcodes. pypkjs
    replies with the opcode and 0x00 (then any result) on success, or 0x01 and an error message.
    """
    if not isinstance(transport, ManagedEmulatorTransport):
        raise ToolError("This command only works with the emulator.")
    ws = websocket.create_connection('ws://localhost:{}/'.format(transport.pypkjs_port), timeout=10)
    try:
        ws.send_binary(bytes([opcode]) + payload)
        reply = ws.recv()
        while not (isinstance(reply, bytes) and reply[:1] == bytes([opcode])):
            reply = ws.recv()
    except websocket.WebSocketTimeoutException:
        raise ToolError("The emulator didn't answer. Its pypkjs may be too old for this command.")
    finally:
        ws.close()
    if reply[1:2] != b'\x00':
        raise ToolError(reply[2:].decode('utf-8') or "The emulator refused.")
    return reply[2:]
