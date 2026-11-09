import textwrap

import pytest

from pebble_tool.sdk.cmake_convert import ConversionError, convert_wscript

DEFAULT_WSCRIPT = textwrap.dedent("""\
    import os.path

    top = '.'
    out = 'build'

    def options(ctx):
        ctx.load('pebble_sdk')

    def configure(ctx):
        ctx.load('pebble_sdk')

    def build(ctx):
        ctx.load('pebble_sdk')

        build_worker = os.path.exists('worker_src')
        binaries = []

        cached_env = ctx.env
        for platform in ctx.env.TARGET_PLATFORMS:
            ctx.env = ctx.all_envs[platform]
            ctx.set_group(ctx.env.PLATFORM_NAME)
            app_elf = '{}/pebble-app.elf'.format(ctx.env.BUILD_DIR)
            ctx.pbl_build(source=ctx.path.ant_glob('src/c/**/*.c'), target=app_elf, bin_type='app')

            if build_worker:
                worker_elf = '{}/pebble-worker.elf'.format(ctx.env.BUILD_DIR)
                binaries.append({'platform': platform, 'app_elf': app_elf, 'worker_elf': worker_elf})
                ctx.pbl_build(source=ctx.path.ant_glob('worker_src/c/**/*.c'),
                              target=worker_elf,
                              bin_type='worker')
            else:
                binaries.append({'platform': platform, 'app_elf': app_elf})
        ctx.env = cached_env

        ctx.set_group('bundle')
        ctx.pbl_bundle(binaries=binaries,
                       js=ctx.path.ant_glob(['src/pkjs/**/*.js',
                                             'src/pkjs/**/*.json',
                                             'src/common/**/*.js']),
                       js_entry_file='src/pkjs/index.js')
    """)


def project(tmp_path, wscript, files):
    (tmp_path / 'wscript').write_text(wscript)
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('')
    return str(tmp_path)


def test_default_wscript(tmp_path) -> None:
    result = convert_wscript(project(tmp_path, DEFAULT_WSCRIPT, [
        'src/c/main.c', 'src/c/ui/window.c', 'src/c/notes.txt', 'worker_src/c/worker.c',
        'src/pkjs/index.js', 'src/pkjs/lib/weather.js']), 'my-app')
    assert not result.review
    assert result.cmake == textwrap.dedent("""\
        cmake_minimum_required(VERSION 3.20)

        # Before project(): the SDK selects the toolchain.
        find_package(PebbleSDK REQUIRED HINTS $ENV{PEBBLE_SDK})

        project(my-app C)

        pebble_app(SOURCES
          src/c/main.c
          src/c/ui/window.c
        )

        pebble_worker(SOURCES
          worker_src/c/worker.c
        )

        pebble_js(
          SOURCES
            src/pkjs/index.js
            src/pkjs/lib/weather.js
          ENTRY src/pkjs/index.js
        )
        """)


def test_no_worker_no_js(tmp_path) -> None:
    result = convert_wscript(project(tmp_path, DEFAULT_WSCRIPT, ['src/c/main.c']), 'face')
    assert 'pebble_worker' not in result.cmake and 'pebble_js' not in result.cmake


def test_sdk3_layout_without_multi_js(tmp_path) -> None:
    wscript = DEFAULT_WSCRIPT.replace("'src/c/**/*.c'", "'src/**/*.c'").replace(
        "js_entry_file='src/pkjs/index.js'", "js_entry_file='src/js/pebble-js-app.js'").replace(
        "['src/pkjs/**/*.js',\n", "['src/js/**/*.js',\n")
    result = convert_wscript(project(tmp_path, wscript, ['src/main.c', 'src/js/pebble-js-app.js']),
                             'old', enable_multi_js=False)
    assert 'src/main.c' in result.cmake
    assert 'src/js/pebble-js-app.js' in result.cmake and 'ENTRY' not in result.cmake


def test_flags_excludes_and_what_is_left(tmp_path) -> None:
    wscript = DEFAULT_WSCRIPT.replace(
        "ctx.path.ant_glob('src/c/**/*.c')", "ctx.path.ant_glob('src/c/**/*.c', excl=['src/c/skip.c'])"
    ).replace(
        "    ctx.load('pebble_sdk')\n\n    build_worker",
        "    ctx.load('pebble_sdk')\n    ctx.env.append_value('CFLAGS', ['-flto'])\n"
        "    ctx.env.DEFINES.append('SHOW_SECONDS=1')\n    os.system('tools/make_keys.py')\n\n    build_worker")
    result = convert_wscript(project(tmp_path, wscript, ['src/c/main.c', 'src/c/skip.c']), 'app')
    assert 'src/c/skip.c' not in result.cmake
    assert 'target_compile_options(app INTERFACE -flto)' in result.cmake
    assert 'target_compile_definitions(app INTERFACE SHOW_SECONDS=1)' in result.cmake
    assert [text for _, text in result.review] == ["os.system('tools/make_keys.py')"]
    assert "#   line" in result.cmake and "os.system('tools/make_keys.py')" in result.cmake


def test_package(tmp_path) -> None:
    wscript = textwrap.dedent("""\
        def options(ctx):
            ctx.load('pebble_sdk_lib')

        def configure(ctx):
            ctx.load('pebble_sdk_lib')

        def build(ctx):
            ctx.load('pebble_sdk_lib')
            for platform in ctx.env.TARGET_PLATFORMS:
                ctx.env = ctx.all_envs[platform]
                lib_name = '{}/{}'.format(ctx.env.BUILD_DIR, ctx.env.PROJECT_INFO['name'])
                ctx.pbl_build(source=ctx.path.ant_glob('src/c/**/*.c'), target=lib_name, bin_type='lib')
            ctx.pbl_bundle(includes=ctx.path.ant_glob('include/**/*.h'),
                           js=ctx.path.ant_glob(['src/js/**/*.js', 'src/js/**/*.json']),
                           bin_type='lib')
            if ctx.cmd == 'clean':
                for n in ctx.path.ant_glob(['dist/**/*', 'dist.zip'], quiet=True):
                    n.delete()
        """)
    result = convert_wscript(project(tmp_path, wscript, ['src/c/lib.c', 'include/lib.h', 'src/js/index.js']),
                             'pebble-lib', project_type='package')
    assert not result.review
    assert 'pebble_package(\n  SOURCES\n    src/c/lib.c\n  HEADERS\n    include/lib.h\n)' in result.cmake
    assert 'target_include_directories(package INTERFACE include)' in result.cmake
    assert 'pebble_js(\n  SOURCES\n    src/js/index.js\n)' in result.cmake


def test_nothing_to_build(tmp_path) -> None:
    with pytest.raises(ConversionError):
        convert_wscript(project(tmp_path, DEFAULT_WSCRIPT, []), 'empty')


def test_implicit_include_directories_and_names(tmp_path) -> None:
    root = project(tmp_path, DEFAULT_WSCRIPT, ['src/c/main.c', 'src/libs/assist.h', 'src/c/local.h'])
    (tmp_path / 'src/c/main.c').write_text('#include <pebble.h>\n#include "local.h"\n#include <libs/assist.h>\n')
    result = convert_wscript(root, 'Sliding Text')
    assert 'project(Sliding-Text C)' in result.cmake
    # Only what an #include needs: local.h is next to main.c, pebble.h is the SDK's.
    assert 'target_include_directories(app INTERFACE src)' in result.cmake


def test_own_functions_are_reported_once(tmp_path) -> None:
    wscript = DEFAULT_WSCRIPT + textwrap.dedent("""\

        def generate_keys(task):
            keys = json.load(open(task.inputs[0].abspath()))
            open(task.outputs[0].abspath(), 'w').write(str(keys))
        """)
    result = convert_wscript(project(tmp_path, wscript, ['src/c/main.c']), 'app')
    assert [text for _, text in result.review] == ['def generate_keys(task):']
