
__author__ = 'katharine'

import code
import readline
import rlcompleter

import libpebble2.protocol

from .base import PebbleCommand


class ReplCommand(PebbleCommand):
    """Launches a python prompt with a 'pebble' object already connected."""
    command = 'repl'

    def __call__(self, args):
        super(ReplCommand, self).__call__(args)
        repl_env = {
            'pebble': self.pebble,
            'protocol': libpebble2.protocol,
        }
        readline.set_completer(rlcompleter.Completer(repl_env).complete)
        readline.parse_and_bind('tab:complete')
        code.interact(local=repl_env)
