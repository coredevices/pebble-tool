"""
:Description: The `pebble new-project --plugin` template yields a project pebble-tool accepts.
"""
from __future__ import annotations

import json
import os

from pebble_tool.commands.sdk.create import _copy_from_template
from pebble_tool.sdk.project import NpmProject

TEMPLATES = os.path.join(os.path.dirname(__file__), '..', 'pebble_tool', 'sdk', 'templates')


def test_plugin_template(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with open(os.path.join(TEMPLATES, 'templates.json')) as f:
        layout = json.load(f)

    _copy_from_template(layout, TEMPLATES, 'counter', ['plugin'])

    with open(tmp_path / 'counter' / 'package.json') as f:
        pebble = json.load(f)['pebble']
    assert pebble['projectType'] == 'plugin'
    assert (tmp_path / 'counter' / pebble['plugin']['script']).is_file()
    assert (tmp_path / 'counter' / 'wscript').is_file()
    # No watchapp: nothing for the C build to pick up.
    assert not (tmp_path / 'counter' / 'src' / 'c').exists()

    project = NpmProject(str(tmp_path / 'counter'))
    assert project.project_type == 'plugin'
