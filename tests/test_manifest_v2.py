"""
:Description: `pebble convert-manifest` declares what a project's PebbleKit JS visibly uses.
"""
from __future__ import annotations

import os

from pebble_tool.sdk import manifest_v2

WEATHER_JS = """
var API = 'https://api.openweathermap.org/data/2.5/weather?q=';
navigator.geolocation.getCurrentPosition(function (pos) {
  var xhr = new XMLHttpRequest();
  xhr.open('GET', API + pos.coords.latitude);
});
fetch(`https://${server}/status`);
var bridge = 'http://192.168.1.20/api';
Pebble.getTimelineToken(function (token) {
  fetch('https://timeline-api.rebble.io/v1/user/pins/x', { headers: { 'X-User-Token': token } });
});
var svg = 'http://www.w3.org/2000/svg';
"""


def project(tmp_path, js=WEATHER_JS):
    (tmp_path / 'src' / 'pkjs').mkdir(parents=True)
    (tmp_path / 'src' / 'pkjs' / 'index.js').write_text(js)
    return str(tmp_path)


def test_scan_finds_what_the_javascript_uses(tmp_path) -> None:
    found = manifest_v2.scan(project(tmp_path), {'configPage': 'https://config.example.com/page.html'})
    assert sorted(found['Internet']) == ['api.openweathermap.org', 'config.example.com',
                                         'timeline-api.rebble.io']
    assert found['Internet']['config.example.com'] == 'package.json configPage'
    assert found['LocalNetwork'] == {'192.168.1.20': os.path.join('src', 'pkjs', 'index.js')}
    assert list(found['Location']) == ['navigator.geolocation']
    assert sorted(found['Timeline']) == ['Pebble.getTimelineToken', 'timeline-api.rebble.io']


def test_upgrade_a_legacy_project(tmp_path) -> None:
    pebble = {'manifestVersion': 1}
    changes = manifest_v2.upgrade(pebble, manifest_v2.scan(project(tmp_path), pebble))
    assert 'manifestVersion' not in pebble
    assert pebble['usesPermissions'] == [
        'Location', 'Timeline', 'LocalNetwork',
        {'name': 'Internet', 'parameters': {'domains': ['api.openweathermap.org', 'timeline-api.rebble.io']}},
    ]
    assert changes[0] == ('removed "manifestVersion": 1 (version 2 is the default)', None)
    assert ('usesPermissions: Location', 'navigator.geolocation in src/pkjs/index.js') in changes


def test_upgrade_merges_into_what_is_declared(tmp_path) -> None:
    pebble = {'usesPermissions': ['Location', {'name': 'Internet', 'parameters': {'domains': ['openweathermap.org']}}]}
    changes = manifest_v2.upgrade(pebble, manifest_v2.scan(project(tmp_path), pebble))
    # api.openweathermap.org is covered by its parent domain.
    assert pebble['usesPermissions'][1] == {
        'name': 'Internet', 'parameters': {'domains': ['openweathermap.org', 'timeline-api.rebble.io']}}
    assert [c for c, _ in changes] == ['usesPermissions: Timeline', 'usesPermissions: LocalNetwork',
                                       'usesPermissions: Internet timeline-api.rebble.io']


def test_internet_without_domains_is_left_alone(tmp_path) -> None:
    for declared in ('Internet', {'name': 'Internet'}):
        pebble = {'usesPermissions': [declared]}
        manifest_v2.upgrade(pebble, manifest_v2.scan(project(tmp_path / str(type(declared))), pebble))
        assert pebble['usesPermissions'][0] == declared
        assert not any(isinstance(p, dict) and p.get('parameters') for p in pebble['usesPermissions'])


def test_nothing_to_do(tmp_path) -> None:
    pebble = {}
    assert manifest_v2.upgrade(pebble, manifest_v2.scan(project(tmp_path, "console.log('hi');"), pebble)) == []
    assert pebble == {}

