
__author__ = 'katharine'

from six import iteritems

import collections
import os
import signal
import subprocess

from libpebble2.exceptions import TimeoutError
from libpebble2.protocol.apps import AppRunState, AppRunStateRequest, AppRunStateStart

from pebble_tool.commands.base import PebbleCommand
from pebble_tool.commands.install import ToolAppInstaller
from pebble_tool.sdk import sdk_manager, add_tools_to_path
from pebble_tool.sdk.emulator import ManagedEmulatorTransport
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.exceptions import ToolError


class GdbCommand(PebbleCommand):
    """Connects a debugger to the current app. Only works in the emulator."""
    command = 'gdb'
    valid_connections = {'emulator'}

    @staticmethod
    def _find_app_section_offsets(app_elf_path):
        SectionRow = collections.namedtuple(
                'SectionRow', 'index name size vma lma file_offset align flags')

        info = subprocess.check_output(
                ['arm-none-eabi-objdump', '--headers', '--wide',
                 app_elf_path]).decode('utf-8').split('\n')[5:]
        sections = [SectionRow._make(section_string.split(None, 7))
                    for section_string in info if section_string]
        offsets = {section.name: int(section.vma, 16)
                   for section in sections if 'ALLOC' in section.flags}
        return offsets

    @staticmethod
    def _find_legacy_app_load_offset(fw_elf, kind):
        """Use readelf to find the app/worker load offset in a legacy 3.x
        firmware debugging symbols ELF where GDB is unable to read the symbols
        itself.
        """
        elf_sections = subprocess.check_output(["arm-none-eabi-readelf", "-W", "-s", fw_elf])

        # Figure out where we load the app into firmware memory
        for line in elf_sections.split(b'\n'):
            if b'__{}_flash_load_start__'.format(kind) in line:
                return int(line.split()[1], 16)
        else:
            raise ToolError("Couldn't find the {} address offset.".format(kind))

    def _get_symbol_command(self, elf, base_addr_expr):
        offsets = self._find_app_section_offsets(elf)

        command = ['add-symbol-file', '"{}"'.format(elf),
                   '{base_addr}+{text:#x}'.format(
                       base_addr=base_addr_expr, text=offsets['.text'])]
        command += ['-s {section} {base_addr}+{offset:#x}'
                    .format(section=section, offset=offset,
                            base_addr=base_addr_expr)
                    for section, offset in iteritems(offsets)
                    if section != '.text']
        return ' '.join(command)

    def _ensure_correct_app(self, try_install=True):
        project = PebbleProject()
        if project.project_type != 'native':
            raise ToolError("Only native apps can be debugged using gdb.")

        current_app_uuid = self.pebble.send_and_read(AppRunState(data=AppRunStateRequest()), AppRunState).data.uuid
        if current_app_uuid != project.uuid:
            print("Launching {}...".format(project.long_name))
            # Try launching the app we want. This just does nothing if the app doesn't exist.
            # Edge case: the app exists in blobdb but isn't installed. This shouldn't come up with the pebble tool.
            queue = self.pebble.get_endpoint_queue(AppRunState)
            try:
                self.pebble.send_packet(AppRunState(data=AppRunStateStart(uuid=project.uuid)))
                while True:
                    packet = queue.get(timeout=0.5)
                    if isinstance(packet.data, AppRunStateStart) and packet.data.uuid == project.uuid:
                        break
            except TimeoutError:
                if try_install:
                    print("App did not launch. Trying to install it...")
                    try:
                        ToolAppInstaller(self.pebble).install()
                    except IOError:
                        raise ToolError("The app to debug must be built and installed on the watch.")
                    self._ensure_correct_app(try_install=False)
                else:
                    raise ToolError("The app to debug must be running on the watch to start gdb.")
            finally:
                queue.close()

    def __call__(self, args):
        super(GdbCommand, self).__call__(args)
        # We poke around in the ManagedEmulatorTransport, so it's important that we actually have one.
        # Just asserting is okay because this should already be enforced by valid_connections.
        assert isinstance(self.pebble.transport, ManagedEmulatorTransport)
        self._ensure_correct_app()
        add_tools_to_path()

        platform = self.pebble.transport.platform
        sdk_version = self.pebble.transport.version
        gdb_port = self.pebble.transport.qemu_gdb_port
        if gdb_port is None:
            raise ToolError("The emulator does not have gdb support. Try killing and re-running it.")

        sdk_root = sdk_manager.path_for_sdk(sdk_version)
        self._fw_elf = os.path.join(sdk_root, 'pebble', platform, 'qemu', '{}_sdk_debug.elf'.format(platform))

        if not os.path.exists(self._fw_elf):
            raise ToolError("SDK {} does not support app debugging. You need at least SDK 3.10.".format(sdk_version))

        app_elf_path = os.path.join(os.getcwd(), 'build', platform, 'pebble-app.elf')
        if not os.path.exists(app_elf_path):
            raise ToolError("No app debugging information available. "
                            "You must be in a project directory and have built the app.")

        if self.pebble.firmware_version.major >= 4:
            # Type information for symbols is not currently written into the
            # debugging symbols generated by fw_elf_obfuscate.py. We must
            # explicitly tell GDB what type the symbols are so that their values
            # can be read.
            app_load_address = '*(void**)&g_app_load_address'
            worker_load_address = '*(void**)&g_worker_load_address'
        else:
            # The version of fw_elf_obfuscate.py which generated the debugging
            # symbol files for 3.x SDKs wrote out the symbol information for
            # variables in a way that caused them to be unavailable to GDB.
            # We have to use readelf to work around that and get the symbol
            # addresses.
            app_load_address = '(void*){:#x}'.format(
                    self._find_legacy_app_load_offset(self._fw_elf, 'app'))
            worker_load_address = '(void*){:#x}'.format(
                    self._find_legacy_app_load_offset(self._fw_elf, 'worker'))

        gdb_commands = [
            "set charset US-ASCII",  # Avoid a bug in the ancient version of libiconv apple ships.
            "target remote :{}".format(gdb_port),
            "set confirm off",
            self._get_symbol_command(app_elf_path, app_load_address)
        ]

        # Optionally add the worker symbols, if any exist.
        worker_elf_path = os.path.join(os.getcwd(), 'build', platform, 'pebble-worker.elf')
        if os.path.exists(worker_elf_path):
            gdb_commands.append(self._get_symbol_command(worker_elf_path,
                                                         worker_load_address))

        gdb_commands.extend([
            "set confirm on",
            "break app_crashed",  # app crashes (as of FW 3.10) go through this symbol for our convenience.
            'echo \nPress ctrl-D or type \'quit\' to exit.\n',
            'echo Try `pebble gdb --help` for a short cheat sheet.\n',
        ])

        gdb_args = ['arm-none-eabi-gdb', self._fw_elf, '-q'] + ['--ex={}'.format(x) for x in gdb_commands]

        # Ignore SIGINT, or we'll die every time the user tries to pause execution.
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        subprocess.call(gdb_args)

    epilog = """
gdb cheat sheet:
  ctrl-C                Pause app execution.
  ctrl-D, quit          Quit gdb.
  continue, c           Continue app execution.
  break, b              Set a breakpoint. This can be either a symbol or a
                        position:
                         - `b show_train_info` to break when entering a
                            function.
                         - `b stop_info.c:45` to break on line 45 of stop_info.c.
  step, s               Step forward one line.
  next, n               Step *over* the current line, avoiding stopping for
                        any functions it calls into.
  finish                Run forward until exiting the current stack frame.
  backtrace, bt         Print out the current call stack.
  p [expression]        Print the result of evaluating the given expression.
  set var x = foo       Set the value of the variable x to foo.
  info args             Show the values of arguments to the current function.
  info locals           Show local variables in the current frame.
  bt full               Show all local variables in all stack frames.
  info break            List break points (#1 is <app_crashed>, and is
                        inserted by the pebble tool).
  delete [n]            Delete breakpoint #n.
"""
