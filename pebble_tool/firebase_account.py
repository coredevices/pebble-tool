import datetime
import http.server
import json
import os
import os.path
import socketserver
import time
import urllib.parse
import webbrowser

import requests

from pebble_tool.exceptions import ToolError
from pebble_tool.util import get_persist_dir


DEFAULT_FIREBASE_API_KEY = os.getenv("PEBBLE_FIREBASE_API_KEY", "AIzaSyBZ9Cdvwwv9At2lPmc8TxyyEqSXGXejGvc")
DEFAULT_FIREBASE_PROJECT_ID = os.getenv("PEBBLE_FIREBASE_PROJECT_ID", "coreapp-ce061")
DEFAULT_FIREBASE_ME_URI = os.getenv("PEBBLE_FIREBASE_ME_URI")
DEFAULT_FIREBASE_LOGIN_VERIFY_URI = os.getenv(
    "PEBBLE_FIREBASE_LOGIN_VERIFY_URI",
    "https://cloud.repebble.com/accounts/api/firebase-login",
)
DEFAULT_APPSTORE_API_BASE = "https://appstore-api.repebble.com"
DEFAULT_OAUTH_BROKER_CLIENT_KEY = os.getenv("PEBBLE_CLI_OAUTH_CLIENT_KEY", "pebble-cli-public")


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
            raise ToolError("Not logged in. Run 'pebble login'.")
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

    def _capture_local_redirect_params(self, host, port, open_browser, url_to_open):
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
        return params

    def _oauth_broker_base(self, args):
        from_env = os.getenv("PEBBLE_CLI_OAUTH_BROKER_BASE")
        fallback_api_base = os.getenv("PEBBLE_APPSTORE_API_BASE", DEFAULT_APPSTORE_API_BASE)
        return (getattr(args, "oauth_broker_base", None) or from_env or fallback_api_base).rstrip("/")

    def _oauth_client_key(self, args):
        return getattr(args, "oauth_client_key", None) or DEFAULT_OAUTH_BROKER_CLIENT_KEY

    def _broker_start_session_auth(self, args):
        host = getattr(args, "auth_host_name", "localhost")
        port = int(getattr(args, "auth_host_port", 60000))
        callback_url = "http://{}:{}/".format(host, port)
        broker_base = self._oauth_broker_base(args)
        client_key = self._oauth_client_key(args)

        start_result = requests.post(
            "{}/api/v1/cli-oauth/start".format(broker_base),
            headers={"X-Pebble-Client-Key": client_key},
            json={"callback_url": callback_url},
            timeout=30,
        )
        try:
            start_payload = start_result.json()
        except ValueError:
            start_payload = {}
        if start_result.status_code >= 400:
            raise ToolError(
                "OAuth broker start failed ({}): {}".format(
                    start_result.status_code,
                    start_payload.get("error") or start_payload.get("code") or start_result.text[:200],
                )
            )

        auth_url = start_payload.get("auth_url")
        flow_id = start_payload.get("flow_id")
        if not auth_url or not flow_id:
            raise ToolError("OAuth broker returned an invalid start response.")

        params = self._capture_local_redirect_params(
            host=host,
            port=port,
            open_browser=(not getattr(args, "no_open_browser", False)),
            url_to_open=auth_url,
        )

        callback_flow_id = (params.get("flow_id") or [flow_id])[0]
        if callback_flow_id != flow_id:
            raise ToolError("OAuth callback flow mismatch.")
        status = (params.get("broker_status") or [""])[0]
        if status == "error":
            callback_code = (params.get("broker_code") or ["UNKNOWN"])[0]
            raise ToolError("OAuth provider callback failed ({})".format(callback_code))

        exchange_url = "{}/api/v1/cli-oauth/exchange".format(broker_base)
        for _ in range(60):
            exchange_result = requests.post(
                exchange_url,
                headers={"X-Pebble-Client-Key": client_key},
                json={"flow_id": flow_id},
                timeout=20,
            )
            try:
                exchange_payload = exchange_result.json()
            except ValueError:
                exchange_payload = {}
            if exchange_result.status_code >= 400:
                raise ToolError(
                    "OAuth broker exchange failed ({}): {}".format(
                        exchange_result.status_code,
                        exchange_payload.get("error") or exchange_payload.get("code") or exchange_result.text[:200],
                    )
                )
            if exchange_payload.get("status") == "pending":
                time.sleep(0.5)
                continue
            if exchange_payload.get("status") == "error":
                raise ToolError(
                    "OAuth provider exchange failed ({}): {}".format(
                        exchange_payload.get("code") or "UNKNOWN",
                        exchange_payload.get("error") or "unknown error",
                    )
                )
            if exchange_payload.get("status") == "ok":
                return exchange_payload
            raise ToolError("OAuth broker exchange returned unexpected response.")
        raise ToolError("Timed out waiting for OAuth broker exchange.")

    def _firebase_sign_in_with_custom_token(self, api_key, custom_token):
        url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={}".format(api_key)
        result = requests.post(
            url,
            json={
                "token": custom_token,
                "returnSecureToken": True,
            },
            timeout=30,
        )
        try:
            response_payload = result.json()
        except ValueError:
            response_payload = {}

        if result.status_code >= 400:
            message = ((response_payload.get("error") or {}).get("message")) or "UNKNOWN_ERROR"
            raise ToolError("Firebase custom-token sign-in failed: {}".format(message))

        return response_payload

    def _firebase_lookup_profile(self, api_key, id_token):
        url = "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={}".format(api_key)
        result = requests.post(
            url,
            json={"idToken": id_token},
            timeout=30,
        )
        result.raise_for_status()
        payload = result.json()
        users = payload.get("users") or []
        if not users:
            return {}
        user = users[0]
        provider_infos = user.get("providerUserInfo") or []
        provider_id = None
        for info in provider_infos:
            pid = info.get("providerId")
            if pid and pid != "password":
                provider_id = pid
                break
        return {
            "localId": user.get("localId"),
            "email": user.get("email"),
            "displayName": user.get("displayName"),
            "identity_provider": provider_id,
        }

    def _verify_with_cloudpebble(self, firebase_id_token):
        result = requests.post(
            DEFAULT_FIREBASE_LOGIN_VERIFY_URI,
            data={"id_token": firebase_id_token},
            timeout=20,
        )
        result.raise_for_status()

    def _authenticate_session_via_broker(self, args):
        payload = self._broker_start_session_auth(args)
        if payload.get("status") != "ok":
            raise ToolError("OAuth broker did not return a successful result.")
        credential_type = payload.get("credential_type")
        credential = payload.get("credential")
        if credential_type != "custom_token" or not credential:
            raise ToolError("OAuth broker did not return a Firebase custom token.")
        return credential

    def login(self, args):
        api_key = getattr(args, "firebase_api_key", None) or DEFAULT_FIREBASE_API_KEY
        if not api_key:
            raise ToolError("Missing Firebase API key. Set PEBBLE_FIREBASE_API_KEY or pass --firebase-api-key.")
        direct_id_token = getattr(args, "id_token", None)
        if direct_id_token:
            firebase_payload = {
                "idToken": direct_id_token,
                "refreshToken": getattr(args, "refresh_token", None),
                "expiresIn": str(getattr(args, "expires_in", 3600)),
                "localId": getattr(args, "local_id", None),
            }
            provider_used = "token"
        else:
            custom_token = self._authenticate_session_via_broker(args)
            firebase_payload = self._firebase_sign_in_with_custom_token(api_key, custom_token)
            provider_used = "dashboard"

        firebase_id_token = firebase_payload.get("idToken")
        if not firebase_id_token:
            raise ToolError("Firebase sign-in failed to return an idToken.")

        if getattr(args, "verify_login", False):
            self._verify_with_cloudpebble(firebase_id_token)

        profile = {}
        try:
            profile = self._firebase_lookup_profile(api_key, firebase_id_token)
        except requests.RequestException:
            profile = {}

        expires_in = int(firebase_payload.get("expiresIn", 3600))
        expires_at = (self._utc_now() + datetime.timedelta(seconds=expires_in)).isoformat()
        identity_provider = profile.get("identity_provider")
        if identity_provider:
            provider_used = identity_provider
        stored = {
            "id_token": firebase_id_token,
            "refresh_token": firebase_payload.get("refreshToken"),
            "expires_at": expires_at,
            "firebase_user_id": profile.get("localId") or firebase_payload.get("localId"),
            "email": profile.get("email") or firebase_payload.get("email"),
            "display_name": profile.get("displayName") or firebase_payload.get("displayName"),
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
                cached = json.load(f)
                cached_email = cached.get("email")
                cached_id = cached.get("id")
                if (not cached_email) or (str(cached_email).lower() == "unknown") or (not cached_id):
                    creds = self.get_credentials() or {}
                    try:
                        profile = self._firebase_lookup_profile(
                            creds.get("firebase_api_key") or DEFAULT_FIREBASE_API_KEY,
                            creds.get("id_token"),
                        )
                    except requests.RequestException:
                        profile = {}
                    if profile:
                        creds["firebase_user_id"] = profile.get("localId") or creds.get("firebase_user_id")
                        creds["email"] = profile.get("email") or creds.get("email")
                        creds["display_name"] = profile.get("displayName") or creds.get("display_name")
                        if profile.get("identity_provider"):
                            creds["identity_provider"] = profile.get("identity_provider")
                        self._save_credentials(creds)
                        cached = {
                            "id": creds.get("firebase_user_id"),
                            "name": creds.get("display_name") or creds.get("email") or "Unknown",
                            "email": creds.get("email"),
                            "roles": cached.get("roles"),
                            "legacy_id": cached.get("legacy_id"),
                        }
                        with open(file_path, "w") as wf:
                            json.dump(cached, wf)
                self._user_info = cached
                return self._user_info
        except (IOError, ValueError):
            creds = self.get_credentials() or {}
            if (not creds.get("email")) or (not creds.get("firebase_user_id")):
                try:
                    profile = self._firebase_lookup_profile(
                        creds.get("firebase_api_key") or DEFAULT_FIREBASE_API_KEY,
                        creds.get("id_token"),
                    )
                    if profile:
                        creds["firebase_user_id"] = profile.get("localId") or creds.get("firebase_user_id")
                        creds["email"] = profile.get("email") or creds.get("email")
                        creds["display_name"] = profile.get("displayName") or creds.get("display_name")
                        if profile.get("identity_provider"):
                            creds["identity_provider"] = profile.get("identity_provider")
                        self._save_credentials(creds)
                except requests.RequestException:
                    pass
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
