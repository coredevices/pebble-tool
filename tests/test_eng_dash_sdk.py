"""
Unit tests for pebble_tool/sdk/eng_dash.py — the thin eng-dash client
pebble sdk commands go through. Covers env-var base URL handling, HTTP
status mapping (404 -> None, >=400 -> SDKInstallError), host → toolchain
kind mapping, and toolchain picker fallback.
"""

import os
import unittest
from unittest import mock

import requests

from pebble_tool.sdk import eng_dash
from pebble_tool.exceptions import SDKInstallError


class EngDashBaseTests(unittest.TestCase):
    def test_default_base(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PEBBLE_ENG_DASH_BASE", None)
            self.assertEqual(eng_dash.eng_dash_base(), "https://dash.repebble.com")

    def test_env_override_strips_trailing_slash(self):
        with mock.patch.dict(os.environ, {"PEBBLE_ENG_DASH_BASE": "http://localhost:3006/"}):
            self.assertEqual(eng_dash.eng_dash_base(), "http://localhost:3006")


class EngDashGetTests(unittest.TestCase):
    def _mock_response(self, status, body=None):
        resp = mock.Mock(spec=requests.Response)
        resp.status_code = status
        resp.text = str(body) if body is not None else ""
        resp.json.return_value = body if body is not None else {}
        return resp

    def test_404_returns_none(self):
        with mock.patch("pebble_tool.sdk.eng_dash.requests.get", return_value=self._mock_response(404)):
            self.assertIsNone(eng_dash.latest())

    def test_500_raises(self):
        with mock.patch("pebble_tool.sdk.eng_dash.requests.get", return_value=self._mock_response(500, "boom")):
            with self.assertRaises(SDKInstallError):
                eng_dash.latest()

    def test_bearer_header_sent(self):
        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return self._mock_response(200, {"version": "4.5.0"})

        with mock.patch("pebble_tool.sdk.eng_dash.requests.get", side_effect=fake_get):
            payload = eng_dash.latest(token="abc.def.ghi")
        self.assertEqual(payload["version"], "4.5.0")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer abc.def.ghi")
        self.assertTrue(captured["url"].endswith("/api/sdk/latest"))

    def test_requestexception_wraps_to_sdkerror(self):
        with mock.patch("pebble_tool.sdk.eng_dash.requests.get", side_effect=requests.RequestException("no net")):
            with self.assertRaises(SDKInstallError):
                eng_dash.list_releases()


class ToolchainKindTests(unittest.TestCase):
    def _case(self, system, machine, expected_kind):
        with mock.patch("platform.system", return_value=system), mock.patch("platform.machine", return_value=machine):
            kind, label = eng_dash.toolchain_kind_for_host()
            self.assertEqual(kind, expected_kind)
            self.assertEqual(label, expected_kind)

    def test_mac_arm64(self):
        self._case("Darwin", "arm64", "mac-arm64")

    def test_mac_x86_64(self):
        self._case("Darwin", "x86_64", "mac-x86_64")

    def test_linux_x86_64(self):
        self._case("Linux", "x86_64", "linux-x86_64")

    def test_linux_aarch64(self):
        self._case("Linux", "aarch64", "linux-aarch64")

    def test_unknown_host_returns_none(self):
        with mock.patch("platform.system", return_value="Windows"), mock.patch("platform.machine", return_value="AMD64"):
            self.assertEqual(eng_dash.toolchain_kind_for_host(), (None, None))


class PickToolchainTests(unittest.TestCase):
    def test_exact_match_wins(self):
        with mock.patch("platform.system", return_value="Darwin"), mock.patch("platform.machine", return_value="arm64"):
            tc, label = eng_dash.pick_toolchain({
                "mac-arm64": {"url": "u1"},
                "mac-x86_64": {"url": "u2"},
            })
        self.assertEqual(label, "mac-arm64")
        self.assertEqual(tc["url"], "u1")

    def test_prefix_fallback_when_no_exact(self):
        with mock.patch("platform.system", return_value="Linux"), mock.patch("platform.machine", return_value="aarch64"):
            tc, label = eng_dash.pick_toolchain({
                "linux-x86_64": {"url": "u1"},
            })
        self.assertEqual(label, "linux-x86_64")

    def test_no_match_returns_none(self):
        with mock.patch("platform.system", return_value="Darwin"), mock.patch("platform.machine", return_value="arm64"):
            tc, label = eng_dash.pick_toolchain({"linux-x86_64": {"url": "u1"}})
        self.assertIsNone(tc)
        self.assertIsNone(label)

    def test_empty_returns_none(self):
        tc, label = eng_dash.pick_toolchain({})
        self.assertIsNone(tc)
        self.assertIsNone(label)


class SDKManagerUseEngDashTests(unittest.TestCase):
    def test_eng_dash_disabled_when_sdk_server_env_set(self):
        from pebble_tool.sdk.manager import SDKManager
        with mock.patch.dict(os.environ, {"PEBBLE_SDK_SERVER": "https://staging.example"}):
            manager = SDKManager(sdk_dir="/tmp/pebble-sdk-test")
            self.assertFalse(manager.use_eng_dash)

    def test_eng_dash_disabled_when_download_server_overridden(self):
        from pebble_tool.sdk.manager import SDKManager
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PEBBLE_SDK_SERVER", None)
            manager = SDKManager(sdk_dir="/tmp/pebble-sdk-test")
            manager.DOWNLOAD_SERVER = "https://staging.example"
            self.assertFalse(manager.use_eng_dash)

    def test_eng_dash_enabled_by_default(self):
        from pebble_tool.sdk.manager import SDKManager
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PEBBLE_SDK_SERVER", None)
            manager = SDKManager(sdk_dir="/tmp/pebble-sdk-test")
            self.assertTrue(manager.use_eng_dash)


class ToolchainFlattenTests(unittest.TestCase):
    def test_platform_only_tarball_under_arch_label(self):
        # eng-dash may serve the legacy toolchain-mac.tar.gz in the
        # mac-arm64 slot; extraction must not assume the root dir name.
        import io
        import tarfile
        import tempfile
        from pebble_tool.sdk.manager import SDKManager

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            info = tarfile.TarInfo("toolchain-mac/bin/gcc")
            info.size = 2
            t.addfile(info, io.BytesIO(b"hi"))
        buf.seek(0)

        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "4.4"))
            SDKManager(sdk_dir=d)._install_toolchain_from_handle(buf, "4.4", "mac-arm64")
            self.assertTrue(os.path.exists(os.path.join(d, "4.4", "toolchain", "bin", "gcc")))
            self.assertFalse(os.path.exists(os.path.join(d, "4.4", "toolchain", "toolchain-mac")))


if __name__ == "__main__":
    unittest.main()
