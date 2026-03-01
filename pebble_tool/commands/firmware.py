from __future__ import absolute_import, print_function

import datetime
import json
import os
import zipfile

from progressbar import ProgressBar, Bar, FileTransferSpeed, Timer, Percentage, FormatLabel

from libpebble2.protocol.system import SystemMessage, Reset
from libpebble2.protocol.transfers import GetBytesInfoResponse
from libpebble2.services.putbytes import PutBytes, PutBytesType
from libpebble2.services.getbytes import GetBytesService
from libpebble2.exceptions import GetBytesError

from .base import PebbleCommand
from ..exceptions import ToolError


FLASH_LOG_REGIONS = {
    # Legacy Platforms
    'aplite': (0x3E0000, 0x20000),
    'tintin': (0x3E0000, 0x20000),

    # Snowy / Spalding (Bottom Boot)
    'basalt': (0x000000, 0x20000),
    'snowy':  (0x000000, 0x20000),
    'chalk':  (0x000000, 0x20000),
    'spalding': (0x000000, 0x20000),

    # Silk / Diorite
    'diorite': (0x280000, 0x20000),
    'silk':    (0x280000, 0x20000),

    # Asterix
    'asterix': (0x1FD0000, 0x20000),

    # Obelix / Getafix
    'obelix': (0x1FCF000, 0x20000),
    'getafix': (0x1FCF000, 0x20000),
}


class FirmwareManager(PebbleCommand):
    """Firmware management commands."""
    command = 'fw'
    has_subcommands = True

    def __call__(self, args):
        super(FirmwareManager, self).__call__(args)
        args.sub_func(self, args)

    @classmethod
    def add_parser(cls, parser):
        parser = super(FirmwareManager, cls).add_parser(parser)
        subparsers = parser.add_subparsers(title="subcommand")

        install_parser = subparsers.add_parser("install", help="Install a .pbz firmware bundle onto the watch.")
        install_parser.add_argument('filename', type=str, help="Path to .pbz firmware bundle")
        install_parser.add_argument('--slot', type=int, choices=[0, 1], default=0,
                                    help="Firmware slot to install (for multi-slot PBZ bundles, default: 0)")
        install_parser.set_defaults(sub_func=cls.do_install)

        lang_parser = subparsers.add_parser("install-lang", help="Install a language pack onto the watch.")
        lang_parser.add_argument('lang_file', type=str, help="Path to language pack file")
        lang_parser.set_defaults(sub_func=cls.do_install_lang)

        coredump_parser = subparsers.add_parser("coredump", help="Extract a coredump from the watch.")
        coredump_parser.add_argument('filename', nargs='?', type=str, help="Output filename (auto-generated if omitted)")
        coredump_parser.add_argument('--fresh', action='store_true', help="Require a fresh (unread) coredump")
        coredump_parser.set_defaults(sub_func=cls.do_coredump)

        flash_logs_parser = subparsers.add_parser("flash-logs", help="Dump PBL_LOG flash logs from the watch.")
        flash_logs_parser.add_argument('--board', required=True, type=str.lower,
                                       help="Board name (e.g., aplite, basalt, asterix)")
        flash_logs_parser.set_defaults(sub_func=cls.do_flash_logs)

        enter_prf_parser = subparsers.add_parser("enter-prf", help="Reboot the watch into PRF (recovery firmware).")
        enter_prf_parser.set_defaults(sub_func=cls.do_enter_prf)

        return parser

    @staticmethod
    def _load_pbz_manifest(filename, slot):
        """Load manifest and file paths from a PBZ, handling both legacy and multi-slot formats."""
        pbz = zipfile.ZipFile(os.path.abspath(filename))
        names = set(pbz.namelist())

        # Legacy format: manifest.json at root
        if 'manifest.json' in names:
            manifest = json.loads(pbz.read('manifest.json').decode('utf-8'))
            prefix = ''
        # Multi-slot format: slot0/manifest.json, slot1/manifest.json
        elif 'slot{}/manifest.json'.format(slot) in names:
            manifest = json.loads(pbz.read('slot{}/manifest.json'.format(slot)).decode('utf-8'))
            prefix = 'slot{}/'.format(slot)
        else:
            raise ToolError("Could not find manifest.json in PBZ. Not a valid firmware bundle.")

        if 'firmware' not in manifest:
            raise ToolError("PBZ manifest does not contain firmware info.")

        return pbz, manifest, prefix

    def do_install(self, args):
        label = FormatLabel('{variables.task}', new_style=True)
        progress_bar = ProgressBar(widgets=[label, Percentage(), Bar(marker='=', left='[', right=']'), ' ',
                                            FileTransferSpeed(), ' ', Timer(format='%s')])
        started = False

        def handle_progress(this_interval, progress, total):
            nonlocal started
            if not started:
                progress_bar.max_value = total
                progress_bar.start()
                started = True
            progress_bar.update(progress)

        pbz, manifest, prefix = self._load_pbz_manifest(args.filename, args.slot)
        fw_info = manifest['firmware']
        res_info = manifest.get('resources')

        fw_name = prefix + fw_info['name']
        print("Installing {} (slot {}, {})".format(fw_info.get('versionTag', '?'), fw_info.get('slot', 'n/a'),
                                                    fw_info.get('hwrev', '?')))

        self.pebble.send_and_read(SystemMessage(message_type=SystemMessage.Type.FirmwareUpdateStart), SystemMessage)

        try:
            progress_bar.variables['task'] = "{:<24}".format(fw_info['name'])
            firmware_bytes = pbz.read(fw_name)
            pb = PutBytes(self.pebble, PutBytesType.Firmware, firmware_bytes, bank=args.slot)
            pb.register_handler("progress", handle_progress)
            pb.send()
            progress_bar.finish()
            started = False

            if res_info:
                res_name = prefix + res_info['name']
                progress_bar.variables['task'] = "{:<24}".format(res_info['name'])
                resource_bytes = pbz.read(res_name)
                pb = PutBytes(self.pebble, PutBytesType.SystemResources, resource_bytes, bank=args.slot)
                pb.register_handler("progress", handle_progress)
                pb.send()
                progress_bar.finish()
        except Exception:
            self.pebble.send_packet(SystemMessage(message_type=SystemMessage.Type.FirmwareUpdateFailed))
            raise

        self.pebble.send_packet(SystemMessage(message_type=SystemMessage.Type.FirmwareUpdateComplete))

    def do_install_lang(self, args):
        progress_bar = ProgressBar(widgets=[Percentage(), Bar(marker='=', left='[', right=']'),
                                            ' ', FileTransferSpeed(), ' ', Timer(format='%s')])

        with open(args.lang_file, 'rb') as f:
            lang_pack = f.read()

        progress_bar.max_value = len(lang_pack)
        progress_bar.start()

        def handle_progress(sent, total_sent, total_length):
            progress_bar.update(total_sent)

        pb = PutBytes(self.pebble, PutBytesType.File, lang_pack, bank=0, filename="lang")
        pb.register_handler("progress", handle_progress)
        pb.send()

        progress_bar.finish()

    def do_coredump(self, args):
        progress_bar = ProgressBar(widgets=[Percentage(), Bar(marker='=', left='[', right=']'), ' ',
                                            FileTransferSpeed(), ' ', Timer(format='%s')])
        started = False

        def handle_progress(progress, total):
            nonlocal started
            if not started:
                progress_bar.max_value = total
                started = True
            progress_bar.update(progress)

        get_bytes = GetBytesService(self.pebble)
        get_bytes.register_handler("progress", handle_progress)

        progress_bar.start()
        try:
            core_data = get_bytes.get_coredump(args.fresh)
        except GetBytesError as ex:
            if ex.code == GetBytesInfoResponse.ErrorCode.DoesNotExist:
                raise ToolError('No coredump on device')
            else:
                raise

        progress_bar.finish()

        filename = args.filename or datetime.datetime.now().strftime("pebble_coredump_%Y-%m-%d_%H-%M-%S.core")
        with open(filename, "wb") as core_file:
            core_file.write(core_data)
        print("Saved coredump to {}".format(filename))

    def do_enter_prf(self, args):
        # libpebble2 has Reset.Command.PRF = 0x03, but the actual firmware
        # expects 0xFF for ResetCmdIntoRecovery (see fw_reset.c)
        PRF_COMMAND = 0xFF
        print("Rebooting watch into PRF...")
        self.pebble.send_packet(Reset(command=PRF_COMMAND))

    def do_flash_logs(self, args):
        board = args.board
        region = FLASH_LOG_REGIONS.get(board)
        if not region:
            raise ToolError("Unknown board '{}'. Supported boards: {}".format(
                board, ", ".join(sorted(FLASH_LOG_REGIONS.keys()))))

        flash_log_start, flash_log_size = region

        print("Board: {}".format(board))
        print("Reading flash log region: 0x{:X} - 0x{:X} ({} KB)".format(
            flash_log_start, flash_log_start + flash_log_size, flash_log_size // 1024))

        get_bytes = GetBytesService(self.pebble)

        try:
            flash_data = get_bytes.get_flash_region(flash_log_start, flash_log_size)
            print("Read {} bytes from flash".format(len(flash_data)))

            filename = datetime.datetime.now().strftime("flash_logs_{}_%Y-%m-%d_%H-%M-%S.bin".format(board))
            filepath = os.path.abspath(filename)
            with open(filename, "wb") as log_file:
                log_file.write(flash_data)
            print("Saved flash logs to {}".format(filepath))

        except GetBytesError as ex:
            if ex.code in (GetBytesInfoResponse.ErrorCode.DoesNotExist,
                           GetBytesInfoResponse.ErrorCode.MalformedRequest):
                raise ToolError('Could not read flash region. '
                                'Flash log reading requires non-release firmware (disabled in release builds).')
            else:
                raise
