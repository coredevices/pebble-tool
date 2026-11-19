from __future__ import absolute_import, print_function

import json
import os
import sys

from pebble_tool.commands.base import BaseCommand
from pebble_tool.exceptions import PebbleProjectException, ToolError
from pebble_tool.sdk import doctor
from pebble_tool.sdk.manager import _sdk_platform_module
from pebble_tool.sdk.project import PebbleProject


def _displays():
    """The displays of the platforms of the active SDK, with those every SDK had."""
    displays = dict(doctor.DISPLAYS)
    try:
        for name, platform in _sdk_platform_module().pebble_platforms.items():
            display = platform['DESCRIPTOR']['display']
            displays[name] = (display['width'], display['height'])
    except Exception:
        pass
    return displays


class DoctorCommand(BaseCommand):
    """Looks for what ties the app's C code to one display size or one watch's hardware."""
    command = 'doctor'
    epilog = ("A finding is not an error: an app built for a fixed display size works as it is.\n"
              "An app that is to run natively on every display has none of them left.\n"
              "A line with the comment '{}' is skipped.".format(doctor.IGNORE))

    def __call__(self, args):
        super(DoctorCommand, self).__call__(args)
        if args.paths:
            paths = []
            for path in args.paths:
                if os.path.isdir(path):
                    for root, dirs, names in os.walk(path):
                        dirs.sort()
                        paths += [os.path.join(root, name) for name in sorted(names)
                                  if name.endswith(doctor.SOURCE_SUFFIXES)]
                elif os.path.isfile(path):
                    paths.append(path)
                else:
                    raise ToolError("{} is not a file or a directory.".format(path))
            base = None
        else:
            try:
                base = PebbleProject().project_dir
            except PebbleProjectException:
                raise ToolError("This is not a Pebble project: run this in one, or name the files to check.")
            paths = doctor.source_files(base)

        findings = doctor.check_files(paths, relative_to=base, displays=_displays())
        warnings = sum(1 for f in findings if f.level == doctor.WARNING)

        if args.json:
            print(json.dumps([f._asdict() for f in findings], indent=2))
        else:
            for f in findings:
                print("{}:{}:{}: {}: {} [{}]".format(f.path, f.line, f.column, f.level, f.message, f.rule))
            if findings:
                print()
            print("{} file{} checked: {} warning{}, {} note{}.".format(
                len(paths), '' if len(paths) == 1 else 's',
                warnings, '' if warnings == 1 else 's',
                len(findings) - warnings, '' if len(findings) - warnings == 1 else 's'))
        if args.strict and warnings:
            sys.exit(1)

    @classmethod
    def add_parser(cls, parser):
        parser = super(DoctorCommand, cls).add_parser(parser)
        parser.add_argument('paths', nargs='*',
                            help="Files or directories to check instead of the project's sources.")
        parser.add_argument('--json', action='store_true', help="Print the findings as JSON.")
        parser.add_argument('--strict', action='store_true', help="Fail if there is a warning.")
        return parser
