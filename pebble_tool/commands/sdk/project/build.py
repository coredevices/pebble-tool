
__author__ = 'katharine'

import argparse
import os
import shutil
import subprocess
import sys
import time

from pebble_tool.exceptions import BuildError, ToolError
from pebble_tool.util.analytics import post_event
import pebble_tool.util.npm as npm
from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.sdk import has_rocky_tools, sdk_version, has_moddable_tools


class BuildCommand(SDKProjectCommand):
    """Builds the current project."""
    command = "build"

    def __call__(self, args):
        super(BuildCommand, self).__call__(args)
        start_time = time.time()
        # Check if Rocky.js project has required SDK tools
        if self.project.project_type == 'rocky':
            if not has_rocky_tools():
                raise ToolError(
                    "This is a Rocky.js project, but the currently active SDK ({}) "
                    "does not have Rocky.js tools installed.\n\n"
                    "Rocky.js requires an SDK version that includes js_tooling.wasm.\n"
                    "Try installing a different SDK version with 'pebble sdk install <version>'.".format(
                        sdk_version() or "unknown"
                    )
                )
        if len(self.project.dependencies) > 0:
            post_event('app_build_with_npm_deps')
            try:
                npm.invoke_npm(["install"])
                npm.invoke_npm(["dedupe"])
            except subprocess.CalledProcessError:
                post_event("app_build_failed_npm")
                raise BuildError("npm failed.")
        try:
            waf = list(args.args)
            try:
                waf.remove('--')
            except ValueError:
                pass
            extra_env = {}
            if args.debug:
                extra_env = {'CFLAGS': os.environ.get('CFLAGS', '') + ' -O0'}
            if self.project.project_type == 'moddable':
                if not has_moddable_tools():
                    raise ToolError("This is a Moddable project, but the currently active SDK does not have Moddable tools. "
                                    "Please install an SDK with Moddable support to build this project.")
                self.run_moddable_prebuild()
            self._waf("configure", extra_env=extra_env, args=waf)
            self._waf("build", args=waf)
        except subprocess.CalledProcessError:
            duration = time.time() - start_time
            post_event("app_build_failed", build_time=duration)
            raise BuildError("Build failed.")
        else:
            duration = time.time() - start_time
            has_js = os.path.exists(os.path.join('src', 'js'))
            post_event("app_build_succeeded", has_js=has_js, line_counts=self._get_line_counts(), build_time=duration)

    def run_moddable_prebuild(self):
        print("Running Moddable prebuild.")
        try:
            os.makedirs('build', exist_ok=True)

            for platform in self.project.target_platforms:
                cmd = [
                    'mcrun',
                    '-m', './src/embeddedjs/manifest.json',
                    '-f', 'x',
                    '-p', f'pebble/{platform}',
                    '-t', 'build',
                    '-o', './build',
                    '-s', 'tech.moddable.pebble'
                ]
                print(f"Running {' '.join(cmd)}")
                subprocess.run(cmd, check=True)

            print("Moddable prebuild completed.")
        except subprocess.CalledProcessError as e:
            print(f"Moddable prebuild failed: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Error in Moddable prebuild: {e}")
            sys.exit(1)

    @classmethod
    def _get_line_counts(cls):
        c_line_count = 0
        js_line_count = 0
        if os.path.exists('src'):
            c_line_count += cls._count_lines('src', ['.h', '.c'])
            js_line_count += cls._count_lines('src', ['.js'])

        return {'c_line_count': c_line_count, 'js_line_count': js_line_count}

    @classmethod
    def _count_lines(cls, path, extensions):
        src_lines = 0
        files = os.listdir(path)
        for name in files:
            if name.startswith('.'):
                continue
            if os.path.isdir(os.path.join(path, name)):
                if not os.path.islink(os.path.join(path, name)):
                    src_lines += cls._count_lines(os.path.join(path, name), extensions)
                continue
            ext = os.path.splitext(name)[1]
            if ext in extensions:
                src_lines += sum(1 for line in open(os.path.join(path, name)))
        return src_lines

    @classmethod
    def add_parser(cls, parser):
        parser = super(BuildCommand, cls).add_parser(parser)
        parser.add_argument('--debug', action='store_true', help="Build without optimisations for easier debugging. "
                                                                 "This may cause apps to run slower or not fit at all.")
        parser.add_argument('args', nargs=argparse.REMAINDER, help="Extra arguments to pass to waf.")
        return parser


class CleanCommand(SDKProjectCommand):
    command = "clean"

    def __call__(self, args):
        super(CleanCommand, self).__call__(args)
        try:
            self._waf("distclean")
        except subprocess.CalledProcessError:
            print("Clean failed.")
