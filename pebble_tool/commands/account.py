
__author__ = 'katharine'

import os

import requests

from .base import BaseCommand
from pebble_tool.account import get_account


DEFAULT_APPSTORE_API_BASE = "https://appstore-api.repebble.com"


class LogInCommand(BaseCommand):
    """Logs you in using Firebase auth. Required for CloudPebble and publish flows."""
    command = 'login'

    def __call__(self, args):
        super(LogInCommand, self).__call__(args)
        account = get_account(auth_provider="firebase")
        if getattr(args, "status", False):
            if account.is_logged_in:
                creds = account.get_credentials() or {}
                api_base = os.getenv("PEBBLE_APPSTORE_API_BASE", DEFAULT_APPSTORE_API_BASE).rstrip("/")
                me_url = "{}/api/v1/developer/me".format(api_base)
                link_status = "unknown"
                link_error = None
                developer_id = None
                try:
                    response = requests.get(
                        me_url,
                        headers={"Authorization": "Bearer {}".format(account.get_access_token())},
                        timeout=20,
                    )
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    if response.status_code == 403 and (payload.get("code") == "DEVELOPER_NOT_LINKED"):
                        link_status = "not linked"
                    elif response.status_code >= 400:
                        link_status = "check failed"
                        link_error = payload.get("error") or response.text[:200]
                    else:
                        developer = payload.get("developer") or {}
                        if isinstance(developer, dict):
                            developer_id = developer.get("id") or developer.get("_id")
                        if not (account.email or creds.get("email")):
                            dev_email = developer.get("email")
                            if dev_email:
                                creds["email"] = dev_email
                                account._save_credentials(creds)
                        if isinstance(developer, dict) and (developer.get("id") or developer.get("_id") or developer.get("firebase_uid")):
                            link_status = "linked"
                        else:
                            link_status = "not linked"
                except requests.RequestException as e:
                    link_status = "check failed"
                    link_error = str(e)

                print("Firebase login status: logged in")
                print("Email: {}".format(account.email or creds.get("email") or "unknown"))
                print("Identity provider: {}".format(creds.get("identity_provider", "unknown")))
                print("User ID: {}".format(account.id or creds.get("firebase_user_id") or "unknown"))
                print("Developer ID: {}".format(developer_id or "unknown"))
                print("Appstore API base: {}".format(api_base))
                print("Developer link: {}".format(link_status))
                if link_error:
                    print("Developer link error: {}".format(link_error))
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
        parser = super(LogInCommand, cls).add_parser(parser)
        parser.add_argument("--status", action="store_true", default=False,
                            help="Show Firebase login status and exit.")
        parser.add_argument("--id-token", type=str, help="Firebase id_token to use directly (skip browser auth).")
        parser.add_argument("--refresh-token", type=str, help="Optional Firebase refresh token for --id-token mode.")
        parser.add_argument("--expires-in", type=int, default=3600, help="Token lifetime in seconds for --id-token mode.")
        parser.add_argument("--local-id", type=str, help="Optional Firebase localId for --id-token mode.")
        parser.add_argument("--firebase-api-key", type=str, help="Firebase Web API key for Identity Toolkit exchange.")
        parser.add_argument("--firebase-project-id", type=str, help="Firebase project id (stored for metadata/debugging).")
        parser.add_argument("--oauth-broker-base", type=str,
                            help="OAuth broker API base URL (default: PEBBLE_CLI_OAUTH_BROKER_BASE or appstore API base).")
        parser.add_argument("--oauth-client-key", type=str,
                            help="Public client key for OAuth broker (default: PEBBLE_CLI_OAUTH_CLIENT_KEY).")
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
        logged_in_any = False
        for provider in ("firebase", "legacy"):
            account = get_account(auth_provider=provider)
            if account.is_logged_in:
                account.logout()
                logged_in_any = True
        if not logged_in_any:
            print("You aren't logged in anyway.")
