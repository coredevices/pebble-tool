
__author__ = 'katharine'

import os.path
import sys

from pebble_tool.sdk import sdk_path
from pebble_tool.exceptions import PebbleProjectException, ToolError
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.commands.base import BaseCommand


class AnalyseSizeCommand(SDKProjectCommand):
    """Analyze the size of your pebble app."""
    command = "analyze-size"

    def __call__(self, args):
        BaseCommand.__call__(self, args)
        self.add_arm_tools_to_path()
        sys.path.append(os.path.join(sdk_path(), 'pebble', 'common', 'tools'))

        paths = []
        if args.elf_path is None:
            try:
                project = PebbleProject()
                paths = ['build/{}/pebble-app.elf'.format(x) for x in project.target_platforms]
            except PebbleProjectException:
                raise ToolError("This is not a valid Pebble project. Please instead specify a valid elf path.")
            except Exception as e:
                print(e)
        else:
            paths.append(args.elf_path)

        # This is Super Special Magic of some form that comes from the SDK.
        import binutils

        for path in paths:
            print("\n======{}======".format(path))
            sections = binutils.analyze_elf(path, 'bdt', use_fast_nm=True)

            for s in sections.values():
                s.pprint(args.summary, args.verbose)

    @classmethod
    def add_parser(cls, parser):
        parser = super(AnalyseSizeCommand, cls).add_parser(parser)
        parser.add_argument('elf_path', type=str, nargs='?', help='Path to the elf file to analyze')
        parser.add_argument('--summary', action='store_true', help='Disable a single line per section')
        parser.add_argument('--verbose', action='store_true', help='Disable a per-symbol breakdown')
        return parser
