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


def test_firebase_login_via_dashboard_session_broker_flow(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_broker_auth(args):
        return "firebase-custom-token"

    def fake_post(url, data=None, json=None, timeout=None):
        if "signInWithCustomToken" in url:
            return _FakeResponse({
                "idToken": "firebase-id-token",
                "refreshToken": "firebase-refresh-token",
                "expiresIn": "3600",
            })
        if "accounts:lookup" in url:
            return _FakeResponse({
                "users": [{
                    "localId": "local-user",
                    "email": "oauth@example.com",
                    "displayName": "OAuth User",
                    "providerUserInfo": [{"providerId": "google.com"}],
                }]
            })
        return _FakeResponse({
            "error": {"message": "unexpected url"},
        })

    monkeypatch.setattr(account, "_authenticate_session_via_broker", fake_broker_auth)
    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)

    args = Namespace(
        id_token=None,
        refresh_token=None,
        expires_in=3600,
        local_id=None,
        firebase_api_key="firebase-api-key",
        firebase_project_id="coreapp-ce061",
        oauth_broker_base="http://localhost:3001",
        oauth_client_key="pebble-cli-public",
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
    assert account.get_credentials()["identity_provider"] == "google.com"


def test_firebase_custom_token_sign_in_error_surfaces(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_broker_auth(args):
        return "firebase-custom-token"

    def fake_post(url, data=None, json=None, timeout=None):
        return _FakeResponse({"error": {"message": "INVALID_CUSTOM_TOKEN"}}, status_code=400)

    monkeypatch.setattr(account, "_authenticate_session_via_broker", fake_broker_auth)
    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)

    args = Namespace(
        id_token=None,
        refresh_token=None,
        expires_in=3600,
        local_id=None,
        firebase_api_key="firebase-api-key",
        firebase_project_id="coreapp-ce061",
        oauth_broker_base="http://localhost:3001",
        oauth_client_key="pebble-cli-public",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
        verify_login=False,
    )
    try:
        account.login(args)
        assert False, "expected ToolError"
    except Exception as exc:
        assert "INVALID_CUSTOM_TOKEN" in str(exc)


def test_firebase_logout_clears_storage(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse({"uid": "uid-1", "name": "User"})

    monkeypatch.setattr("pebble_tool.firebase_account.requests.get", fake_get)
    account.login_with_token("firebase-token")
    assert account.is_logged_in

    account.logout()
    assert not account.is_logged_in
