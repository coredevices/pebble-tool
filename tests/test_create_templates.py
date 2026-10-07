"""
Tests for the project templates used by `pebble new-project`.
"""
from __future__ import annotations

import json
import os

import pytest

from pebble_tool.commands.sdk.create import _copy_from_template

TEMPLATE_ROOT = os.path.join(os.path.dirname(__file__), "..", "pebble_tool", "sdk", "templates")


def _create(tmp_path, monkeypatch, options):
    with open(os.path.join(TEMPLATE_ROOT, "templates.json")) as f:
        layout = json.load(f)
    monkeypatch.chdir(tmp_path)
    _copy_from_template(layout, TEMPLATE_ROOT, "proj", options)
    return tmp_path / "proj"


@pytest.mark.parametrize("options", [["app", "ai"], ["moddable", "ai"]])
def test_ai_option_writes_agent_files(tmp_path, monkeypatch, options):
    project = _create(tmp_path, monkeypatch, options)
    agents = (project / "AGENTS.md").read_text()

    assert "## Verify your work" in agents
    assert "pebble screenshot --scale 6 --no-open" in agents
    assert "gabbro" in agents
    # CLAUDE.md imports AGENTS.md instead of duplicating it.
    assert (project / "CLAUDE.md").read_text().strip() == "@AGENTS.md"
    assert (project / ".cursor" / "rules" / "pebble.mdc").read_text() == agents


def test_without_ai_option_no_agent_files(tmp_path, monkeypatch):
    project = _create(tmp_path, monkeypatch, ["app"])
    assert not (project / "AGENTS.md").exists()
    assert not (project / "CLAUDE.md").exists()
