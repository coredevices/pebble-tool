# encoding: utf-8


import subprocess

from . import SDKProjectCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.sdk.project import NpmProject
import pebble_tool.util.npm as npm

__author__ = "katharine"


class PackageManager(SDKProjectCommand):
    """Manages npm packages."""
    command = 'package'
    has_subcommands = True

    def __call__(self, args):
        super(PackageManager, self).__call__(args)
        if not isinstance(self.project, NpmProject):
            raise ToolError("Package management is only available on projects using package.json. "
                            "Try pebble convert-project.")
        args.sub_func(args)

    @classmethod
    def add_parser(cls, parser):
        parser = super(PackageManager, cls).add_parser(parser)
        subparsers = parser.add_subparsers(title="subcommand")

        install_parser = subparsers.add_parser("install", help="Installs the given package.")
        install_parser.add_argument('package', nargs='?', help="npm package to install.")
        install_parser.set_defaults(sub_func=cls.do_install)

        uninstall_parser = subparsers.add_parser("uninstall", help="Uninstalls the given package.")
        uninstall_parser.add_argument('package', help="package to uninstall.")
        uninstall_parser.set_defaults(sub_func=cls.do_uninstall)

        login_parser = subparsers.add_parser("login", help="Log in to npm, or create an npm account.")
        login_parser.set_defaults(sub_func=cls.do_login)

        publish_parser = subparsers.add_parser("publish", help="Publish package to npm.")
        publish_parser.set_defaults(sub_func=cls.do_publish)

        return parser

    @classmethod
    def do_install(cls, args):
        try:
            npm.invoke_npm(["install", "--save", "--ignore-scripts", args.package])
            npm.invoke_npm(["dedupe"])
            npm.sanity_check()
        except subprocess.CalledProcessError:
            raise ToolError()

    @classmethod
    def do_uninstall(cls, args):
        npm.invoke_npm(["uninstall", "--save", "--ignore-scripts", args.package])

    @classmethod
    def do_login(cls, args):
        print("You can either log in to or create an npm account here.")
        try:
            npm.invoke_npm(["login"])
        except subprocess.CalledProcessError:
            pass

    @classmethod
    def do_publish(cls, args):
        try:
            npm.invoke_npm(["publish"])
        except subprocess.CalledProcessError:
            pass
