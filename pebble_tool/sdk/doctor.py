"""Finds what ties an app's C source to one display or to the hardware of one watch.

A fixed-size build is compiled for one platform, so a display size written into the source, or a
feature decided by the preprocessor, is right for the watch the binary runs on. A flexible binary
runs on every watch of its class: it lays out from the window bounds and asks for features when it
runs. This reads source the way a person would, without compiling it, so it works on any project and
for any platform.
"""

from __future__ import annotations

import os
import re
from collections import namedtuple

Finding = namedtuple('Finding', 'path line column level rule message')

WARNING = 'warning'
NOTE = 'note'

# The displays an app may have been written for, by platform.
DISPLAYS = {
    'aplite': (144, 168),
    'basalt': (144, 168),
    'chalk': (180, 180),
    'diorite': (144, 168),
    'emery': (200, 228),
    'flint': (144, 168),
    'gabbro': (260, 260),
}

# Features a flexible binary asks for when it runs. Shape and colour depth are part of its class, so
# PBL_IF_ROUND_ELSE, PBL_IF_COLOR_ELSE and COLOR_FALLBACK stay constants.
FEATURE_DEFINES = ('PBL_MICROPHONE', 'PBL_HEALTH', 'PBL_SMARTSTRAP', 'PBL_SMARTSTRAP_POWER', 'PBL_COMPASS',
                   'PBL_TOUCH', 'PBL_RGB_BACKLIGHT', 'PBL_SPEAKER', 'PBL_BAROMETER')
FEATURE_MACROS = ('PBL_IF_MICROPHONE_ELSE', 'PBL_IF_HEALTH_ELSE')
# Decided when the app is built, in a flexible build too.
BUILD_TIME_MACROS = {'PBL_IF_SMARTSTRAP_ELSE': 'PlatformFeatureSmartstrap'}

IGNORE = 'pebble-doctor: ignore'

SOURCE_DIRS = ('src', 'worker_src', 'include')
SOURCE_SUFFIXES = ('.c', '.h')

_NUMBER_RE = re.compile(r'(?<![\w.])(\d+)[uUlL]*(?![\w.])')
_GEOMETRY_RE = re.compile(r'\b(GRect|GSize|GPoint)\s*\(')
_SIZE_MACRO_RE = re.compile(r'\bPBL_DISPLAY_(WIDTH|HEIGHT)\b')
_FEATURE_MACRO_RE = re.compile(r'\b(%s)\b' % '|'.join(FEATURE_MACROS))
_BUILD_TIME_MACRO_RE = re.compile(r'\b(%s)\b' % '|'.join(BUILD_TIME_MACROS))
_FEATURE_DEFINE_RE = re.compile(r'\b(%s)\b' % '|'.join(FEATURE_DEFINES))
_PLATFORM_DEFINE_RE = re.compile(r'\bPBL_PLATFORM_(?!TYPE_CURRENT\b|SWITCH)[A-Z0-9_]+\b')
_MODEL_RE = re.compile(r'\bwatch_info_get_model\s*\(')
_CONDITION_RE = re.compile(r'^[ \t]*#[ \t]*(if|ifdef|ifndef|elif)\b')


def blank(text):
    """The source with comments and string and character literals turned into spaces, so that nothing
    in them is taken for code and every character stays where it was."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if text.startswith('//', i):
            end = text.find('\n', i)
            end = n if end < 0 else end
            out.append(' ' * (end - i))
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            end = n if end < 0 else end + 2
            out.append(''.join(ch if ch == '\n' else ' ' for ch in text[i:end]))
            i = end
        elif c in '"\'':
            end = i + 1
            while end < n and text[end] != c and text[end] != '\n':
                end += 2 if text[end] == '\\' else 1
            end = min(end + 1, n)
            out.append(c + ' ' * (end - i - 2) + c if end - i >= 2 else ' ')
            i = end
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _names(platforms):
    platforms = sorted(platforms)
    if len(platforms) == 1:
        return platforms[0]
    return '{} and {}'.format(', '.join(platforms[:-1]), platforms[-1])


class _Displays(object):
    def __init__(self, displays):
        self.sizes = {}
        for name, size in displays.items():
            self.sizes.setdefault(tuple(size), []).append(name)
        self.dimensions = {}
        for (width, height), names in self.sizes.items():
            self.dimensions.setdefault(width, set()).update(names)
            self.dimensions.setdefault(height, set()).update(names)

    def in_line(self, values):
        """The displays a line of numbers spells out: (size, platforms, how it is written)."""
        for (width, height), names in sorted(self.sizes.items()):
            for divisor, what in ((1, 'the display size'), (2, 'half the display size')):
                if width % divisor or height % divisor:
                    continue
                w, h = width // divisor, height // divisor
                if (values.count(w) >= 2) if w == h else (w in values and h in values):
                    yield (w, h), names, what


def _function_spans(code):
    """Where the source is inside a function body, as a list of (start, end)."""
    spans = []
    depth = 0
    start = None
    last = ''
    i = 0
    n = len(code)
    line_start = True
    while i < n:
        c = code[i]
        if line_start and code[i:].lstrip(' \t').startswith('#'):
            # A preprocessor line, with its continuations.
            while i < n:
                end = code.find('\n', i)
                end = n if end < 0 else end
                continued = code[i:end].rstrip().endswith('\\')
                i = end + 1
                if not continued:
                    break
            continue
        if c == '{':
            if depth == 0 and start is None and last == ')':
                start = i
                function_depth = depth
            depth += 1
        elif c == '}':
            depth -= 1
            if start is not None and depth == function_depth:
                spans.append((start, i))
                start = None
        if not c.isspace():
            last = c
        line_start = c == '\n'
        i += 1
    if start is not None:
        spans.append((start, n))
    return spans


def _statement_start(code, pos):
    start = max(code.rfind(ch, 0, pos) for ch in ';{}')
    return code[start + 1:pos].lstrip()


def _needs_constant(code, pos, spans):
    """Why the expression at pos has to be a constant, or None if it may be computed."""
    if not any(start < pos < end for start, end in spans):
        return 'outside a function'
    statement = _statement_start(code, pos)
    if re.match(r'(const\s+)?static\b', statement):
        return 'in the initializer of a static variable'
    if re.match(r'case\b[^:]*$', statement):
        return 'in a case label'
    return None


def check_source(text, path='', displays=None):
    """The findings in the text of one C file."""
    displays = _Displays(displays or DISPLAYS)
    code = blank(text)
    lines = text.split('\n')
    spans = _function_spans(code)
    findings = []

    def add(pos, level, rule, message):
        line = code.count('\n', 0, pos)
        if IGNORE in lines[line]:
            return
        column = pos - (code.rfind('\n', 0, pos) + 1)
        findings.append(Finding(path, line + 1, column + 1, level, rule, message))

    offset = 0
    for line in code.split('\n'):
        condition = _CONDITION_RE.match(line)
        if condition:
            for m in _FEATURE_DEFINE_RE.finditer(line):
                add(offset + m.start(), WARNING, 'feature-at-build-time',
                    '{} is decided when the app is built: a flexible binary also runs on watches that '
                    'differ in it, so ask when the app runs, or require the feature in targetPlatforms'
                    .format(m.group(1)))
            for m in _PLATFORM_DEFINE_RE.finditer(line):
                add(offset + m.start(), WARNING, 'platform-name',
                    '{} names a platform: a flexible binary is built for no platform by name, go by the '
                    'window bounds and the features instead'.format(m.group(0)))
        for m in _SIZE_MACRO_RE.finditer(line):
            add(offset + m.start(), WARNING, 'display-size',
                '{} is not defined in a flexible build: take the size from the window, '
                'layer_get_bounds(window_get_root_layer(window))'.format(m.group(0)))
        if not condition:
            _check_numbers(line, offset, displays, add)
        offset += len(line) + 1

    for m in _FEATURE_MACRO_RE.finditer(code):
        line_start = code.rfind('\n', 0, m.start()) + 1
        if _CONDITION_RE.match(code[line_start:m.start() + 1]):
            why = 'in a preprocessor condition'
        else:
            why = _needs_constant(code, m.start(), spans)
        if why:
            add(m.start(), WARNING, 'feature-constant',
                '{} is used {}, where only a constant works: in a flexible build it is asked when the '
                'app runs, so set the value in code that runs'.format(m.group(1), why))

    for m in _BUILD_TIME_MACRO_RE.finditer(code):
        add(m.start(), WARNING, 'feature-at-build-time',
            '{} is decided when the app is built: a flexible binary also runs on watches that differ in '
            'it, so ask platform_has_feature({}) when the app runs, or require the feature in '
            'targetPlatforms'.format(m.group(1), BUILD_TIME_MACROS[m.group(1)]))

    for m in _MODEL_RE.finditer(code):
        add(m.start(), NOTE, 'watch-model',
            'watch_info_get_model() does not know watches newer than the SDK: go by the window bounds '
            'and the features instead')

    return sorted(findings, key=lambda f: (f.line, f.column))


def _check_numbers(line, offset, displays, add):
    numbers = [(m.start(), int(m.group(1))) for m in _NUMBER_RE.finditer(line)]
    if not numbers:
        return
    values = [value for _, value in numbers]
    reported = set()
    for (w, h), names, what in displays.in_line(values):
        pos = next(p for p, value in numbers if value in (w, h))
        reported.update((w, h))
        size = '{} and {} are'.format(w, h) if w != h else '{} twice is'.format(w)
        add(offset + pos, WARNING, 'display-size',
            '{} {} of {}: lay out from the window bounds'.format(size, what, _names(names)))
    # A lone number is only taken for a size where one is expected.
    for m in _GEOMETRY_RE.finditer(line):
        end = _closing(line, m.end() - 1)
        for pos, value in numbers:
            if m.end() <= pos < end and value in displays.dimensions and value not in reported:
                reported.add(value)
                add(offset + pos, NOTE, 'display-size',
                    '{} in {}() is a side of the display of {}: if it stands for the display, take it '
                    'from the window bounds'.format(value, m.group(1), _names(displays.dimensions[value])))


def _closing(line, opening):
    depth = 0
    for i in range(opening, len(line)):
        if line[i] == '(':
            depth += 1
        elif line[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return len(line)


def source_files(project_dir):
    """The C sources and headers of a project."""
    files = []
    for directory in SOURCE_DIRS:
        for root, dirs, names in os.walk(os.path.join(project_dir, directory)):
            dirs.sort()
            files += [os.path.join(root, name) for name in sorted(names) if name.endswith(SOURCE_SUFFIXES)]
    return files


def check_files(paths, relative_to=None, displays=None):
    findings = []
    for path in paths:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
        shown = os.path.relpath(path, relative_to) if relative_to else path
        findings += check_source(text, shown, displays)
    return findings
