"""pebble doctor: what ties an app's source to one display or one watch's hardware."""

from __future__ import annotations

import argparse
import json

import pytest

from pebble_tool.commands.sdk.project.doctor import DoctorCommand
from pebble_tool.sdk import doctor


def rules(source: str) -> list[tuple[int, str, str]]:
    return [(f.line, f.level, f.rule) for f in doctor.check_source(source)]


def test_clean_source_has_no_findings() -> None:
    source = '''
#include <pebble.h>

static void prv_load(Window *window) {
  GRect bounds = layer_get_bounds(window_get_root_layer(window));
  TextLayer *text = text_layer_create(GRect(0, 20, bounds.size.w, 40));
  text_layer_set_text(text, PBL_IF_MICROPHONE_ELSE("speak", "type"));
  window_set_background_color(window, PBL_IF_COLOR_ELSE(GColorBlue, GColorBlack));
}
'''
    assert rules(source) == []


def test_display_size_written_out() -> None:
    assert rules('GRect frame = GRect(0, 0, 144, 168);') == [(1, 'warning', 'display-size')]
    finding = doctor.check_source('GRect frame = GRect(0, 0, 144, 168);')[0]
    assert finding.column == 27
    assert 'aplite, basalt, diorite and flint' in finding.message


def test_square_display_takes_the_number_twice() -> None:
    assert rules('layer = layer_create(GRect(0, 0, 180, 180));') == [(1, 'warning', 'display-size')]
    # Once, it is a note at most: 180 is a common number.
    assert rules('int degrees = 180;') == []
    assert rules('GRect r = GRect(0, 0, 180, 20);') == [(1, 'note', 'display-size')]


def test_half_the_display() -> None:
    finding = doctor.check_source('GPoint center = GPoint(72, 84);')[0]
    assert (finding.level, finding.rule) == ('warning', 'display-size')
    assert 'half the display size' in finding.message


def test_one_side_of_a_display_in_a_rectangle() -> None:
    assert rules('text = text_layer_create(GRect(0, 50, 144, 30));') == [(1, 'note', 'display-size')]
    # Not where no size is expected.
    assert rules('int steps = 144;') == []
    assert rules('if (count > 168) { return; }') == []


def test_numbers_in_comments_and_strings_are_not_code() -> None:
    source = '''
// The display is 144 by 168.
/* GRect(0, 0, 144, 168) */
static const char *s_size = "144x168";
'''
    assert rules(source) == []


def test_numbers_that_only_contain_a_size() -> None:
    assert rules('GRect r = GRect(0, 0, 1440, 1680); float f = 144.5; int x = 0x144 + v168;') == []


def test_display_size_macros() -> None:
    source = '''
static void prv_draw(void) {
  int width = PBL_DISPLAY_WIDTH;
}
#if PBL_DISPLAY_HEIGHT > 180
#endif
'''
    assert rules(source) == [(3, 'warning', 'display-size'), (5, 'warning', 'display-size')]


def test_feature_decided_by_the_preprocessor() -> None:
    source = '''
#if defined(PBL_MICROPHONE)
static void prv_dictate(void) {}
#endif
#ifdef PBL_HEALTH
#elif PBL_COMPASS
#endif
#ifndef PBL_COLOR
#endif
'''
    assert rules(source) == [
        (2, 'warning', 'feature-at-build-time'),
        (5, 'warning', 'feature-at-build-time'),
        (6, 'warning', 'feature-at-build-time'),
    ]


def test_platform_named_by_the_preprocessor() -> None:
    source = '''
#if defined(PBL_PLATFORM_EMERY) || PBL_PLATFORM_GABBRO
#endif
int width = PBL_PLATFORM_SWITCH(PBL_PLATFORM_TYPE_CURRENT, 1, 2, 3, 4, 5, 6, 7);
'''
    assert rules(source) == [(2, 'warning', 'platform-name'), (2, 'warning', 'platform-name')]


def test_feature_macro_where_a_constant_is_needed() -> None:
    source = '''
static const int s_rows = PBL_IF_MICROPHONE_ELSE(3, 2);

static const char *s_titles[] = {
  "one",
  PBL_IF_HEALTH_ELSE("steps", "none"),
};

static void prv_run(int kind) {
  static int s_count = PBL_IF_MICROPHONE_ELSE(1, 0);
  int rows = PBL_IF_MICROPHONE_ELSE(3, 2);
  switch (kind) {
    case PBL_IF_HEALTH_ELSE(1, 2):
      rows += PBL_IF_HEALTH_ELSE(1, 0);
      break;
  }
}

#if PBL_IF_MICROPHONE_ELSE(1, 0)
#endif
'''
    assert rules(source) == [
        (2, 'warning', 'feature-constant'),
        (6, 'warning', 'feature-constant'),
        (10, 'warning', 'feature-constant'),
        (13, 'warning', 'feature-constant'),
        (19, 'warning', 'feature-constant'),
    ]
    reasons = [f.message for f in doctor.check_source(source)]
    assert 'outside a function' in reasons[0]
    assert 'static variable' in reasons[2]
    assert 'case label' in reasons[3]
    assert 'preprocessor condition' in reasons[4]


def test_smartstrap_macro_is_decided_at_build_time() -> None:
    source = '''
static void prv_run(void) {
  int straps = PBL_IF_SMARTSTRAP_ELSE(1, 0);
}
'''
    assert rules(source) == [(3, 'warning', 'feature-at-build-time')]
    assert 'PlatformFeatureSmartstrap' in doctor.check_source(source)[0].message


def test_shape_and_colour_macros_stay_constants() -> None:
    source = '''
static const int s_inset = PBL_IF_ROUND_ELSE(12, 0);
static const GColor8 s_colour = {.argb = COLOR_FALLBACK(0xC3, 0xFF)};
#if defined(PBL_ROUND) || defined(PBL_COLOR)
#endif
'''
    assert rules(source) == []


def test_watch_model() -> None:
    assert rules('if (watch_info_get_model() == WATCH_INFO_MODEL_PEBBLE_TIME_2) {}') == [
        (1, 'note', 'watch-model')]


def test_a_line_can_be_skipped() -> None:
    assert rules('GRect frame = GRect(0, 0, 144, 168);  // pebble-doctor: ignore') == []


def test_displays_of_the_sdk() -> None:
    displays = dict(doctor.DISPLAYS, slate=(240, 320))
    findings = doctor.check_source('GSize size = GSize(240, 320);', displays=displays)
    assert [(f.level, f.rule) for f in findings] == [('warning', 'display-size')]
    assert 'slate' in findings[0].message
    assert doctor.check_source('GSize size = GSize(240, 320);') == []


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / 'src' / 'c').mkdir(parents=True)
    (tmp_path / 'worker_src' / 'c').mkdir(parents=True)
    (tmp_path / 'build').mkdir()
    (tmp_path / 'src' / 'c' / 'main.c').write_text('GRect frame = GRect(0, 0, 144, 168);\n')
    (tmp_path / 'src' / 'c' / 'ok.c').write_text('int main(void) { return 0; }\n')
    (tmp_path / 'worker_src' / 'c' / 'worker.c').write_text('#ifdef PBL_HEALTH\n#endif\n')
    (tmp_path / 'build' / 'generated.c').write_text('GRect frame = GRect(0, 0, 144, 168);\n')
    (tmp_path / 'package.json').write_text(json.dumps({
        'name': 'app', 'author': 'a', 'version': '1.0.0', 'keywords': ['pebble-app'],
        'pebble': {'displayName': 'app', 'uuid': '5e7b0c11-93a4-4a52-9d0c-1b6f2f0c5a01', 'sdkVersion': '3',
                   'targetPlatforms': ['emery'], 'watchapp': {'watchface': False},
                   'resources': {'media': []}}}))
    (tmp_path / 'CMakeLists.txt').write_text('')
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*argv):
    args = argparse.Namespace(v=0, paths=list(argv), json=False, strict=False)
    return args


def test_command_checks_the_project_sources(project, capsys) -> None:
    DoctorCommand()(run())
    out = capsys.readouterr().out
    assert 'src/c/main.c:1:27: warning: 144 and 168 are the display size' in out
    assert 'worker_src/c/worker.c:1:8: warning: PBL_HEALTH' in out
    assert 'build/' not in out
    assert '3 files checked: 2 warnings, 0 notes.' in out


def test_command_takes_paths(project, capsys) -> None:
    DoctorCommand()(run('src/c/ok.c'))
    assert capsys.readouterr().out == '1 file checked: 0 warnings, 0 notes.\n'


def test_command_json(project, capsys) -> None:
    args = run()
    args.json = True
    DoctorCommand()(args)
    findings = json.loads(capsys.readouterr().out)
    assert [f['rule'] for f in findings] == ['display-size', 'feature-at-build-time']
    assert findings[0]['path'] == 'src/c/main.c'


def test_command_strict_fails_on_a_warning(project) -> None:
    args = run()
    args.strict = True
    with pytest.raises(SystemExit) as e:
        DoctorCommand()(args)
    assert e.value.code == 1
    args = run('src/c/ok.c')
    args.strict = True
    DoctorCommand()(args)
