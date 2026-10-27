"""Bringing a project to manifest version 2.

A version 1 pbw is a legacy app to the phone app, whose PebbleKit JS may reach the whole internet,
the phone's location and the timeline without declaring anything. A version 2 app gets only what
its `usesPermissions` declares, so converting declares what the JavaScript and C visibly use.
"""

import glob
import ipaddress
import os
import re
from urllib.parse import urlsplit

INTERNET = 'Internet'
LOCAL_NETWORK = 'LocalNetwork'
LOCATION = 'Location'
TIMELINE = 'Timeline'
WATCH_MICROPHONE = 'WatchMicrophone'

JS_GLOBS = ('src/pkjs/**/*.js', 'src/js/**/*.js', 'src/common/**/*.js')
C_GLOBS = ('src/c/**/*.c', 'src/c/**/*.h')

_URL = re.compile(r"""\b(?:https?|wss?)://[^\s'"`<>()\\]+""", re.IGNORECASE)
_GEOLOCATION = re.compile(r'\bgeolocation\b')
_TIMELINE_CALL = re.compile(r'\b(getTimelineToken|timelineSubscribe|timelineUnsubscribe|timelineSubscriptions)\b')
# Only the watchapp's C code can start the microphone.
_MICROPHONE_CALL = re.compile(r'\b(mic_capture_start|mic_stream_to_phone_start)\s*\(')
# The phone app answers these itself when it emulates the timeline web API, behind Timeline.
TIMELINE_API_HOSTS = ('timeline-api.rebble.io', 'timeline-api.getpebble.com')
# Namespace URLs, not requests.
_NOT_REQUESTED_HOSTS = ('www.w3.org',)


def scan(project_dir, pebble_info):
    """What the project's PebbleKit JS (and config page) and watchapp visibly use, as
    {permission: {what: where}}: domains for Internet and LocalNetwork, calls for the rest."""
    found = {INTERNET: {}, LOCAL_NETWORK: {}, LOCATION: {}, TIMELINE: {}, WATCH_MICROPHONE: {}}
    for path in _js_files(project_dir):
        where = os.path.relpath(path, project_dir)
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        for match in _URL.finditer(text):
            _add_url(found, match.group(0), where)
        if _GEOLOCATION.search(text):
            found[LOCATION].setdefault('navigator.geolocation', where)
        for match in _TIMELINE_CALL.finditer(text):
            found[TIMELINE].setdefault('Pebble.' + match.group(1), where)
    for path in _files(project_dir, C_GLOBS):
        where = os.path.relpath(path, project_dir)
        with open(path, encoding='utf-8', errors='replace') as f:
            for match in _MICROPHONE_CALL.finditer(f.read()):
                found[WATCH_MICROPHONE].setdefault(match.group(1) + '()', where)
    config_page = pebble_info.get('configPage')
    if isinstance(config_page, str):
        _add_url(found, config_page, 'package.json configPage')
    return found


def upgrade(pebble_info, found):
    """Make `pebble_info` (package.json's "pebble" object) version 2 and declare what `found` lists
    that it doesn't already. Returns what changed, as (change, where) pairs."""
    changes = []
    if pebble_info.get('manifestVersion') == 1:
        del pebble_info['manifestVersion']
        changes.append(('removed "manifestVersion": 1 (version 2 is the default)', None))

    uses = list(pebble_info.get('usesPermissions') or [])
    names = {p if isinstance(p, str) else p.get('name') for p in uses
             if isinstance(p, str) or not p.get('plugin')}
    for name in (LOCATION, TIMELINE, LOCAL_NETWORK, WATCH_MICROPHONE):
        if found[name] and name not in names:
            uses.append(name)
            what, where = next(iter(found[name].items()))
            changes.append(('usesPermissions: %s' % name, '%s in %s' % (what, where)))

    internet = _internet_entry(uses)
    # "Internet" without domains already covers them all.
    covers_all = (INTERNET in names and internet is None) or \
        (internet is not None and not (internet.get('parameters') or {}).get('domains'))
    if found[INTERNET] and not covers_all:
        if internet is None:
            internet = {'name': INTERNET, 'parameters': {'domains': []}}
            uses.append(internet)
        domains = internet.setdefault('parameters', {}).setdefault('domains', [])
        for host, where in sorted(found[INTERNET].items()):
            if not _covered(host, domains):
                domains.append(host)
                changes.append(('usesPermissions: Internet %s' % host, where))

    if uses:
        pebble_info['usesPermissions'] = uses
    return changes


def _js_files(project_dir):
    return _files(project_dir, JS_GLOBS)


def _files(project_dir, globs):
    paths = set()
    for pattern in globs:
        paths.update(glob.glob(os.path.join(project_dir, pattern), recursive=True))
    return sorted(paths)


def _add_url(found, url, where):
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return
    # A host assembled at runtime (`https://${server}/...`) can't be known here.
    if not host or '$' in host or '{' in host:
        return
    host = host.rstrip('.')
    if host in _NOT_REQUESTED_HOSTS:
        return
    if host in TIMELINE_API_HOSTS:
        found[TIMELINE].setdefault(host, where)
    found[LOCAL_NETWORK if _is_local(host) else INTERNET].setdefault(host, where)


def _is_local(host):
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.home.arpa', '.internal')):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    carrier_nat = ipaddress.ip_network('100.64.0.0/10')
    return (address.is_private or address.is_loopback or address.is_link_local
            or (address.version == 4 and address in carrier_nat))


def _internet_entry(uses):
    return next((p for p in uses if isinstance(p, dict) and p.get('name') == INTERNET and not p.get('plugin')),
                None)


def _covered(host, domains):
    """A declared domain covers itself and its subdomains."""
    for domain in domains:
        domain = str(domain).lower().lstrip('.').rstrip('.')
        if host == domain or host.endswith('.' + domain):
            return True
    return False
