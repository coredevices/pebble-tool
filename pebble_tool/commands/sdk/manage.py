
__author__ = "katharine"

import collections
import os
import re
import requests
import shutil

from ..base import BaseCommand
from pebble_tool.exceptions import MissingSDK
from pebble_tool.sdk import get_sdk_persist_dir, sdk_manager, pebble_platforms
from pebble_tool.util.versions import version_to_key


class SDKManager(BaseCommand):
    """Manages available SDKs"""
    command = 'sdk'
    has_subcommands = True

    def __call__(self, args):
        super(SDKManager, self).__call__(args)
        args.sub_func(args)


    @classmethod
    def add_parser(cls, parser):
        parser = super(SDKManager, cls).add_parser(parser)
        subparsers = parser.add_subparsers(title="subcommand")

        list_parser = subparsers.add_parser("list", help="Lists available SDKs.")
        list_parser.set_defaults(sub_func=cls.do_list)

        install_parser = subparsers.add_parser("install", help="Installs the given SDK.")
        group = install_parser.add_mutually_exclusive_group(required=True)
        group.add_argument('version', nargs='?', help="Version to install, or 'latest' for the latest.")
        group.add_argument('--tintin', help="Path to a copy of the tintin source (internal only).")
        install_parser.set_defaults(sub_func=cls.do_install)

        activate_parser = subparsers.add_parser("activate", help="Makes the given, installed SDK active.")
        activate_parser.add_argument('version', help="Version to make active.")
        activate_parser.set_defaults(sub_func=cls.do_activate)

        uninstall_parser = subparsers.add_parser("uninstall", help="Uninstalls the given SDK.")
        uninstall_parser.add_argument('--keep-data', action="store_true", help="Skip deleting SDK-specific data "
                                                                               "such as persistent storage.")
        uninstall_parser.add_argument('version', help="Version to uninstall.")
        uninstall_parser.set_defaults(sub_func=cls.do_uninstall)

        set_channel_parser = subparsers.add_parser("set-channel", help="Sets the SDK channel.")
        set_channel_parser.add_argument('channel', help="The channel to use.")
        set_channel_parser.set_defaults(sub_func=cls.do_set_channel)

        include_path_parser = subparsers.add_parser("include-path", help="Prints out the SDK include path.")
        include_path_parser.add_argument("platform", help="The platform to give includes for.")
        include_path_parser.add_argument("--sdk", help="Optional SDK version override.")
        include_path_parser.set_defaults(sub_func=cls.do_include_path)
        return parser

    @classmethod
    def do_list(cls, args):
        current_sdk = sdk_manager.get_current_sdk()
        local_sdks = sdk_manager.list_local_sdks()
        local_sdk_versions = sdk_manager.list_local_sdk_versions()
        sorted_local_sdks = sorted(local_sdks, key=lambda x: version_to_key(x['version']), reverse=True)
        if len(local_sdks) > 0:
            print("Installed SDKs:")
            for sdk in sorted_local_sdks:
                line = sdk['version']
                if sdk['channel']:
                    line += " ({})".format(sdk['channel'])
                if sdk['version'] == current_sdk:
                    line += " (active)"
                print(line)
            print()
        else:
            print("No SDKs installed yet.")
        if sdk_manager.get_channel() != '':
            channel_text = ' ({} channel)'.format(sdk_manager.get_channel())
        else:
            channel_text = ''
        print("Available SDKs{}:".format(channel_text))
        try:
            for sdk in sdk_manager.list_remote_sdks():
                if sdk['version'] in local_sdk_versions:
                    continue
                line = sdk['version']
                if sdk['channel']:
                    line += " ({})".format(sdk['channel'])
                if sdk['version'] == current_sdk:
                    line += " (active)"
                print(line)
        except requests.RequestException:
            print("Could not fetch list of available SDKs.")


    @classmethod
    def do_install(cls, args):
        print("Installing SDK...")
        if args.tintin:
            sdk_manager.make_tintin_sdk(args.tintin)
        else:
            if re.match("https?://", args.version):
                sdk_manager.install_from_url(args.version)
            elif os.path.exists(args.version):
                sdk_manager.install_from_path(args.version)
            else:
                sdk_manager.install_remote_sdk(args.version)
        print("Installed.")

    @classmethod
    def do_uninstall(cls, args):
        print("Uninstalling SDK {}...".format(args.version))
        sdk_manager.uninstall_sdk(args.version)
        if not args.keep_data:
            for platform in pebble_platforms:
                shutil.rmtree(os.path.join(get_sdk_persist_dir(platform, args.version)))
        print("Done.")

    @classmethod
    def do_activate(cls, args):
        sdk_manager.set_current_sdk(args.version)
        print("Set active SDK to {}.".format(sdk_manager.get_current_sdk()))

    @classmethod
    def do_set_channel(cls, args):
        sdk_manager.set_channel(args.channel)
        print("Set channel to {}.".format(sdk_manager.get_channel()))

    @classmethod
    def do_include_path(cls, args):
        sdk = args.sdk or sdk_manager.get_current_sdk()
        path = sdk_manager.path_for_sdk(sdk)
        path = os.path.join(path, "pebble", args.platform, "include")
        if not os.path.exists(path):
            raise MissingSDK("No platform '{}' available for SDK {}".format(args.platform, sdk))
        print(path)
