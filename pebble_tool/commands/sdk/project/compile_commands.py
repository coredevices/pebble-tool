
__author__ = 'jplexer'

import json
import os

from pebble_tool.commands.sdk.project import SDKProjectCommand
from pebble_tool.exceptions import ToolError


class CompileCommandsCommand(SDKProjectCommand):
    """Generate a compile_commands.json so editors (clangd, etc.) can resolve the Pebble SDK."""
    command = "compile-commands"

    def __call__(self, args):
        super(CompileCommandsCommand, self).__call__(args)

        platform = self._choose_platform(args.platform)
        project_dir = os.path.abspath(self.project.project_dir)

        sources = self._find_sources()
        if not sources:
            print("No C source files found under src/ or worker_src/; nothing to do.")
            return

        env = self._waf_env(platform)
        define_flag = env.get('DEFINES_ST', '-D%s')
        include_flag = env.get('CPPPATH_ST', '-I%s')

        base_args = [self._find_compiler()]
        base_args += list(env.get('CFLAGS', []))
        base_args += [define_flag % d for d in env.get('DEFINES', [])]
        base_args += [include_flag % p for p in self._include_dirs(project_dir, platform, env)]

        db = []
        for src in sources:
            abs_src = os.path.join(project_dir, src)
            db.append({
                "directory": project_dir,
                "file": abs_src,
                "arguments": base_args + ["-c", abs_src,
                                          "-o", os.path.join('build', platform, '{}.o'.format(src))],
            })

        out_path = os.path.join(project_dir, 'compile_commands.json')
        with open(out_path, 'w') as f:
            json.dump(db, f, indent=2)
            f.write('\n')

        print("Wrote {} ({} file{}, platform '{}').".format(
            out_path, len(db), '' if len(db) == 1 else 's', platform))

    def _waf_env(self, platform):
        """Read the build flags the SDK's own waf toolchain resolved for this platform.

        `waf configure` writes the fully-resolved compile environment to
        build/c4che/<platform>_cache.py, so we read CFLAGS/DEFINES straight from there
        rather than reconstructing them by hand. That keeps us in lockstep with whatever
        the active SDK actually compiles with (e.g. the version-conditional
        -D_TIME_H_/-Dtime_t=long flags newer SDKs add, the RELEASE define, the warning
        set). We run configure if that cache isn't present yet.
        """
        cache = os.path.join(self.project.project_dir, 'build', 'c4che',
                             '{}_cache.py'.format(platform))
        if not os.path.exists(cache):
            print("Configuring to read the SDK's build flags...")
            self._waf("configure")
        if not os.path.exists(cache):
            raise ToolError("Couldn't find build flags for platform '{}' (expected {}). "
                            "Try 'pebble build' first.".format(platform, cache))
        # The cache is a waf-generated python file of plain `NAME = <literal>` assignments.
        env = {}
        with open(cache) as f:
            exec(compile(f.read(), cache, 'exec'), {}, env)
        return env

    def _choose_platform(self, requested):
        platforms = self.project.target_platforms
        if requested is not None:
            if requested not in platforms:
                raise ToolError("This project doesn't target '{}'. Available platforms: {}.".format(
                    requested, ", ".join(platforms)))
            return requested
        # emery exposes the widest API surface (colour, rectangular, plus PBL_TOUCH,
        # PBL_SPEAKER, PBL_RGB_BACKLIGHT, ...), so it's the most useful default for
        # completion; otherwise fall back to the first target platform.
        return 'emery' if 'emery' in platforms else platforms[0]

    def _find_compiler(self):
        # The arm-none-eabi toolchain ships alongside sdk-core in the SDK bundle (the same
        # sibling layout `_waf` uses to find the venv). An absolute path lets clangd query
        # the cross-compiler for its builtin headers (stdint.h, stdbool.h, ...); the bare
        # 'arm-none-eabi-gcc' from the cache's CC wouldn't pin those down for an editor.
        candidate = os.path.abspath(os.path.join(
            self.get_sdk_path(), '..', 'toolchain', 'arm-none-eabi', 'bin', 'arm-none-eabi-gcc'))
        if os.path.exists(candidate):
            return candidate
        # add_arm_tools_to_path() (run in SDKCommand.__call__) has already put the
        # toolchain on PATH, so the bare name still resolves for tools that exec it.
        return 'arm-none-eabi-gcc'

    def _include_dirs(self, project_dir, platform, env):
        # The SDK's waf adds these per-compile in setup_pebble_c but never persists them
        # to the env cache, so we reproduce them here.
        dirs = [os.path.join(self.get_sdk_path(), 'pebble', platform, 'include')]
        # Project include dirs: '.', 'src', 'include' (per setup_pebble_c); modern
        # projects also keep their C under src/c.
        for rel in ('.', 'src', os.path.join('src', 'c'), 'include'):
            path = os.path.normpath(os.path.join(project_dir, rel))
            if os.path.isdir(path):
                dirs.append(path)
        # waf resolves its relative include dirs against the build tree too, which is where
        # generated headers live: message_keys.auto.h in build/include, resource_ids.auto.h
        # in build/<platform>/src. These only exist after a build, but harmless if absent.
        build = os.path.join(project_dir, 'build')
        dirs.append(os.path.join(build, 'include'))
        for inc in env.get('INCLUDES', []):
            dirs.append(os.path.join(build, inc))
            dirs.append(os.path.join(build, inc, 'src'))
        return dirs

    def _find_sources(self):
        sources = []
        # Modern projects keep C under src/c/ (workers under worker_src/c/); older ones put
        # it directly in src/. Walk both roots so either layout works.
        for root_dir in ('src', 'worker_src'):
            abs_root = os.path.join(self.project.project_dir, root_dir)
            if not os.path.isdir(abs_root):
                continue
            for dirpath, dirnames, filenames in os.walk(abs_root):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for name in filenames:
                    if name.endswith('.c'):
                        sources.append(os.path.relpath(os.path.join(dirpath, name),
                                                       self.project.project_dir))
        return sorted(sources)

    @classmethod
    def add_parser(cls, parser):
        parser = super(CompileCommandsCommand, cls).add_parser(parser)
        parser.add_argument('--platform', help="Platform whose flags and includes to use "
                                               "(default: emery if targeted, else the first "
                                               "target platform).")
        return parser
