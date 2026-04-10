from __future__ import annotations

from argparse import Namespace

import pytest

from pebble_tool.commands.publish import PublishCommand
from pebble_tool.exceptions import ToolError


def test_platform_from_capture_path_parses_prefix():
    assert PublishCommand._platform_from_capture_path("/tmp/emery_1.0_20260222-211128.gif") == "emery"


def test_platform_from_capture_path_rejects_unexpected_name():
    with pytest.raises(ToolError):
        PublishCommand._platform_from_capture_path("/tmp/no-delimiter.gif")


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def test_resolve_app_id_from_me_uses_lookup_map(monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        assert method == "GET"
        return _FakeResponse(
            {
                "app_lookup": {
                    "by_app_uuid": {
                        "123e4567-e89b-12d3-a456-426614174000": "app_abc123",
                    }
                }
            }
        )

    monkeypatch.setattr("pebble_tool.commands.publish.requests.request", fake_request)

    payload = PublishCommand._get_me_context(
        api_base="http://localhost:3000",
        firebase_id_token="id-token",
    )
    assert payload["app_lookup"]["by_app_uuid"]["123e4567-e89b-12d3-a456-426614174000"] == "app_abc123"


def test_get_me_context_returns_none_when_developer_not_linked(monkeypatch):
    def fake_request(method, url, headers=None, json=None, timeout=None):
        assert method == "GET"
        return _FakeResponse({"code": "DEVELOPER_NOT_LINKED"}, status_code=403)

    monkeypatch.setattr("pebble_tool.commands.publish.requests.request", fake_request)

    payload = PublishCommand._get_me_context(
        api_base="http://localhost:3000",
        firebase_id_token="id-token",
    )
    assert payload is None


def test_lookup_app_id_case_insensitive_matches_uuid():
    app_id = PublishCommand._lookup_app_id_case_insensitive(
        {"ABCDEF12-3456-7890-ABCD-EF1234567890": "app_1"},
        "abcdef12-3456-7890-abcd-ef1234567890",
    )
    assert app_id == "app_1"


def test_default_category_key_watchface_is_none():
    assert PublishCommand._default_category_key({}, "watchface") is None


def test_collect_new_app_details_non_interactive_watchface(monkeypatch):
    monkeypatch.setattr(PublishCommand, "_git_remote_source_url", classmethod(lambda cls: "https://example.com/repo"))
    args = Namespace(
        non_interactive=True,
        description="A watchface",
        name=None,
        version=None,
        source=None,
        category=None,
        icon_small=None,
        icon_large=None,
    )
    details = PublishCommand._collect_new_app_details(
        args,
        {"app_name": "Face", "version": "1.2.3", "app_type": "watchface"},
        {},
    )
    assert details["name"] == "Face"
    assert details["version"] == "1.2.3"
    assert details["description"] == "A watchface"
    assert details["source"] == "https://example.com/repo"
    assert details["category"] is None


def test_collect_screenshot_assets_non_interactive_uses_local_files(monkeypatch, tmp_path):
    png = tmp_path / "emery_screenshot.png"
    gif = tmp_path / "emery_preview.gif"
    png.write_bytes(b"")
    gif.write_bytes(b"")

    args = Namespace(
        non_interactive=True,
        screenshots=[str(png), str(gif)],
        capture_gif_all_platforms=False,
        capture_all_platforms=False,
        v=0,
        sdk=None,
    )
    cmd = PublishCommand()
    screenshot_paths, gif_paths = [], []
    result_gifs, result_screenshots = cmd._collect_screenshot_assets(args, {}, allow_skip=False)
    assert result_screenshots == [str(png)]
    assert result_gifs == [str(gif)]


def test_collect_screenshot_assets_non_interactive_local_files_separates_gifs(tmp_path):
    files = [
        tmp_path / "emery_screenshot.png",
        tmp_path / "basalt_screenshot.png",
        tmp_path / "emery_preview.gif",
    ]
    for f in files:
        f.write_bytes(b"")

    args = Namespace(
        non_interactive=True,
        screenshots=[str(f) for f in files],
        capture_gif_all_platforms=False,
        capture_all_platforms=False,
        v=0,
        sdk=None,
    )
    cmd = PublishCommand()
    result_gifs, result_screenshots = cmd._collect_screenshot_assets(args, {}, allow_skip=False)
    assert len(result_screenshots) == 2
    assert len(result_gifs) == 1
    assert all(p.endswith(".gif") for p in result_gifs)


def test_collect_screenshot_assets_non_interactive_no_local_files_falls_back_to_emulator(monkeypatch):
    captured = {}

    def fake_capture(self, args):
        captured["called"] = True
        return ["emery_preview.gif"], ["emery_screenshot.png"]

    monkeypatch.setattr(PublishCommand, "_capture_with_emulator", fake_capture)

    args = Namespace(
        non_interactive=True,
        screenshots=None,
        capture_gif_all_platforms=False,
        capture_all_platforms=False,
        v=0,
        sdk=None,
    )
    cmd = PublishCommand()
    result_gifs, result_screenshots = cmd._collect_screenshot_assets(args, {}, allow_skip=False)
    assert captured.get("called") is True
    assert result_screenshots == ["emery_screenshot.png"]


def test_collect_new_app_details_non_interactive_requires_description():
    args = Namespace(
        non_interactive=True,
        description="",
        name=None,
        version=None,
        source=None,
        category=None,
        icon_small=None,
        icon_large=None,
    )
    with pytest.raises(ToolError):
        PublishCommand._collect_new_app_details(
            args,
            {"app_name": "App", "version": "1.0", "app_type": "watchapp"},
            {},
        )
