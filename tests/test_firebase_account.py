from __future__ import annotations

import datetime
from argparse import Namespace

from pebble_tool.firebase_account import FirebaseAccount


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeCreds:
    def __init__(self, id_token):
        self.id_token = id_token


class _FakeFlow:
    def __init__(self, id_token):
        self._id_token = id_token

    def run_local_server(self, **kwargs):
        return _FakeCreds(self._id_token)


def test_firebase_login_with_token_and_user_info(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    account.login_with_token("firebase-token")

    assert account.is_logged_in
    assert account.get_access_token() == "firebase-token"
    assert account.id is None
    assert account.name == "Unknown"
    assert account.email is None
    assert account.roles is None


def test_firebase_refreshes_expired_token(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))
    expired = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat()
    account._save_credentials({
        "id_token": "old-token",
        "refresh_token": "refresh-token",
        "expires_at": expired,
        "firebase_api_key": "api-key",
    })

    def fake_post(url, data=None, json=None, timeout=None):
        return _FakeResponse({
            "id_token": "new-token",
            "refresh_token": "refresh-token-new",
            "expires_in": "3600",
            "user_id": "new-user-id",
        })

    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)

    assert account.get_access_token() == "new-token"
    creds = account._load_credentials()
    assert creds["id_token"] == "new-token"
    assert creds["refresh_token"] == "refresh-token-new"
    assert creds["firebase_user_id"] == "new-user-id"


def test_firebase_login_via_google_oauth_flow(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_from_client_config(client_config, scopes):
        return _FakeFlow("google-id-token")

    def fake_post(url, data=None, json=None, timeout=None):
        return _FakeResponse({
            "idToken": "firebase-id-token",
            "refreshToken": "firebase-refresh-token",
            "expiresIn": "3600",
            "localId": "local-user",
            "email": "oauth@example.com",
            "displayName": "OAuth User",
        })

    monkeypatch.setattr(
        "pebble_tool.firebase_account.InstalledAppFlow.from_client_config",
        fake_from_client_config,
    )
    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)

    args = Namespace(
        provider="google",
        auto_link=True,
        id_token=None,
        refresh_token=None,
        expires_in=3600,
        local_id=None,
        firebase_api_key="firebase-api-key",
        firebase_project_id="coreapp-ce061",
        client_secrets=None,
        google_client_id="google-client-id",
        google_client_secret="google-client-secret",
        github_client_id=None,
        github_client_secret=None,
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
        verify_login=False,
    )
    account.login(args)

    assert account.is_logged_in
    assert account.bearer_token == "firebase-id-token"
    assert account.id == "local-user"
    assert account.email == "oauth@example.com"


def test_firebase_auto_link_on_conflict(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))
    sign_in_calls = []

    def fake_authenticate_provider(provider, args):
        if provider == "google":
            return "providerId=google.com&id_token=google-id-token"
        if provider == "github":
            return "providerId=github.com&access_token=github-access-token"
        raise AssertionError("unexpected provider")

    def fake_firebase_sign_in(api_key, post_body, existing_id_token=None):
        sign_in_calls.append((post_body, existing_id_token))
        if "providerId=google.com" in post_body and existing_id_token is None:
            from pebble_tool.firebase_account import _FirebaseNeedsLinkError
            raise _FirebaseNeedsLinkError("ACCOUNT_EXISTS_WITH_DIFFERENT_CREDENTIAL", email="x@example.com")
        if "providerId=github.com" in post_body and existing_id_token is None:
            return {
                "idToken": "existing-account-id-token",
                "refreshToken": "existing-refresh",
                "expiresIn": "3600",
                "localId": "existing-user",
            }
        if "providerId=google.com" in post_body and existing_id_token == "existing-account-id-token":
            return {
                "idToken": "linked-id-token",
                "refreshToken": "linked-refresh",
                "expiresIn": "3600",
                "localId": "existing-user",
            }
        raise AssertionError("unexpected sign-in payload")

    monkeypatch.setattr(account, "_authenticate_provider", fake_authenticate_provider)
    monkeypatch.setattr(account, "_firebase_sign_in_with_idp", fake_firebase_sign_in)

    args = Namespace(
        provider="google",
        auto_link=True,
        id_token=None,
        refresh_token=None,
        expires_in=3600,
        local_id=None,
        firebase_api_key="firebase-api-key",
        firebase_project_id="coreapp-ce061",
        client_secrets=None,
        google_client_id="google-client-id",
        google_client_secret="google-client-secret",
        github_client_id="github-client-id",
        github_client_secret="github-client-secret",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
        verify_login=False,
    )
    account.login(args)

    assert account.bearer_token == "linked-id-token"
    assert account.id == "existing-user"
    assert len(sign_in_calls) == 3


def test_prompt_provider_reads_selection(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert account._prompt_provider() == "github"


def test_firebase_logout_clears_storage(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse({"uid": "uid-1", "name": "User"})

    monkeypatch.setattr("pebble_tool.firebase_account.requests.get", fake_get)
    account.login_with_token("firebase-token")
    assert account.is_logged_in

    account.logout()
    assert not account.is_logged_in
