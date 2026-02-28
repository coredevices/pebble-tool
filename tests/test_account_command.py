from __future__ import annotations

from argparse import Namespace

import requests

from pebble_tool.commands.account import LogInCommand, LogOutCommand


class _Resp:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class _FakeAccount:
    def __init__(self, logged_in=False):
        self.is_logged_in = logged_in
        self.email = None
        self.id = None
        self._creds = {}
        self.saved_creds = None
        self.logged_in_args = None
        self.login_token = None
        self.logged_out = False

    def get_credentials(self):
        return dict(self._creds)

    def get_access_token(self):
        return "id-token"

    def _save_credentials(self, creds):
        self.saved_creds = dict(creds)
        self._creds = dict(creds)

    def login(self, args):
        self.logged_in_args = args

    def login_with_token(self, token):
        self.login_token = token

    def logout(self):
        self.logged_out = True


def _args(**kw):
    base = {"v": 0, "status": False, "id_token": None}
    base.update(kw)
    return Namespace(**base)


def test_login_status_logged_out(monkeypatch, capsys):
    account = _FakeAccount(logged_in=False)
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    LogInCommand()(_args(status=True))

    out = capsys.readouterr().out
    assert "Firebase login status: logged out" in out


def test_login_status_logged_in_linked_shows_developer_id(monkeypatch, capsys):
    account = _FakeAccount(logged_in=True)
    account._creds = {"identity_provider": "dashboard", "firebase_user_id": "uid-123"}
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    def fake_get(url, headers=None, timeout=None):
        return _Resp(
            {
                "developer": {
                    "id": "55023b8faab67cd8c7000049",
                    "email": "dev@example.com",
                    "firebase_uid": "uid-123",
                }
            },
            status_code=200,
        )

    monkeypatch.setattr("pebble_tool.commands.account.requests.get", fake_get)

    LogInCommand()(_args(status=True))

    out = capsys.readouterr().out
    assert "Developer ID: 55023b8faab67cd8c7000049" in out
    assert "Developer link: linked" in out
    assert "Email: dev@example.com" in out
    assert account.saved_creds["email"] == "dev@example.com"


def test_login_status_not_linked(monkeypatch, capsys):
    account = _FakeAccount(logged_in=True)
    account._creds = {"identity_provider": "dashboard"}
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    def fake_get(url, headers=None, timeout=None):
        return _Resp({"code": "DEVELOPER_NOT_LINKED"}, status_code=403)

    monkeypatch.setattr("pebble_tool.commands.account.requests.get", fake_get)

    LogInCommand()(_args(status=True))

    out = capsys.readouterr().out
    assert "Developer link: not linked" in out


def test_login_status_check_failed_exception(monkeypatch, capsys):
    account = _FakeAccount(logged_in=True)
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    def fake_get(url, headers=None, timeout=None):
        raise requests.RequestException("boom")

    monkeypatch.setattr("pebble_tool.commands.account.requests.get", fake_get)

    LogInCommand()(_args(status=True))

    out = capsys.readouterr().out
    assert "Developer link: check failed" in out
    assert "Developer link error: boom" in out


def test_login_with_id_token(monkeypatch, capsys):
    account = _FakeAccount(logged_in=False)
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    LogInCommand()(_args(id_token="abc123"))

    out = capsys.readouterr().out
    assert "Successfully logged in with provided Firebase id token." in out
    assert account.login_token == "abc123"


def test_login_interactive_path_calls_account_login(monkeypatch):
    account = _FakeAccount(logged_in=False)
    monkeypatch.setattr("pebble_tool.commands.account.get_account", lambda auth_provider: account)

    args = _args()
    LogInCommand()(args)

    assert account.logged_in_args is args


def test_logout_command_logs_out_all(monkeypatch):
    firebase = _FakeAccount(logged_in=True)
    legacy = _FakeAccount(logged_in=True)

    def fake_get_account(auth_provider):
        return firebase if auth_provider == "firebase" else legacy

    monkeypatch.setattr("pebble_tool.commands.account.get_account", fake_get_account)

    LogOutCommand()(_args())

    assert firebase.logged_out is True
    assert legacy.logged_out is True


def test_logout_command_prints_when_nothing_to_do(monkeypatch, capsys):
    firebase = _FakeAccount(logged_in=False)
    legacy = _FakeAccount(logged_in=False)

    def fake_get_account(auth_provider):
        return firebase if auth_provider == "firebase" else legacy

    monkeypatch.setattr("pebble_tool.commands.account.get_account", fake_get_account)

    LogOutCommand()(_args())

    out = capsys.readouterr().out
    assert "You aren't logged in anyway." in out
