
__author__ = 'katharine'

from .base import PebbleCommand
from pebble_tool.util.logs import PebbleLogPrinter
from pebble_tool.util.xsbug import XsbugCtrlPrinter, launch_xsbug, wait_for_listener, should_debug_moddable


class LogsCommand(PebbleCommand):
    """Displays running logs from the watch."""
    command = 'logs'

    def __call__(self, args):
        super(LogsCommand, self).__call__(args)
        force_colour = args.color if args.color != args.no_color else None
        log_printer = PebbleLogPrinter(self.pebble, force_colour=force_colour)
        # For a moddable project built with --debug, also bridge the xsbug JS debugger.
        xsbug_printer = None
        if should_debug_moddable():
            launch_xsbug()
            wait_for_listener()
            xsbug_printer = XsbugCtrlPrinter(self.pebble)
        try:
            log_printer.wait()
        finally:
            if xsbug_printer:
                xsbug_printer.stop()

    @classmethod
    def add_parser(cls, parser):
        parser = super(LogsCommand, cls).add_parser(parser)
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--color', action='store_true', help="Force colored output on")
        group.add_argument('--no-color', action='store_true', help="Force colored output off")
        return parser
