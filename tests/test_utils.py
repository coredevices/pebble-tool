"""Tests for utility modules."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from pebble_tool.util.versions import version_to_key


class TestVersionToKey:
    def test_valid_version(self):
        assert version_to_key("3.0.0") == (3, 0, 0, 0, 0, "")

    def test_version_with_beta(self):
        assert version_to_key("4.1.2-beta3") == (4, 1, 2, -2, 3, "")

    def test_version_with_rc(self):
        assert version_to_key("5.0.0-rc1") == (5, 0, 0, -1, 1, "")

    def test_version_with_dp(self):
        assert version_to_key("3.0.0-dp2") == (3, 0, 0, -3, 2, "")

    def test_invalid_version(self):
        assert version_to_key("notaversion") == (0, 0, 0, 0, 0, "notaversion")

    def test_major_only(self):
        assert version_to_key("3") == (3, 0, 0, 0, 0, "")

    def test_major_minor(self):
        assert version_to_key("3.1") == (3, 1, 0, 0, 0, "")


class TestConfig:
    def _make_config(self, tmp_path):
        from pebble_tool.util.config import Config
        with patch("pebble_tool.util.config.get_persist_dir", return_value=str(tmp_path)):
            return Config()

    def test_config_missing_file(self, tmp_path):
        config = self._make_config(tmp_path)
        assert config.content == {}

    def test_config_get_set(self, tmp_path):
        config = self._make_config(tmp_path)
        config.set("key", "value")
        assert config.get("key") == "value"
        assert config.get("missing", "default") == "default"

    def test_config_setdefault(self, tmp_path):
        config = self._make_config(tmp_path)
        result = config.setdefault("key", "default")
        assert result == "default"
        assert config.get("key") == "default"

    def test_config_save_and_reload(self, tmp_path):
        config = self._make_config(tmp_path)
        config.set("key", "value")
        config.save()
        assert os.path.exists(os.path.join(str(tmp_path), "settings.json"))
        with open(os.path.join(str(tmp_path), "settings.json")) as f:
            assert json.load(f) == {"key": "value"}
