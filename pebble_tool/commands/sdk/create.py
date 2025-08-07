

import errno
import json
import os
import re

import shutil
from string import Template
from uuid import uuid4
from six import iteritems, string_types

from . import SDKCommand
from pebble_tool.sdk import SDK_VERSION, sdk_version
from pebble_tool.exceptions import ToolError
from pebble_tool.util.analytics import post_event
from pebble_tool.util.versions import version_to_key


def _mkdirs(path):
    """
    Like os.makedirs, but doesn't complain if they're already made.
    :param path: Directories to make
    :type path: str
    :return:
    """

    try:
        os.makedirs(os.path.dirname(path))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def extant_path(paths):
    """
    Returns the first path that exists, or None if no path does.
    :param paths: List of paths to test, in order.
    :type paths: Iterable[str]
    :return: The first path that exists, or None
    :rtype: str
    """
    for path in paths:
        if os.path.exists(path):
            return path

    return None


def _copy_from_template(template, template_root, path, options):
    """
    Given a description of a template and a pointer to the root that description uses,
    instantiates a template at the given path, using the given options
    :param template: The dictionary describing a template
    :type template: dict[str, dict[str, str | list[str] | dict[str, dict[str, str | list[str]]]]]
    :param template_root: The path to the root of the source files described by template
    :type template_root: str
    :param path: The path to the directory to create for the template.
    :type path: str
    :param options: The type of template to create
    :type options: list[str]
    """
    project_path = path
    project_name = os.path.split(project_path)[1]
    project_root = os.path.join(os.getcwd(), project_path)
    uuid = uuid4()

    try:
        os.mkdir(project_path)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise ToolError("A directory called '{}' already exists.".format(project_name))
        raise

    def substitute(template_content):
        return Template(template_content).substitute(uuid=str(uuid),
                                                     project_name=project_name,
                                                     display_name=project_name,
                                                     project_name_c=re.sub(r'[^a-zA-Z0-9_]+', '_', project_name),
                                                     sdk_version=SDK_VERSION)

    def copy_group(group, must_succeed=True):
        """
        Copies the files described by a subgroup of the main template definition.
        :param group: The group to copy
        :type group: dict[str, str | list[str] | dict[str, str | list[str]]]
        :param must_succeed: If nothing is copied, and this is True, throw a ToolError.
        :type must_succeed: bool
        """
        copied_files = 0

        for dest, origins in iteritems(group):
            target_path = os.path.join(substitute(project_root), dest)
            if origins is None:
                _mkdirs(target_path)
                continue

            if isinstance(origins, string_types):
                origins = [origins]

            origin_path = extant_path(os.path.join(template_root, x) for x in origins)
            if origin_path is not None:
                copied_files += 1
                _mkdirs(target_path)
                with open(origin_path) as f:
                    template_content = f.read()
                with open(substitute(target_path), 'w') as f:
                    f.write(substitute(template_content))

        if must_succeed and copied_files == 0:
            raise ToolError("Can't create that sort of project with the current SDK.")

    try:
        copy_group(template.get('default', {}), must_succeed=False)
        copy_group(template.get(options[0], {}).get('default', {}))
        for option in options[1:]:
            copy_group(template.get(options[0], {}).get(option, {}))
    except Exception:
        shutil.rmtree(project_root)
        raise


class NewProjectCommand(SDKCommand):
    """Creates a new pebble project with the given name in a new directory."""
    command = 'new-project'

    def __call__(self, args):
        super(NewProjectCommand, self).__call__(args)

        template_paths = [
            os.path.join(os.path.dirname(__file__), '..', '..', 'sdk', 'templates')
        ]

        # Ensure SDK is installed (get_sdk_path triggers installation if needed)
        self.get_sdk_path()
        
        sdk = self.sdk or sdk_version()
        sdk2 = (sdk == "2.9")

        if args.rocky:
            if sdk2:
                raise ToolError("--rocky is not compatible with SDK 2.9")
            if args.simple or args.worker:
                raise ToolError("--rocky is incompatible with --simple and --worker")
            options = ['rocky']
        else:
            options = ['app']
            if args.javascript:
                options.append('javascript')
            if args.simple:
                options.append('simple')
            if args.worker:
                options.append('worker')
            if args.ai:
                options.append('ai')

        # Hack for old SDKs that need an appinfo, because the declarative system can't
        # handle "this, but only if not that." For "tintin" SDKs and unparseble
        # versions, assume this hack is not needed.
        version_number = version_to_key(sdk)
        if version_number[:5] != (0, 0, 0, 0, 0) and \
           version_number < (3, 13, 0):
            options.append('appinfo')

        with open(extant_path(os.path.join(x, "templates.json") for x in template_paths)) as f:
            template_layout = json.load(f)

        _copy_from_template(template_layout, extant_path(template_paths), args.name, options)

        post_event("sdk_create_project", javascript=args.javascript or args.rocky, worker=args.worker, rocky=args.rocky)
        print("Created new project {}".format(args.name))

    @classmethod
    def add_parser(cls, parser):
        parser = super(NewProjectCommand, cls).add_parser(parser)
        parser.add_argument("name", help="Name of the project you want to create")
        type_group = parser.add_argument_group("project type")
        exclusive_type_group = type_group.add_mutually_exclusive_group()
        exclusive_type_group.add_argument("--rocky", action="store_true", help="Create a Rocky.js project.")
        exclusive_type_group.add_argument("--c", action="store_true", help="Create a C project.")
        c_group = parser.add_argument_group('C-specific arguments')
        c_group.add_argument("--simple", action="store_true", help="Create a minimal C file.")
        c_group.add_argument("--javascript", action="store_true", help="Generate a JavaScript file.")
        c_group.add_argument("--worker", action="store_true", help="Generate a background worker.")
        c_group.add_argument("--ai", action="store_true", help="Generate templates for Claude Code and Cursor")
        return parser


class NewPackageCommand(SDKCommand):
    """Creates a new pebble package (not app or watchface) with the given name in a new directory."""
    command = 'new-package'

    def __call__(self, args):
        super(NewPackageCommand, self).__call__(args)

        template_path = os.path.join(self.get_sdk_path(), 'pebble', 'common', 'templates')
        control_path = extant_path([
            os.path.join(template_path, 'templates.json'),
            os.path.join(os.path.dirname(__file__), '..', '..', 'sdk', 'templates', 'templates.json'),
        ])
        with open(control_path) as f:
            template_layout = json.load(f)

        options = ["lib"]
        if args.javascript:
            options.append("javascript")

        _copy_from_template(template_layout, template_path, args.name, options)

        post_event("sdk_create_package", javascript=args.javascript)
        print("Created new package {}".format(args.name))

    @classmethod
    def add_parser(cls, parser):
        parser = super(NewPackageCommand, cls).add_parser(parser)
        parser.add_argument("name", help="Name of the package you want to create")
        parser.add_argument("--javascript", action="store_true", help="Include a js directory.")
        return parser
