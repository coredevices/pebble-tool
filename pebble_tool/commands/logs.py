
__author__ = 'katharine'

from .base import PebbleCommand
from pebble_tool.util.logs import PebbleLogPrinter


class LogsCommand(PebbleCommand):
    """Displays running logs from the watch."""
    command = 'logs'

    def __call__(self, args):
        super(LogsCommand, self).__call__(args)
        force_colour = args.color if args.color != args.no_color else None
        PebbleLogPrinter(self.pebble, force_colour=force_colour).wait()

    @classmethod
    def add_parser(cls, parser):
        parser = super(LogsCommand, cls).add_parser(parser)
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--color', action='store_true', help="Force colored output on")
        group.add_argument('--no-color', action='store_true', help="Force colored output off")
        return parser
