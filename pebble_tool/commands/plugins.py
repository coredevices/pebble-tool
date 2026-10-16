import json

from .base import PebbleCommand
from .emucontrol import EmuAppConfigCommand
from ..exceptions import ToolError
from ..util import phonesim

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
        result = phonesim.request(self.pebble.transport, PLUGIN_OPCODE, bytes([command]) + body.encode('utf-8'))
        return json.loads(result.decode('utf-8')) if result else None

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
