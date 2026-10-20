"""OAuth connectors for plugins: the provider settings and client secret the appstore's broker uses
so a plugin's `Pebble.oauth.authorize('<slug>')` can sign users in without shipping the secret.

A plugin declares its connectors as `"oauth": {"<slug>": {}}` in package.json's `pebble.plugin`;
each slug gets one connector on the appstore. Same API as the dashboard's connector page.
"""

import getpass
import os
import sys

import requests

from pebble_tool.account import get_account
from pebble_tool.commands.base import BaseCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.sdk.project import PebbleProject

DEFAULT_APPSTORE_API_BASE = "https://appstore-api.repebble.com"
SECRET_ENV = "PEBBLE_OAUTH_CLIENT_SECRET"

# Field, prompt label, required.
PROVIDER_FIELDS = (
    ("provider_label", "Provider name", True),
    ("authorize_url", "Authorization URL", True),
    ("token_url", "Token URL", True),
    ("scopes", "Scopes (space-separated)", False),
    ("token_auth_style", "Token endpoint auth (basic|body)", False),
    ("token_request_format", "Token request body (form|json)", False),
)


def firebase_token(args):
    token = (getattr(args, "firebase_id_token", None) or os.getenv("PEBBLE_FIREBASE_ID_TOKEN", "")).strip()
    if token:
        return token
    account = get_account(auth_provider="firebase")
    if not account.is_logged_in:
        raise ToolError("Not logged in. Run 'pebble login' first, or set PEBBLE_FIREBASE_ID_TOKEN for CI.")
    return account.get_access_token()


def _request(method, api_base, app_id, token, slug=None, body=None):
    url = "{}/api/dashboard/apps/{}/connector".format(api_base.rstrip("/"), app_id)
    try:
        response = requests.request(method, url, headers={"Authorization": "Bearer {}".format(token)},
                                    params={"slug": slug} if method == "DELETE" else None,
                                    json=body, timeout=60)
    except requests.RequestException as e:
        raise ToolError("Connector request failed: {}".format(e))
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code == 403 and payload.get("code") == "FORBIDDEN":
        raise ToolError("OAuth connectors aren't available for this account yet.")
    if response.status_code >= 400:
        raise ToolError("Connector {} failed ({}): {}".format(
            method, response.status_code, payload.get("error") or response.text[:300]))
    return payload


def get_connectors(api_base, app_id, token):
    return _request("GET", api_base, app_id, token)


def _ask(label, default=None, required=False):
    while True:
        value = input("{}{}: ".format(label, " [{}]".format(default) if default else "")).strip() or (default or "")
        if value or not required:
            return value
        print("This field is required.")


def _read_secret(args, interactive, has_secret):
    path = getattr(args, "client_secret_file", None)
    if path:
        with open(os.path.expanduser(path)) as f:
            return f.read().strip()
    if os.getenv(SECRET_ENV):
        return os.environ[SECRET_ENV].strip()
    if not interactive:
        if has_secret:
            return None
        raise ToolError("A client secret is required: pass --client-secret-file or set {}.".format(SECRET_ENV))
    while True:
        secret = getpass.getpass("Client secret{}: ".format(" (Enter keeps the stored one)" if has_secret else ""))
        if secret.strip() or has_secret:
            return secret.strip() or None
        print("This field is required.")


def build_connector(args, slug, existing, interactive):
    """The PUT body for one slug: the stored values, then flags, then prompts."""
    body = {"slug": slug, "enabled": True}
    for key in ("provider_label", "authorize_url", "token_url", "scopes", "token_auth_style",
                "token_request_format", "authorize_params", "token_request_headers", "client_id"):
        if existing and key in existing:
            body[key] = existing[key]
    for key in ("provider_label", "authorize_url", "token_url", "scopes", "token_auth_style",
                "token_request_format", "client_id"):
        value = getattr(args, key, None)
        if value is not None:
            body[key] = value

    if interactive:
        for key, label, required in PROVIDER_FIELDS:
            body[key] = _ask(label, body.get(key) or ("basic" if key == "token_auth_style" else
                                                      "form" if key == "token_request_format" else None),
                             required)
        body["client_id"] = _ask("Client ID", body.get("client_id"), True)

    missing = [k for k in ("provider_label", "authorize_url", "token_url", "client_id") if not body.get(k)]
    if missing:
        raise ToolError("Missing connector settings for {}: {}".format(slug, ", ".join(missing)))
    secret = _read_secret(args, interactive, bool(existing and existing.get("has_secret")))
    if secret:
        body["client_secret"] = secret
    return body


def configure_connector(args, api_base, app_id, token, slug, state=None, interactive=None):
    state = state or get_connectors(api_base, app_id, token)
    if interactive is None:
        interactive = sys.stdin.isatty()
    existing = next((c for c in state.get("connectors") or [] if c["slug"] == slug), None)
    print("Connector '{}': register this redirect URI with the provider:\n  {}".format(
        slug, state.get("provider_redirect_uri")))
    body = build_connector(args, slug, existing, interactive)
    saved = _request("PUT", api_base, app_id, token, body=body)["connector"]
    print("Saved connector '{}' ({}, client {}, secret {}).".format(
        slug, saved["provider_label"], saved["client_id"], saved.get("secret_hint") or "set"))
    return saved


def print_connectors(state):
    connectors = {c["slug"]: c for c in state.get("connectors") or []}
    slugs = list(dict.fromkeys((state.get("declared_slugs") or []) + list(connectors)))
    if not slugs:
        print("This app declares no OAuth connectors (add \"oauth\": {\"<slug>\": {}} to pebble.plugin).")
    for slug in slugs:
        c = connectors.get(slug)
        if not c:
            print("{}: not set up".format(slug))
            continue
        print("{}: {}{}".format(slug, c["provider_label"], "" if c["enabled"] else " (disabled)"))
        print("    authorize {}\n    token     {} ({}, {})".format(
            c["authorize_url"], c["token_url"], c["token_auth_style"], c.get("token_request_format") or "form"))
        print("    scopes    {}\n    client    {}  secret {}".format(
            c["scopes"] or "-", c["client_id"], c.get("secret_hint") or ("set" if c["has_secret"] else "missing")))
    print("Provider redirect URI: {}".format(state.get("provider_redirect_uri")))


def resolve_app_id(api_base, token, app_id=None):
    if app_id:
        return app_id
    try:
        project = PebbleProject()
    except Exception:
        raise ToolError("Run this inside a plugin project, or pass --app-id.")
    url = "{}/api/v1/developer/me".format(api_base.rstrip("/"))
    try:
        response = requests.get(url, headers={"Authorization": "Bearer {}".format(token)}, timeout=60)
        response.raise_for_status()
    except requests.RequestException as e:
        raise ToolError("Couldn't look up your apps: {}".format(e))
    lookup = (response.json().get("app_lookup") or {}).get("by_app_uuid") or {}
    uuid = str(project.uuid).lower()
    found = next((v for k, v in lookup.items() if k.lower() == uuid), None)
    if not found:
        raise ToolError("No appstore app has this project's UUID ({}). Run 'pebble publish' first.".format(uuid))
    return found


class OAuthConnectorCommand(BaseCommand):
    """Shows, sets up or deletes a plugin's OAuth connectors on the appstore."""
    command = "oauth-connector"

    def __call__(self, args):
        super(OAuthConnectorCommand, self).__call__(args)
        token = firebase_token(args)
        app_id = resolve_app_id(args.api_base, token, args.app_id)
        state = get_connectors(args.api_base, app_id, token)
        if args.action == "show":
            print_connectors(state)
            return
        slug = args.slug or self._only_slug(state)
        if args.action == "delete":
            _request("DELETE", args.api_base, app_id, token, slug=slug)
            print("Deleted connector '{}'.".format(slug))
        else:
            configure_connector(args, args.api_base, app_id, token, slug, state,
                                interactive=sys.stdin.isatty() and not args.non_interactive)

    @staticmethod
    def _only_slug(state):
        slugs = state.get("declared_slugs") or []
        if len(slugs) == 1:
            return slugs[0]
        raise ToolError("Say which connector with --slug ({}).".format(", ".join(slugs) or "none declared"))

    @classmethod
    def add_parser(cls, parser):
        parser = super(OAuthConnectorCommand, cls).add_parser(parser)
        parser.add_argument("action", nargs="?", choices=["show", "set", "delete"], default="show",
                            help="show (the default), set or delete.")
        add_connector_arguments(parser)
        parser.add_argument("--app-id", default=None, help="Appstore app ID. Defaults to the current project's app.")
        parser.add_argument("--api-base", default=os.getenv("PEBBLE_APPSTORE_API_BASE", DEFAULT_APPSTORE_API_BASE),
                            help="Appstore backend base URL.")
        parser.add_argument("--firebase-id-token", default=None, help="Firebase ID token (CI).")
        parser.add_argument("--non-interactive", action="store_true", default=False,
                            help="Don't prompt; take everything from flags and {}.".format(SECRET_ENV))
        return parser


def add_connector_arguments(parser):
    parser.add_argument("--slug", default=None, help="Connector slug, as declared under pebble.plugin.oauth.")
    parser.add_argument("--provider-label", default=None)
    parser.add_argument("--authorize-url", default=None)
    parser.add_argument("--token-url", default=None)
    parser.add_argument("--scopes", default=None)
    parser.add_argument("--token-auth-style", choices=["basic", "body"], default=None)
    parser.add_argument("--token-request-format", choices=["form", "json"], default=None)
    parser.add_argument("--client-id", default=None)
    parser.add_argument("--client-secret-file", default=None,
                        help="File holding the client secret. Or set {}; otherwise you're prompted.".format(SECRET_ENV))
