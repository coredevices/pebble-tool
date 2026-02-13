
__author__ = 'katharine'

import random

from libpebble2.protocol.system import PingPong, Ping, Pong

from .base import PebbleCommand, BaseCommand
from ..exceptions import ToolError


class PingCommand(PebbleCommand):
    """Pings the watch."""
    command = 'ping'

    def __call__(self, args):
        emulator_platform = getattr(args, 'emulator', None)
        if emulator_platform:
            BaseCommand.__call__(self, args)
            from pebble_tool.bridge import run_bridge, ensure_bridge_qemu
            qemu_port, _ = ensure_bridge_qemu(args)
            rc = run_bridge('ping', qemu_port)
            if rc != 0:
                raise ToolError("Ping failed (exit code {}).".format(rc))
            return

        super(PingCommand, self).__call__(args)
        cookie = random.randint(1, 0xFFFFFFFF)
        pong = self.pebble.send_and_read(PingPong(cookie=cookie, message=Ping(idle=False)), PingPong)
        if pong.cookie == cookie:
            print("Pong!")
        else:
            print("Got wrong cookie: {} (expected {})".format(pong.cookie, cookie))
