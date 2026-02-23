
__author__ = 'katharine'

from .base import BaseCommand
from pebble_tool.account import get_account, get_default_account


class LogInCommand(BaseCommand):
    """Logs you in to your Pebble account. Required to use the timeline and CloudPebble connections."""
    command = 'login'

    def __call__(self, args):
        super(LogInCommand, self).__call__(args)
        account = get_default_account()
        if hasattr(args, 'token') and args.token:
            account.login_with_token(args.token)
            print("Successfully logged in with provided token.")
        else:
            account.login(args)

    @classmethod
    def add_parser(cls, parser):
        parser = super(LogInCommand, cls).add_parser(parser)
        parser.add_argument('--token', type=str, help='Access token to use for authentication instead of OAuth flow')
        parser.add_argument('--auth_host_name', type=str, default='localhost')
        parser.add_argument('--auth_host_port', type=int, nargs='?', default=[60000])
        parser.add_argument('--logging_level', type=str, default='ERROR')
        parser.add_argument('--noauth_local_webserver', action='store_true', default=False,
                            help="Try this flag if the standard authentication isn't working.")
        return parser


class LogInFirebaseCommand(BaseCommand):
    """Logs you in using Firebase auth. Experimental drop-in replacement path for `login`."""
    command = 'login-firebase'

    def __call__(self, args):
        super(LogInFirebaseCommand, self).__call__(args)
        account = get_account(auth_provider="firebase")
        if getattr(args, "status", False):
            if account.is_logged_in:
                creds = account.get_credentials() or {}
                print("Firebase login status: logged in")
                print("Email: {}".format(account.email or creds.get("email") or "unknown"))
                print("Identity provider: {}".format(creds.get("identity_provider", "unknown")))
                print("User ID: {}".format(account.id or creds.get("firebase_user_id") or "unknown"))
            else:
                print("Firebase login status: logged out")
            return
        if hasattr(args, "id_token") and args.id_token:
            account.login_with_token(args.id_token)
            print("Successfully logged in with provided Firebase id token.")
            return
        account.login(args)

    @classmethod
    def add_parser(cls, parser):
        parser = super(LogInFirebaseCommand, cls).add_parser(parser)
        parser.add_argument("--status", action="store_true", default=False,
                            help="Show Firebase login status and exit.")
        parser.add_argument("--provider", choices=["google", "github"], default=None,
                            help="Initial provider to use for Firebase login. If omitted, you'll be prompted.")
        parser.add_argument("--auto-link", action="store_true", default=True,
                            help="Automatically handle account-exists conflicts by signing in with existing provider and linking.")
        parser.add_argument("--no-auto-link", action="store_false", dest="auto_link",
                            help="Disable automatic provider-link flow.")
        parser.add_argument("--id-token", type=str, help="Firebase id_token to use directly (skip browser auth).")
        parser.add_argument("--refresh-token", type=str, help="Optional Firebase refresh token for --id-token mode.")
        parser.add_argument("--expires-in", type=int, default=3600, help="Token lifetime in seconds for --id-token mode.")
        parser.add_argument("--local-id", type=str, help="Optional Firebase localId for --id-token mode.")
        parser.add_argument("--firebase-api-key", type=str, help="Firebase Web API key for Identity Toolkit exchange.")
        parser.add_argument("--firebase-project-id", type=str, help="Firebase project id (stored for metadata/debugging).")
        parser.add_argument("--client-secrets", type=str, help="Path to Google OAuth client secrets JSON.")
        parser.add_argument("--google-client-id", type=str, help="Google OAuth client id (if not using --client-secrets).")
        parser.add_argument("--google-client-secret", type=str, help="Google OAuth client secret (if not using --client-secrets).")
        parser.add_argument("--github-client-id", type=str, help="GitHub OAuth app client id.")
        parser.add_argument("--github-client-secret", type=str, help="GitHub OAuth app client secret.")
        parser.add_argument("--auth-host-name", type=str, default="localhost")
        parser.add_argument("--auth-host-port", type=int, default=60000)
        parser.add_argument("--no-open-browser", action="store_true", default=False,
                            help="Print auth URL without opening browser automatically.")
        parser.add_argument("--verify-login", action="store_true", default=False,
                            help="Verify id_token against CloudPebble's /accounts/api/firebase-login endpoint.")
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
