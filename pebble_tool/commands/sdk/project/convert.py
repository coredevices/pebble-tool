

import collections
import hashlib
import json
import os

from shutil import copy2

from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.exceptions import OutdatedProjectException, ToolError
from pebble_tool.sdk.project import NpmProject
from pebble_tool.sdk import pebble_platforms


class PblProjectConverter(SDKProjectCommand):
    """Converts an appinfo project from SDK 2 or SDK 3 to a modern package.json project."""
    command = 'convert-project'

    def __call__(self, args):
        try:
            super(PblProjectConverter, self).__call__(args)
            if not isinstance(self.project, NpmProject):
                self._convert_to_npm()
                print("Converted to package.json format.")
            else:
                print("No conversion required")
        except OutdatedProjectException:
            self._convert_project()
            super(PblProjectConverter, self).__call__(args)
            self._convert_to_npm()
            print("Project successfully converted!")

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

        app_info_json["targetPlatforms"] = pebble_platforms
        app_info_json["sdkVersion"] = "3"

        with open(app_info_path, "w") as f:
            json.dump(app_info_json, f, indent=2, separators=(',', ': '))
