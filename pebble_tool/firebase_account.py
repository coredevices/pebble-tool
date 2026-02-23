import datetime
import http.server
import json
import os
import os.path
import secrets
import socketserver
import urllib.parse
import webbrowser

import requests
from google_auth_oauthlib.flow import InstalledAppFlow

from pebble_tool.exceptions import ToolError
from pebble_tool.util import get_persist_dir


DEFAULT_FIREBASE_API_KEY = os.getenv("PEBBLE_FIREBASE_API_KEY", "AIzaSyBZ9Cdvwwv9At2lPmc8TxyyEqSXGXejGvc")
DEFAULT_FIREBASE_PROJECT_ID = os.getenv("PEBBLE_FIREBASE_PROJECT_ID", "coreapp-ce061")
DEFAULT_FIREBASE_ME_URI = os.getenv("PEBBLE_FIREBASE_ME_URI")
DEFAULT_FIREBASE_LOGIN_VERIFY_URI = os.getenv(
    "PEBBLE_FIREBASE_LOGIN_VERIFY_URI",
    "https://cloud.repebble.com/accounts/api/firebase-login",
)
DEFAULT_GOOGLE_CLIENT_ID = os.getenv(
    "PEBBLE_FIREBASE_GOOGLE_CLIENT_ID",
    "460977838956-0138lsa7ppsrvom6u57nn0jc0b0l0hsh.apps.googleusercontent.com",
)
DEFAULT_GOOGLE_CLIENT_SECRET = os.getenv(
    "PEBBLE_FIREBASE_GOOGLE_CLIENT_SECRET",
    "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",
)


class _FirebaseNeedsLinkError(Exception):
    def __init__(self, message, email=None):
        super(_FirebaseNeedsLinkError, self).__init__(message)
        self.message = message
        self.email = email


class FirebaseAccount(object):
    def __init__(self, persistent_dir):
        self.persistent_dir = persistent_dir
        self.credential_path = os.path.join(self.persistent_dir, "firebase_oauth_storage.json")
        self._user_info = None
        self._get_user_info()

    @property
    def _user_info_path(self):
        return os.path.join(self.persistent_dir, "user_info_firebase")

    @property
    def is_logged_in(self):
        if not os.path.isfile(self.credential_path):
            return False
        creds = self._load_credentials()
        if not creds:
            return False
        return bool(creds.get("id_token"))

    def _load_credentials(self):
        try:
            with open(self.credential_path) as f:
                return json.load(f)
        except (IOError, ValueError):
            return None

    def _save_credentials(self, credentials):
        with open(self.credential_path, "w") as f:
            json.dump(credentials, f)

    def _utc_now(self):
        return datetime.datetime.now(datetime.timezone.utc)

    def _needs_refresh(self, credentials):
        expires_at = credentials.get("expires_at")
        if not expires_at:
            return False
        try:
            expiry = datetime.datetime.fromisoformat(expires_at)
        except ValueError:
            return True
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        return self._utc_now() >= (expiry - datetime.timedelta(minutes=2))

    def _refresh_id_token(self, credentials):
        refresh_token = credentials.get("refresh_token")
        api_key = credentials.get("firebase_api_key")
        if not refresh_token or not api_key:
            return credentials

        url = "https://securetoken.googleapis.com/v1/token?key={}".format(api_key)
        result = requests.post(
            url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=20,
        )
        result.raise_for_status()
        payload = result.json()

        expires_in = int(payload.get("expires_in", 3600))
        expires_at = (self._utc_now() + datetime.timedelta(seconds=expires_in)).isoformat()
        refreshed = dict(credentials)
        refreshed.update(
            {
                "id_token": payload["id_token"],
                "refresh_token": payload.get("refresh_token", refresh_token),
                "expires_at": expires_at,
                "firebase_user_id": payload.get("user_id", credentials.get("firebase_user_id")),
            }
        )
        self._save_credentials(refreshed)
        return refreshed

    def get_credentials(self):
        creds = self._load_credentials()
        if not creds:
            return None
        if self._needs_refresh(creds):
            try:
                creds = self._refresh_id_token(creds)
            except requests.RequestException:
                pass
        return creds

    def refresh_credentials(self):
        creds = self._load_credentials()
        if not creds:
            return
        refreshed = self._refresh_id_token(creds)
        self._save_credentials(refreshed)

    def get_access_token(self):
        creds = self.get_credentials()
        if not creds or not creds.get("id_token"):
            raise ToolError("Not logged in. Run 'pebble login-firebase'.")
        return creds["id_token"]

    bearer_token = property(get_access_token)

    @property
    def id(self):
        return self._get_user_info()["id"]

    @property
    def name(self):
        return self._get_user_info()["name"]

    @property
    def email(self):
        info = self._get_user_info()
        return info.get("email")

    @property
    def roles(self):
        info = self._get_user_info()
        return info.get("roles")

    @property
    def legacy_id(self):
        info = self._get_user_info()
        return info.get("legacy_id")

    def _build_installed_client_config(self, args):
        client_secrets = getattr(args, "client_secrets", None) or os.getenv("PEBBLE_FIREBASE_CLIENT_SECRETS_FILE")
        if client_secrets:
            with open(client_secrets) as f:
                raw = json.load(f)
            if "installed" in raw:
                return raw
            if "web" in raw:
                return {"installed": raw["web"]}
            raise ToolError("Client secrets file must contain an 'installed' or 'web' OAuth client block.")

        client_id = getattr(args, "google_client_id", None) or DEFAULT_GOOGLE_CLIENT_ID
        client_secret = getattr(args, "google_client_secret", None) or DEFAULT_GOOGLE_CLIENT_SECRET
        if not client_id or not client_secret:
            raise ToolError(
                "Missing Google OAuth client credentials. Provide --client-secrets or set "
                "PEBBLE_FIREBASE_GOOGLE_CLIENT_ID and PEBBLE_FIREBASE_GOOGLE_CLIENT_SECRET."
            )

        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    def _capture_local_redirect(self, host, port, open_browser, url_to_open):
        query_container = {"params": None}

        class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query_container["params"] = urllib.parse.parse_qs(parsed.query)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h2>Authentication complete.</h2>You can close this window.</body></html>")

            def log_message(self, format, *args):
                return

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer((host, port), OAuthCallbackHandler) as httpd:
            if open_browser:
                webbrowser.open(url_to_open, new=1, autoraise=True)
            else:
                print("Open this URL in your browser:\n{}".format(url_to_open))
            httpd.handle_request()

        params = query_container["params"] or {}
        if "error" in params:
            raise ToolError("Authentication request was rejected: {}".format(params["error"][0]))
        if "code" not in params:
            raise ToolError('Failed to find "code" in redirect query parameters.')
        return params["code"][0]

    def _google_sign_in(self, args):
        client_config = self._build_installed_client_config(args)
        flow = InstalledAppFlow.from_client_config(
            client_config,
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/userinfo.profile",
            ],
        )
        port = int(getattr(args, "auth_host_port", 60000))
        host = getattr(args, "auth_host_name", "localhost")
        creds = flow.run_local_server(
            host=host,
            port=port,
            open_browser=(not getattr(args, "no_open_browser", False)),
            prompt="consent",
            authorization_prompt_message="",
            success_message="Authentication complete. You can close this window.",
        )
        if not creds.id_token:
            raise ToolError("Google OAuth did not return an id_token.")
        return creds.id_token

    def _github_sign_in(self, args):
        client_id = getattr(args, "github_client_id", None) or os.getenv("PEBBLE_FIREBASE_GITHUB_CLIENT_ID")
        client_secret = getattr(args, "github_client_secret", None) or os.getenv("PEBBLE_FIREBASE_GITHUB_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ToolError(
                "Missing GitHub OAuth client credentials. Provide --github-client-id/--github-client-secret or set "
                "PEBBLE_FIREBASE_GITHUB_CLIENT_ID and PEBBLE_FIREBASE_GITHUB_CLIENT_SECRET."
            )

        host = getattr(args, "auth_host_name", "localhost")
        port = int(getattr(args, "auth_host_port", 60000))
        redirect_uri = "http://{}:{}/".format(host, port)
        state = secrets.token_urlsafe(24)
        authorize_url = (
            "https://github.com/login/oauth/authorize?"
            + urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "scope": "read:user user:email",
                    "state": state,
                }
            )
        )
        code = self._capture_local_redirect(
            host=host,
            port=port,
            open_browser=(not getattr(args, "no_open_browser", False)),
            url_to_open=authorize_url,
        )

        token_result = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "state": state,
            },
            timeout=30,
        )
        token_result.raise_for_status()
        payload = token_result.json()
        access_token = payload.get("access_token")
        if not access_token:
            raise ToolError("GitHub OAuth did not return an access token.")
        return access_token

    def _provider_credential_to_post_body(self, provider, credential):
        if provider == "google":
            return urllib.parse.urlencode(
                {
                    "id_token": credential,
                    "providerId": "google.com",
                }
            )
        if provider == "github":
            return urllib.parse.urlencode(
                {
                    "access_token": credential,
                    "providerId": "github.com",
                }
            )
        raise ToolError("Unsupported provider '{}'. Use google or github.".format(provider))

    def _firebase_sign_in_with_idp(self, api_key, post_body, existing_id_token=None):
        url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={}".format(api_key)
        payload = {
            "postBody": post_body,
            "requestUri": "http://localhost",
            "returnSecureToken": True,
            "returnIdpCredential": True,
        }
        if existing_id_token:
            payload["idToken"] = existing_id_token

        result = requests.post(url, json=payload, timeout=30)
        try:
            response_payload = result.json()
        except ValueError:
            response_payload = {}

        if result.status_code >= 400:
            message = ((response_payload.get("error") or {}).get("message")) or "UNKNOWN_ERROR"
            email = response_payload.get("email")
            if message in ("ACCOUNT_EXISTS_WITH_DIFFERENT_CREDENTIAL", "EMAIL_EXISTS", "FEDERATED_USER_ID_ALREADY_LINKED"):
                raise _FirebaseNeedsLinkError(message=message, email=email)
            raise ToolError("Firebase sign-in failed: {}".format(message))

        return response_payload

    def _verify_with_cloudpebble(self, firebase_id_token):
        result = requests.post(
            DEFAULT_FIREBASE_LOGIN_VERIFY_URI,
            data={"id_token": firebase_id_token},
            timeout=20,
        )
        result.raise_for_status()

    def _authenticate_provider(self, provider, args):
        if provider == "google":
            credential = self._google_sign_in(args)
        elif provider == "github":
            credential = self._github_sign_in(args)
        else:
            raise ToolError("Unsupported provider '{}'. Use google or github.".format(provider))
        return self._provider_credential_to_post_body(provider, credential)

    def _fallback_providers(self, initial_provider):
        return [p for p in ("google", "github") if p != initial_provider]

    def _prompt_provider(self):
        print("Choose sign-in provider:")
        print("  1) Google")
        print("  2) GitHub")
        while True:
            selected = input("Provider [1-2]: ").strip().lower()
            if selected in ("1", "google", "g"):
                return "google"
            if selected in ("2", "github", "gh"):
                return "github"
            print("Please enter 1 or 2.")

    def login(self, args):
        api_key = getattr(args, "firebase_api_key", None) or DEFAULT_FIREBASE_API_KEY
        if not api_key:
            raise ToolError("Missing Firebase API key. Set PEBBLE_FIREBASE_API_KEY or pass --firebase-api-key.")
        provider_used = getattr(args, "provider", None)

        direct_id_token = getattr(args, "id_token", None)
        if direct_id_token:
            firebase_payload = {
                "idToken": direct_id_token,
                "refreshToken": getattr(args, "refresh_token", None),
                "expiresIn": str(getattr(args, "expires_in", 3600)),
                "localId": getattr(args, "local_id", None),
            }
            provider_used = provider_used or "token"
        else:
            initial_provider = provider_used or self._prompt_provider()
            provider_used = initial_provider
            initial_post_body = self._authenticate_provider(initial_provider, args)
            try:
                firebase_payload = self._firebase_sign_in_with_idp(api_key, initial_post_body)
            except _FirebaseNeedsLinkError as link_error:
                if not getattr(args, "auto_link", True):
                    raise ToolError(
                        "This account already exists with a different provider ({}). Re-run with --auto-link.".format(
                            link_error.message
                        )
                    )
                if link_error.email:
                    print(
                        "Firebase reports this email already exists with another login method: {}.".format(
                            link_error.email
                        )
                    )
                else:
                    print("Firebase reports this account exists with a different login method.")
                print("To continue, sign in with your existing provider. I will then link your requested provider automatically.")

                fallback_payload = None
                fallback_provider_used = None
                for fallback_provider in self._fallback_providers(initial_provider):
                    print("Attempting existing-provider sign-in with {}...".format(fallback_provider))
                    try:
                        fallback_post_body = self._authenticate_provider(fallback_provider, args)
                        fallback_payload = self._firebase_sign_in_with_idp(api_key, fallback_post_body)
                        fallback_provider_used = fallback_provider
                        break
                    except _FirebaseNeedsLinkError:
                        continue
                    except ToolError as e:
                        print("Skipping {}: {}".format(fallback_provider, e))
                        continue

                if not fallback_payload:
                    raise ToolError(
                        "Could not sign in with fallback providers automatically. Try again and choose the provider originally used for this account."
                    )

                existing_id_token = fallback_payload.get("idToken")
                if not existing_id_token:
                    raise ToolError("Fallback provider sign-in succeeded but did not return idToken.")

                try:
                    firebase_payload = self._firebase_sign_in_with_idp(
                        api_key,
                        initial_post_body,
                        existing_id_token=existing_id_token,
                    )
                    print(
                        "Linked {} to your existing {} account.".format(initial_provider, fallback_provider_used)
                    )
                except _FirebaseNeedsLinkError:
                    # Even if explicit link cannot be completed, keep user logged in via existing provider.
                    print(
                        "Signed in with existing provider '{}'. Continuing without explicit provider link.".format(
                            fallback_provider_used
                        )
                    )
                    firebase_payload = fallback_payload

        firebase_id_token = firebase_payload.get("idToken")
        if not firebase_id_token:
            raise ToolError("Firebase sign-in failed to return an idToken.")

        if getattr(args, "verify_login", False):
            self._verify_with_cloudpebble(firebase_id_token)

        expires_in = int(firebase_payload.get("expiresIn", 3600))
        expires_at = (self._utc_now() + datetime.timedelta(seconds=expires_in)).isoformat()
        stored = {
            "id_token": firebase_id_token,
            "refresh_token": firebase_payload.get("refreshToken"),
            "expires_at": expires_at,
            "firebase_user_id": firebase_payload.get("localId"),
            "email": firebase_payload.get("email"),
            "display_name": firebase_payload.get("displayName"),
            "firebase_project_id": getattr(args, "firebase_project_id", None) or DEFAULT_FIREBASE_PROJECT_ID,
            "firebase_api_key": api_key,
            "auth_provider": "firebase",
            "identity_provider": provider_used,
        }
        self._save_credentials(stored)
        self._user_info = {
            "id": stored.get("firebase_user_id"),
            "name": stored.get("display_name") or stored.get("email") or "Unknown",
            "email": stored.get("email"),
            "roles": None,
            "legacy_id": None,
        }
        with open(self._user_info_path, "w") as f:
            json.dump(self._user_info, f)
        email = self._user_info.get("email") if self._user_info else None
        if email:
            print("Firebase authentication successful. Signed in as {}.".format(email))
        else:
            print("Firebase authentication successful.")

    def login_with_token(self, access_token):
        stored = {
            "id_token": access_token,
            "refresh_token": None,
            "expires_at": (self._utc_now() + datetime.timedelta(hours=1)).isoformat(),
            "firebase_api_key": DEFAULT_FIREBASE_API_KEY,
            "firebase_project_id": DEFAULT_FIREBASE_PROJECT_ID,
            "auth_provider": "firebase",
        }
        self._save_credentials(stored)
        self._user_info = {
            "id": None,
            "name": "Unknown",
            "email": None,
            "roles": None,
            "legacy_id": None,
        }
        with open(self._user_info_path, "w") as f:
            json.dump(self._user_info, f)

    def logout(self):
        if os.path.isfile(self.credential_path):
            os.unlink(self.credential_path)
        if os.path.isfile(self._user_info_path):
            os.unlink(self._user_info_path)
        self._user_info = None

    def _get_user_info(self):
        if self._user_info is not None:
            return self._user_info

        if not self.is_logged_in:
            return None

        file_path = self._user_info_path
        try:
            with open(file_path) as f:
                self._user_info = json.load(f)
                return self._user_info
        except (IOError, ValueError):
            creds = self.get_credentials() or {}
            stored_info = {
                "id": creds.get("firebase_user_id"),
                "name": creds.get("display_name") or creds.get("email") or "Unknown",
                "email": creds.get("email"),
                "roles": None,
                "legacy_id": None,
            }
            if DEFAULT_FIREBASE_ME_URI:
                try:
                    result = requests.get(
                        DEFAULT_FIREBASE_ME_URI,
                        headers={"Authorization": "Bearer {}".format(self.get_access_token())},
                        timeout=20,
                    )
                    result.raise_for_status()
                    account_info = result.json()
                    stored_info = {
                        "id": account_info.get("uid", account_info.get("id", stored_info["id"])),
                        "name": account_info.get("name", account_info.get("email", stored_info["name"])),
                        "email": account_info.get("email", stored_info["email"]),
                        "roles": account_info.get("scopes", account_info.get("roles")),
                        "legacy_id": account_info.get("legacy_id"),
                    }
                except requests.RequestException:
                    pass
            with open(file_path, "w") as f:
                json.dump(stored_info, f)
            self._user_info = stored_info
            return self._user_info


def get_default_firebase_account():
    path = os.path.join(get_persist_dir(), "oauth_firebase")
    if not os.path.exists(path):
        os.makedirs(path)
    return FirebaseAccount(path)
