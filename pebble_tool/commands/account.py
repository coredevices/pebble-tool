
__author__ = 'katharine'

from .base import BaseCommand
from pebble_tool.account import get_default_account


class LogInCommand(BaseCommand):
    """Logs you in to your Pebble account. Required to use the timeline and CloudPebble connections."""
    command = 'login'

    def __call__(self, args):
        super(LogInCommand, self).__call__(args)
        account = get_default_account()
        account.login(args)

    @classmethod
    def add_parser(cls, parser):
        parser = super(LogInCommand, cls).add_parser(parser)
        parser.add_argument('--auth_host_name', type=str, default='localhost')
        parser.add_argument('--auth_host_port', type=int, nargs='?', default=[60000])
        parser.add_argument('--logging_level', type=str, default='ERROR')
        parser.add_argument('--noauth_local_webserver', action='store_true', default=False,
                            help="Try this flag if the standard authentication isn't working.")
        return parser


class LogOutCommand(BaseCommand):
    """Logs you out of your Pebble account."""
    command = 'logout'

    def __call__(self, args):
        super(LogOutCommand, self).__call__(args)
        account = get_default_account()
        if account.is_logged_in:
            account.logout()
        else:
            print("You aren't logged in anyway.")

