
__author__ = 'katharine'

import random

from libpebble2.protocol.system import PingPong, Ping, Pong

from .base import PebbleCommand


class PingCommand(PebbleCommand):
    """Pings the watch."""
    command = 'ping'

    def __call__(self, args):
        super(PingCommand, self).__call__(args)
        cookie = random.randint(1, 0xFFFFFFFF)
        pong = self.pebble.send_and_read(PingPong(cookie=cookie, message=Ping(idle=False)), PingPong)
        if pong.cookie == cookie:
            print("Pong!")
        else:
            print("Got wrong cookie: {} (expected {})".format(pong.cookie, cookie))
