from __future__ import annotations

import json
import zipfile
from argparse import Namespace
from types import SimpleNamespace

import pytest

from pebble_tool.commands import oauth_connector
from pebble_tool.commands.publish import PublishCommand
from pebble_tool.exceptions import ToolError

UUID = "123e4567-e89b-12d3-a456-426614174000"
PROJECT = SimpleNamespace(uuid=UUID, version="1.0.0", target_platforms=["emery"], long_name="My Plugin",
                          project_dir="/tmp/my-plugin", is_watchface=False)


def _pbw(tmp_path, appinfo, binary=False):
    path = tmp_path / "app.pbw"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("appinfo.json", json.dumps(appinfo))
        if binary:
            zf.writestr("emery/pebble-app.bin", b"\0")
    return str(path)


def test_phone_only_plugin_is_plugin_type_with_oauth_slugs(tmp_path):
    pbw = _pbw(tmp_path, {"uuid": UUID, "versionLabel": "1.0.2", "plugin": {"oauth": {"my-service": {}}}})
    meta = PublishCommand._extract_pbw_metadata(pbw, PROJECT)
    assert meta["app_type"] == "plugin"
    assert meta["oauth_slugs"] == ["my-service"]


def test_watchapp_carrying_a_plugin_keeps_its_type(tmp_path):
    pbw = _pbw(tmp_path, {"uuid": UUID, "watchapp": {"watchface": False}, "plugin": {}}, binary=True)
    meta = PublishCommand._extract_pbw_metadata(pbw, PROJECT)
    assert meta["app_type"] == "watchapp"
    assert meta["oauth_slugs"] == []


def test_plugin_skips_screenshots_and_defaults_to_first_plugin_category():
    me = {"category_options": {"plugin": [{"slug": "productivity", "name": "Productivity"}]}}
    args = Namespace(non_interactive=True, screenshots=None, description="d", name=None, version=None,
                     source=None, category=None, icon_small=None, icon_large=None)
    assert PublishCommand()._collect_screenshot_assets(args, {"app_type": "plugin"}) == ([], [])
    details = PublishCommand._collect_new_app_details(args, {"app_type": "plugin", "app_name": "T"}, me, "1.0")
    assert details["category"] == "productivity"


def _args(**kw):
    base = dict(provider_label=None, authorize_url=None, token_url=None, scopes=None,
                token_auth_style=None, token_request_format=None, client_id=None, client_secret_file=None)
    base.update(kw)
    return Namespace(**base)


def test_new_connector_from_flags_and_env_secret(monkeypatch):
    monkeypatch.setenv(oauth_connector.SECRET_ENV, "s3cret")
    args = _args(provider_label="My Service", authorize_url="https://a", token_url="https://t", client_id="cid")
    body = oauth_connector.build_connector(args, "svc", None, interactive=False)
    assert body["authorize_url"] == "https://a" and body["provider_label"] == "My Service"
    assert body["client_id"] == "cid" and body["client_secret"] == "s3cret"


def test_connector_update_keeps_stored_settings_and_secret(monkeypatch):
    monkeypatch.delenv(oauth_connector.SECRET_ENV, raising=False)
    existing = {"provider_label": "My Service", "authorize_url": "https://a", "token_url": "https://t",
                "client_id": "cid", "has_secret": True}
    body = oauth_connector.build_connector(_args(scopes="x"), "svc", existing, interactive=False)
    assert "client_secret" not in body and body["scopes"] == "x" and body["token_url"] == "https://t"


def test_new_connector_needs_its_settings_and_a_secret(monkeypatch):
    monkeypatch.delenv(oauth_connector.SECRET_ENV, raising=False)
    with pytest.raises(ToolError):
        oauth_connector.build_connector(_args(client_id="cid"), "svc", None, interactive=False)
    args = _args(provider_label="S", authorize_url="https://a", token_url="https://t", client_id="cid")
    with pytest.raises(ToolError):
        oauth_connector.build_connector(args, "svc", None, interactive=False)
