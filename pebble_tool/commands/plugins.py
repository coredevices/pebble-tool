import json

import websocket

from .base import PebbleCommand
from .emucontrol import EmuAppConfigCommand
from ..exceptions import ToolError
from pebble_tool.sdk.emulator import ManagedEmulatorTransport

PLUGIN_OPCODE = 0x0d
LIST = 0x02
REMOVE = 0x03

KIND_LABELS = {'plugin': 'plugin', 'app': "watchapp's plugin", 'builtin': 'built in'}


class PluginsCommand(PebbleCommand):
    """Lists or removes the plugins installed on the emulator."""
    command = 'plugins'
    valid_connections = {'emulator'}

    def __call__(self, args):
        super(PluginsCommand, self).__call__(args)
        if args.action == 'list':
            print_plugins(self._request(LIST))
        else:
            uuid = self._resolve(args.plugin or EmuAppConfigCommand._plugin_project_uuid())
            print("Removed {}.".format(self._request(REMOVE, uuid)['removed']))

    def _resolve(self, wanted):
        """A uuid, or the uuid of the one installed plugin with that name."""
        if wanted is None:
            raise ToolError("Say which plugin to remove: its uuid or name (see `pebble plugins list`).")
        plugins = self._request(LIST)
        named = [p for p in plugins if p['name'].lower() == wanted.lower()]
        if len(named) == 1:
            return named[0]['uuid']
        if len(named) > 1:
            raise ToolError("More than one plugin is called {}; use its uuid.".format(wanted))
        return wanted

    def _request(self, command, body=''):
        transport = self.pebble.transport
        if not isinstance(transport, ManagedEmulatorTransport):
            raise ToolError("Plugins can only be managed on the emulator.")
        # Separate from libpebble2's connection, which doesn't know this opcode.
        ws = websocket.create_connection('ws://localhost:{}/'.format(transport.pypkjs_port), timeout=10)
        try:
            ws.send_binary(bytes([PLUGIN_OPCODE, command]) + body.encode('utf-8'))
            reply = ws.recv()
            while not (isinstance(reply, bytes) and reply[:1] == bytes([PLUGIN_OPCODE])):
                reply = ws.recv()
        except websocket.WebSocketTimeoutException:
            raise ToolError("The emulator didn't answer. Its pypkjs may be too old to manage plugins.")
        finally:
            ws.close()
        if reply[1:2] != b'\x00':
            raise ToolError(reply[2:].decode('utf-8') or "The emulator refused.")
        return json.loads(reply[2:].decode('utf-8')) if len(reply) > 2 else None

    @classmethod
    def add_parser(cls, parser):
        parser = super(PluginsCommand, cls).add_parser(parser)
        parser.add_argument('action', nargs='?', choices=['list', 'remove'], default='list',
                            help="list (the default) or remove.")
        parser.add_argument('plugin', nargs='?',
                            help="For remove: the plugin's uuid or name. Defaults to the current project if it "
                                 "is a plugin.")
        return parser


def print_plugins(plugins):
    # Installed ones first: the built-ins are the same every time.
    for plugin in sorted(plugins, key=lambda p: p['kind'] == 'builtin'):
        print("{}  {}  ({})".format(plugin['name'], plugin['uuid'], KIND_LABELS.get(plugin['kind'], plugin['kind'])))
        if plugin['sources']:
            print("    sources: {}".format(', '.join(plugin['sources'])))
        if plugin['actions']:
            print("    actions: {}".format(', '.join(plugin['actions'])))
