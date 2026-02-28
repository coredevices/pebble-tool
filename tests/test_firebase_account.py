from __future__ import annotations

import datetime
from argparse import Namespace

import pytest

import pebble_tool.firebase_account as firebase_account_module
from pebble_tool.firebase_account import FirebaseAccount


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("http error")
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


def test_broker_start_session_auth_success(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))
    calls = []

    def fake_capture(host, port, open_browser, url_to_open):
        assert "http://broker/auth" == url_to_open
        return {"flow_id": ["f1"], "broker_status": ["ok"]}

    def fake_post(url, headers=None, json=None, timeout=None, data=None):
        calls.append(url)
        if url.endswith("/api/v1/cli-oauth/start"):
            return _FakeResponse({"flow_id": "f1", "auth_url": "http://broker/auth"}, status_code=200)
        if url.endswith("/api/v1/cli-oauth/exchange") and len(calls) == 2:
            return _FakeResponse({"status": "pending"}, status_code=200)
        if url.endswith("/api/v1/cli-oauth/exchange"):
            return _FakeResponse({"status": "ok", "credential_type": "custom_token", "credential": "ctok"}, status_code=200)
        raise AssertionError(url)

    monkeypatch.setattr(account, "_capture_local_redirect_params", fake_capture)
    monkeypatch.setattr(firebase_account_module.time, "sleep", lambda s: None)
    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)

    args = Namespace(
        oauth_broker_base="http://broker",
        oauth_client_key="k",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
    )
    payload = account._broker_start_session_auth(args)
    assert payload["status"] == "ok"
    assert payload["credential"] == "ctok"


def test_broker_start_session_auth_start_error(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))

    def fake_post(url, headers=None, json=None, timeout=None, data=None):
        return _FakeResponse({"error": "bad"}, status_code=500, text="bad")

    monkeypatch.setattr("pebble_tool.firebase_account.requests.post", fake_post)
    args = Namespace(
        oauth_broker_base="http://broker",
        oauth_client_key="k",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
    )
    with pytest.raises(Exception, match="OAuth broker start failed"):
        account._broker_start_session_auth(args)


def test_broker_start_session_auth_callback_mismatch(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))

    monkeypatch.setattr(account, "_capture_local_redirect_params", lambda **kwargs: {"flow_id": ["different"], "broker_status": ["ok"]})
    monkeypatch.setattr(
        "pebble_tool.firebase_account.requests.post",
        lambda *a, **k: _FakeResponse({"flow_id": "f1", "auth_url": "http://auth"}, status_code=200),
    )
    args = Namespace(
        oauth_broker_base="http://broker",
        oauth_client_key="k",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
    )
    with pytest.raises(Exception, match="OAuth callback flow mismatch"):
        account._broker_start_session_auth(args)


def test_broker_start_session_auth_callback_error(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))

    monkeypatch.setattr(account, "_capture_local_redirect_params", lambda **kwargs: {"flow_id": ["f1"], "broker_status": ["error"], "broker_code": ["DENIED"]})
    monkeypatch.setattr(
        "pebble_tool.firebase_account.requests.post",
        lambda *a, **k: _FakeResponse({"flow_id": "f1", "auth_url": "http://auth"}, status_code=200),
    )
    args = Namespace(
        oauth_broker_base="http://broker",
        oauth_client_key="k",
        auth_host_name="localhost",
        auth_host_port=60000,
        no_open_browser=True,
    )
    with pytest.raises(Exception, match="OAuth provider callback failed"):
        account._broker_start_session_auth(args)


def test_get_user_info_repairs_cached_unknown(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))
    account._save_credentials(
        {
            "id_token": "id-token",
            "firebase_api_key": "api-key",
            "email": None,
            "firebase_user_id": None,
        }
    )
    with open(account._user_info_path, "w") as f:
        f.write('{"id": null, "name": "Unknown", "email": "unknown", "roles": null, "legacy_id": null}')

    monkeypatch.setattr(
        account,
        "_firebase_lookup_profile",
        lambda api_key, id_token: {
            "localId": "uid-99",
            "email": "repaired@example.com",
            "displayName": "Repair User",
            "identity_provider": "google.com",
        },
    )

    info = account._get_user_info()
    creds = account.get_credentials()
    assert info["email"] == "repaired@example.com"
    assert info["id"] == "uid-99"
    assert creds["identity_provider"] == "google.com"


def test_get_user_info_uses_me_endpoint_when_configured(monkeypatch, tmp_path):
    account = FirebaseAccount(str(tmp_path))
    account._save_credentials(
        {
            "id_token": "id-token",
            "firebase_api_key": "api-key",
            "email": "x@example.com",
            "firebase_user_id": "uid-x",
            "display_name": "X",
        }
    )
    if account._user_info is not None:
        account._user_info = None

    monkeypatch.setattr(firebase_account_module, "DEFAULT_FIREBASE_ME_URI", "https://example.com/me")
    monkeypatch.setattr(
        "pebble_tool.firebase_account.requests.get",
        lambda *a, **k: _FakeResponse(
            {"uid": "uid-from-me", "name": "FromME", "email": "fromme@example.com", "roles": ["dev"]},
            status_code=200,
        ),
    )

    info = account._get_user_info()
    assert info["id"] == "uid-from-me"
    assert info["email"] == "fromme@example.com"
    assert info["roles"] == ["dev"]


def test_firebase_logout_clears_storage(tmp_path, monkeypatch):
    account = FirebaseAccount(str(tmp_path))

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse({"uid": "uid-1", "name": "User"})

    monkeypatch.setattr("pebble_tool.firebase_account.requests.get", fake_get)
    account.login_with_token("firebase-token")
    assert account.is_logged_in

    account.logout()
    assert not account.is_logged_in
