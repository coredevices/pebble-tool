

import collections
import hashlib
import json
import os

from shutil import copy2

from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.exceptions import OutdatedProjectException, ToolError
from pebble_tool.sdk import manifest_v2
from pebble_tool.sdk.project import NpmProject
from pebble_tool.sdk import get_pebble_platforms, sdk_has_cmake
from pebble_tool.sdk.cmake_convert import ConversionError, convert_wscript


class PblProjectConverter(SDKProjectCommand):
    """Converts a project to the current format: appinfo.json to package.json, and a wscript to a
    CMakeLists.txt."""
    command = 'convert-project'

    def __call__(self, args):
        try:
            super(PblProjectConverter, self).__call__(args)
            converted = False
            if not isinstance(self.project, NpmProject):
                self._convert_to_npm()
                print("Converted to package.json format.")
                converted = True
        except OutdatedProjectException:
            self._convert_project()
            super(PblProjectConverter, self).__call__(args)
            self._convert_to_npm()
            print("Project successfully converted!")
            converted = True
        converted = self._convert_to_cmake() or converted
        if not converted:
            print("No conversion required")

    def _convert_to_cmake(self):
        """Port the wscript to a CMakeLists.txt, when the SDK builds with CMake."""
        project_dir = self.project.project_dir
        wscript = os.path.join(project_dir, 'wscript')
        cmakelists = os.path.join(project_dir, 'CMakeLists.txt')
        if not os.path.exists(wscript) or os.path.exists(cmakelists):
            return False
        if not sdk_has_cmake():
            return False
        with open(os.path.join(project_dir, 'package.json')) as f:
            package = json.load(f)
        pebble = package.get('pebble', {})
        project_type = pebble.get('projectType', 'native')
        if project_type in ('rocky', 'moddable'):
            print("{} projects still build with waf: the wscript stays.".format(project_type.capitalize()))
            return False
        name = package.get('name', os.path.basename(os.path.abspath(project_dir))).split('/')[-1]
        try:
            result = convert_wscript(project_dir, name, project_type, pebble.get('enableMultiJS', False))
        except ConversionError as e:
            raise ToolError(str(e))
        with open(cmakelists, 'w') as f:
            f.write(result.cmake)
        os.rename(wscript, wscript + '.backup')
        print("Wrote CMakeLists.txt from the wscript, which is kept as wscript.backup.")
        print("New source files have to be added to CMakeLists.txt: it lists them, where the wscript "
              "took whatever was in src/.")
        if result.review:
            print("The wscript did more than build the sources. This was not carried over, and is "
                  "listed at the end of CMakeLists.txt:")
            for number, text in result.review:
                print("  line {}: {}".format(number, text))
        return True

    def _convert_to_npm(self):
        new_info = {
            'name': self.project.short_name,
            'author': self.project.company_name,
            'version': self.project.version + '.0',
            'private': True,
            'keywords': ['pebble-app'],
            'dependencies': {},
            'pebble': {
                'sdkVersion': self.project.sdk_version,
                'targetPlatforms': self.project.target_platforms,
                'enableMultiJS': self.project.enable_multi_js,
                'capabilities': self.project.capabilities,
                'projectType': self.project.project_type,
                'displayName': self.project.long_name,
                'uuid': str(self.project.uuid),
                'watchapp': {
                    'watchface': self.project.is_watchface,
                    'hiddenApp': self.project.is_hidden,
                    'onlyShownOnCommunication': self.project.is_shown_only_on_communication,
                },
                'resources': self.project.resources,
                'messageKeys': self.project.message_keys,
            }
        }
        if os.path.exists('package.json'):
            with open('package.json') as f:
                try:
                    new_info.update(json.load(f))
                except ValueError:
                    raise ToolError("An invalid package.json already exists; conversion aborted.")
            copy2('package.json', 'package.json~')
            print("A package.json already exists. It has been backed up to package.json~.")
        with open('package.json', 'w') as f:
            json.dump(new_info, f, indent=2, separators=(',', ': '))
        os.unlink('appinfo.json')

        self._ignore_npm()

    def _ignore_npm(self):
        if os.path.exists('.gitignore'):
            with open('.gitignore') as f:
                content = f.read()
                if 'node_modules' in content:
                    return

            with open('.gitignore', 'a') as f:
                f.write("\nnode_modules/\n")

    def _convert_project(self):
        project_root = os.getcwd()
        project_template_path = os.path.join(self.get_sdk_path(), 'pebble', 'common', 'templates')
        tool_project_template_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'sdk', 'templates')

        self._generate_appinfo_from_old_project(project_root)

        wscript_path = os.path.join(project_root, "wscript")

        print('Saving your old wscript in wscript.backup.')
        os.rename(wscript_path, wscript_path + '.backup')

        print('Generating new 3.x wscript')
        try:
            copy2(os.path.join(project_template_path, 'wscript'), wscript_path)
        except IOError:
            copy2(os.path.join(tool_project_template_path, 'wscript'), wscript_path)
        os.system('pebble clean')

    @classmethod
    def _generate_appinfo_from_old_project(cls, project_root):
        app_info_path = os.path.join(project_root, "appinfo.json")
        with open(app_info_path, "r") as f:
            app_info_json = json.load(f, object_pairs_hook=collections.OrderedDict)

        app_info_json["targetPlatforms"] = list(get_pebble_platforms())
        app_info_json["sdkVersion"] = "3"

        with open(app_info_path, "w") as f:
            json.dump(app_info_json, f, indent=2, separators=(',', ': '))


class ManifestV2Converter(SDKProjectCommand):
    """Converts a project to manifest version 2, declaring the permissions its code uses."""
    command = 'convert-manifest'

    def __call__(self, args):
        super(ManifestV2Converter, self).__call__(args)
        if not isinstance(self.project, NpmProject):
            raise ToolError("This project still uses appinfo.json: run `pebble convert-project` first.")
        with open('package.json') as f:
            package = json.load(f)
        changes = manifest_v2.upgrade(package['pebble'], manifest_v2.scan(os.getcwd(), package['pebble']))
        if not changes:
            print("Nothing to change: the project is manifest version 2 and declares everything its "
                  "JavaScript visibly uses.")
            return
        for change, where in changes:
            print("  {}{}".format(change, "  ({})".format(where) if where else ""))
        if args.dry_run:
            print("Dry run: package.json is unchanged.")
        else:
            with open('package.json', 'w') as f:
                json.dump(package, f, indent=2, separators=(',', ': '))
                f.write("\n")
            print("Updated package.json.")
        print("Only URLs written out in the JavaScript are found. Try the app on the emulator: "
              "`pebble logs` warns about any request the phone would refuse.")

    @classmethod
    def add_parser(cls, parser):
        parser = super(ManifestV2Converter, cls).add_parser(parser)
        parser.add_argument('--dry-run', action='store_true', help="Show the changes without making them.")
        return parser
