"""Porting a project's wscript to a CMakeLists.txt.

A wscript is a Python program, so what it builds is read out of its syntax tree rather than by
running it: the sources its ant_glob() patterns match today, the PebbleKit JS, and the compiler
and linker flags it appends. Whatever else it does cannot be translated, and is reported for the
developer to port by hand.
"""

import ast
import fnmatch
import glob
import os
import re

__all__ = ['convert_wscript', 'ConversionResult', 'ConversionError']

# Calls a wscript that does nothing of its own makes.
_KNOWN_CALLS = {
    'load', 'ant_glob', 'exists', 'set_env', 'set_group', 'format', 'append', 'delete',
    'pbl_program', 'pbl_build', 'pbl_worker', 'pbl_bundle',
}
_FLAG_COMMANDS = {
    'CFLAGS': 'target_compile_options',
    'DEFINES': 'target_compile_definitions',
    'LINKFLAGS': 'target_link_options',
}


class ConversionError(Exception):
    pass


class ConversionResult(object):
    """
    :ivar cmake: The text of the CMakeLists.txt.
    :ivar review: ``(line number, source line)`` of what the wscript does that was not carried
                  over, for the developer to port by hand.
    """
    def __init__(self, cmake, review):
        self.cmake = cmake
        self.review = review


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _call_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _keywords(call):
    return {k.arg: k.value for k in call.keywords if k.arg}


def _as_list(value):
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _expand(project_dir, patterns, excludes=()):
    """The files ant_glob() patterns match, relative to the project, sorted."""
    matches = set()
    for pattern in patterns:
        for path in glob.glob(os.path.join(project_dir, pattern), recursive=True):
            if os.path.isfile(path):
                matches.add(os.path.relpath(path, project_dir).replace(os.sep, '/'))
    return sorted(m for m in matches if not any(fnmatch.fnmatch(m, e) for e in excludes))


def _glob_files(project_dir, node):
    """The files a `ctx.path.ant_glob(...)` argument names, or None if it is anything else."""
    if node is None:
        return []
    if isinstance(node, ast.Call) and _call_name(node) == 'ant_glob' and node.args:
        patterns = _literal(node.args[0])
        if patterns is None:
            return None
        keywords = _keywords(node)
        excludes = _as_list(_literal(keywords['excl'])) if 'excl' in keywords else []
        return _expand(project_dir, _as_list(patterns), excludes)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _glob_files(project_dir, node.left)
        right = _glob_files(project_dir, node.right)
        return None if left is None or right is None else sorted(set(left + right))
    literal = _literal(node)
    if isinstance(literal, (list, tuple, str)):
        return [f for f in _as_list(literal) if os.path.isfile(os.path.join(project_dir, f))]
    return None


def _flag_setting(call):
    """(variable, values) of `ctx.env.append_value('CFLAGS', [...])`,
    `ctx.env.CFLAGS.append(...)` and `.extend([...])`, or None."""
    name = _call_name(call)
    func = call.func
    if name in ('append_value', 'append_unique', 'prepend_value') and len(call.args) == 2:
        variable, values = _literal(call.args[0]), _literal(call.args[1])
    elif (name in ('append', 'extend') and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute) and len(call.args) == 1):
        variable, values = func.value.attr, _literal(call.args[0])
    else:
        return None
    if variable not in _FLAG_COMMANDS or values is None:
        return None
    values = _as_list(values)
    return (variable, values) if all(isinstance(v, str) for v in values) else None


_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
# What waf put on the include path of every project, without being asked.
_IMPLICIT_INCLUDES = ('src', 'include', '.')


def _implicit_includes(project_dir, sources):
    """Which of waf's implicit include directories the sources rely on: those an #include only
    resolves through."""
    needed = set()
    seen = set()
    queue = list(sources)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(os.path.join(project_dir, path), errors='replace') as f:
                text = f.read()
        except (IOError, OSError):
            continue
        for name in _INCLUDE.findall(text):
            local = os.path.normpath(os.path.join(os.path.dirname(path), name))
            if os.path.isfile(os.path.join(project_dir, local)):
                queue.append(local)
                continue
            for directory in _IMPLICIT_INCLUDES:
                candidate = os.path.normpath(os.path.join(directory, name))
                if os.path.isfile(os.path.join(project_dir, candidate)):
                    needed.add(directory)
                    queue.append(candidate)
                    break
    return [d for d in _IMPLICIT_INCLUDES if d in needed]


def _project_name(name):
    """A name CMake takes for a project: no spaces or punctuation."""
    name = re.sub(r'[^A-Za-z0-9_.+-]+', '-', name).strip('-')
    return name or 'app'


def _cmake_list(command, target, items, keyword='SOURCES'):
    lines = ['{}({}'.format(command, ' '.join(filter(None, [target, keyword])))]
    lines += ['  {}'.format(item) for item in items]
    lines.append(')')
    return '\n'.join(lines)


def convert_wscript(project_dir, project_name, project_type='native', enable_multi_js=True):
    """
    Write what a project's wscript builds as a CMakeLists.txt.

    :param project_dir: The project, holding the wscript.
    :param project_name: The name CMake knows the project by.
    :param project_type: ``projectType`` from package.json.
    :param enable_multi_js: ``enableMultiJS`` from package.json.
    :rtype: ConversionResult
    """
    path = os.path.join(project_dir, 'wscript')
    with open(path) as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        raise ConversionError("wscript is not Python 3 this tool can read: {}".format(e))
    lines = source.splitlines()

    is_package = project_type == 'package'
    target = 'package' if is_package else 'app'
    app_sources, worker_sources, headers, js, js_entry = [], [], [], [], None
    includes, flags, review = [], [], {}
    handled = set()

    # A function of the wscript's own is reported once, not line by line.
    own = [node for node in tree.body
           if isinstance(node, (ast.FunctionDef, ast.ClassDef))
           and node.name not in ('options', 'configure', 'build')]

    def note(node):
        if any(o.lineno < node.lineno <= o.end_lineno for o in own):
            return
        review[node.lineno] = lines[node.lineno - 1].strip()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        keywords = _keywords(node)

        flag = _flag_setting(node)
        if flag is not None:
            flags.append(flag)
            handled.add(node.lineno)
            continue

        if name in ('pbl_program', 'pbl_build', 'pbl_worker'):
            files = _glob_files(project_dir, keywords.get('source'))
            if files is None:
                note(node)
                continue
            bin_type = _literal(keywords['bin_type']) if 'bin_type' in keywords else None
            if name == 'pbl_worker' or bin_type == 'worker':
                worker_sources = files
            else:
                app_sources = files
            if 'includes' in keywords:
                dirs = _literal(keywords['includes'])
                if dirs is None:
                    note(node)
                else:
                    includes += _as_list(dirs)
            unknown = set(keywords) - {'source', 'target', 'bin_type', 'includes'}
            if unknown:
                note(node)
        elif name == 'pbl_bundle':
            found = _glob_files(project_dir, keywords.get('js'))
            if found is None:
                note(node)
            else:
                js = [f for f in found if f.endswith(('.js', '.json'))]
            if 'js_entry_file' in keywords:
                js_entry = _literal(keywords['js_entry_file'])
            if 'includes' in keywords:
                found = _glob_files(project_dir, keywords['includes'])
                if found is None:
                    note(node)
                else:
                    headers = found
            unknown = set(keywords) - {'binaries', 'js', 'js_entry_file', 'includes', 'bin_type'}
            if unknown:
                note(node)
        elif name not in _KNOWN_CALLS:
            note(node)

    # Anything the wscript defines beyond the three functions waf calls is its own.
    for node in own:
        note(node)
    for line in handled:
        review.pop(line, None)

    if project_type != 'plugin' and not app_sources:
        raise ConversionError("Could not tell which C files the wscript builds; write the "
                              "CMakeLists.txt by hand (see the SDK documentation).")

    out = [
        'cmake_minimum_required(VERSION 3.20)\n\n'
        '# Before project(): the SDK selects the toolchain.\n'
        'find_package(PebbleSDK REQUIRED HINTS $ENV{PEBBLE_SDK})\n\n'
        + 'project({} C)'.format(_project_name(project_name)),
    ]
    if is_package:
        block = ['pebble_package(', '  SOURCES'] + ['    ' + s for s in app_sources]
        if headers:
            block += ['  HEADERS'] + ['    ' + h for h in headers]
        block.append(')')
        out.append('\n'.join(block))
        header_dirs = sorted({h.split('/')[0] for h in headers})
        if header_dirs:
            out.append('target_include_directories(package INTERFACE {})'.format(' '.join(header_dirs)))
    elif app_sources:
        out.append(_cmake_list('pebble_app', '', app_sources))
        if worker_sources:
            out.append(_cmake_list('pebble_worker', '', worker_sources))
    # waf had src, include and the project itself on the include path; keep those the code uses.
    for directory in _implicit_includes(project_dir, app_sources + worker_sources):
        if directory not in includes and not (is_package and directory == 'include'):
            includes.append(directory)
    if includes:
        out.append('target_include_directories({} INTERFACE {})'.format(
            target, ' '.join(dict.fromkeys(includes))))
        if worker_sources:
            out.append('target_include_directories(worker INTERFACE {})'.format(
                ' '.join(dict.fromkeys(includes))))
    for variable, values in flags:
        out.append('{}({} INTERFACE {})'.format(_FLAG_COMMANDS[variable], target, ' '.join(values)))
    if js:
        block = ['pebble_js(', '  SOURCES'] + ['    ' + j for j in js]
        # Without enableMultiJS the files are copied as they are, and there is no entry point.
        if js_entry and enable_multi_js and not is_package:
            block.append('  ENTRY {}'.format(js_entry))
        block.append(')')
        out.append('\n'.join(block))

    review = sorted(review.items())
    if review:
        out.append('\n'.join(
            ['# The wscript also did the following, which was not carried over. See "Porting from',
             '# waf" in the SDK documentation, and wscript.backup:']
            + ['#   line {}: {}'.format(number, text) for number, text in review]))
    return ConversionResult('\n\n'.join(out) + '\n', review)
