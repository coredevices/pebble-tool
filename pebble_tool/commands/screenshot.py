
__author__ = 'katharine'

import argparse
import datetime
import errno
import itertools
import json
import os
import png
import os.path
import re
from progressbar import ProgressBar, Bar, ReverseBar, FileTransferSpeed, Timer, Percentage
import signal
import subprocess
import sys
import time
import zipfile

from libpebble2.communication import PebbleConnection
from libpebble2.exceptions import ScreenshotError
from libpebble2.services.screenshot import Screenshot
from libpebble2.protocol.system import TimeMessage, SetUTC

from .base import PebbleCommand, BaseCommand
from .install import ToolAppInstaller
from pebble_tool.commands.sdk.project.build import BuildCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.sdk import sdk_manager
import pebble_tool.sdk.emulator as emulator
from pebble_tool.sdk.emulator import ManagedEmulatorTransport


def _positive_int(value):
    """Validate that the value is a positive integer."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")

    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"'{value}' must be a positive integer (>= 1)")

    return ivalue


class ScreenshotCommand(PebbleCommand):
    """Takes a screenshot from the watch."""
    command = 'screenshot'

    def __init__(self):
        self.progress_bar = ProgressBar(widgets=[Percentage(), Bar(marker='=', left='[', right=']'), ' ',
                                                 FileTransferSpeed(), ' ', Timer(format='%s')])
        self.started = False

    def __call__(self, args):
        if args.all_platforms:
            BaseCommand.__call__(self, args)
            self._capture_all_platforms(args)
            return

        super(ScreenshotCommand, self).__call__(args)
        image = self._grab_processed_image(args)

        filename = self._generate_filename() if args.filename is None else args.filename
        png.from_array(image, mode='RGBA;8').save(filename)
        print("Saved screenshot to {}".format(filename))
        if not args.no_open:
            self._open(os.path.abspath(filename))

    def _grab_processed_image(self, args):
        screenshot = Screenshot(self.pebble)
        screenshot.register_handler("progress", self._handle_progress)
        self.started = False
        self.progress_bar.start()
        try:
            image = screenshot.grab_image()
        except ScreenshotError as e:
            if self.pebble.firmware_version.major == 3 and self.pebble.firmware_version.minor == 2:
                # PBL-21154: Screenshots failing with error code 2 (out of memory)
                raise ToolError(str(e) + " (screenshots are known to be broken using firmware 3.2; try the emulator.)")
            else:
                raise ToolError(str(e) + " (try rebooting the watch)")
        if not args.no_correction:
            image = self._correct_colours(image)
        image = self._roundify(image)
        if args.scale > 1:
            image = self._scale_image(image, args.scale)
        self.progress_bar.finish()
        return image

    def _capture_all_platforms(self, args):
        project, pbw_path = self._ensure_project_pbw(args)
        platforms, app_version = self._extract_pbw_metadata(pbw_path, project)

        screenshots_dir = os.path.join(project.project_dir, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        print("Using PBW: {}".format(pbw_path))
        print("Platforms: {}".format(", ".join(platforms)))
        print("App version: {}".format(app_version))

        for platform_name in platforms:
            print("\n=== Capturing {} ===".format(platform_name))
            pebble = None
            previous_pebble = getattr(self, "pebble", None)
            try:
                pebble = self._connect_emulator(platform_name, args.sdk)
                self.pebble = pebble
                ToolAppInstaller(pebble, pbw_path).install()
                self._set_time_1010(pebble)
                image = self._grab_processed_image(args)
                filename = self._platform_filename(screenshots_dir, platform_name, app_version)
                png.from_array(image, mode='RGBA;8').save(filename)
                print("Saved screenshot to {}".format(filename))
            finally:
                self.pebble = previous_pebble
                self._shutdown_platform_emulator(platform_name, args.sdk)

    def _connect_emulator(self, platform_name, sdk_version):
        transport = ManagedEmulatorTransport(platform_name, sdk_version, False)
        pebble = PebbleConnection(transport, **self._get_debug_args())
        pebble.connect()
        pebble.run_async()
        return pebble

    def _ensure_project_pbw(self, args):
        try:
            project = PebbleProject()
        except Exception as e:
            raise ToolError("This mode must be run from a Pebble project directory: {}".format(e))

        pbw_path = os.path.join(project.project_dir, "build", "{}.pbw".format(os.path.basename(project.project_dir)))
        if not os.path.exists(pbw_path):
            print("PBW not found at {}. Building project first...".format(pbw_path))
            build_args = argparse.Namespace(v=args.v, sdk=args.sdk, debug=False, args=[])
            BuildCommand()(build_args)
        if not os.path.exists(pbw_path):
            raise ToolError("Expected PBW at {} after build, but it was not created.".format(pbw_path))
        return project, pbw_path

    @classmethod
    def _extract_pbw_metadata(cls, pbw_path, project):
        platforms = list(project.target_platforms)
        version = project.version
        try:
            with zipfile.ZipFile(pbw_path) as zf:
                for metadata_name in ("appinfo.json", "manifest.json"):
                    if metadata_name in zf.namelist():
                        with zf.open(metadata_name) as f:
                            metadata = json.loads(f.read().decode("utf-8"))
                        platforms = metadata.get("targetPlatforms", platforms)
                        version = metadata.get("versionLabel", metadata.get("version", version))
                        break
        except (IOError, ValueError, zipfile.BadZipFile):
            pass
        return platforms, str(version)

    @classmethod
    def _sanitize_for_filename(cls, value):
        return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "unknown"

    @classmethod
    def _platform_filename(cls, screenshots_dir, platform_name, app_version):
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_version = cls._sanitize_for_filename(app_version)
        safe_platform = cls._sanitize_for_filename(platform_name)
        return os.path.join(screenshots_dir, "{}_{}_{}.png".format(safe_platform, safe_version, timestamp))

    @classmethod
    def _set_time_1010(cls, pebble):
        now = datetime.datetime.now()
        target = now.replace(hour=10, minute=10, second=0, microsecond=0)
        ts = int(target.timestamp())
        tz_offset = -time.altzone if time.localtime(ts).tm_isdst and time.daylight else -time.timezone
        tz_offset_minutes = tz_offset // 60
        tz_name = "UTC%+d" % (tz_offset_minutes // 60)
        # Send twice with a short settle delay to make sure the rendered face reflects the new time.
        pebble.send_packet(TimeMessage(message=SetUTC(unix_time=ts, utc_offset=tz_offset_minutes, tz_name=tz_name)))
        time.sleep(0.35)
        pebble.send_packet(TimeMessage(message=SetUTC(unix_time=ts, utc_offset=tz_offset_minutes, tz_name=tz_name)))
        time.sleep(0.65)

    @classmethod
    def _shutdown_platform_emulator(cls, platform_name, sdk_version):
        target_sdk = sdk_version or sdk_manager.get_current_sdk()
        info = emulator.get_emulator_info(platform_name, target_sdk)
        if info is None:
            return
        for key in ("qemu", "pypkjs", "websockify"):
            pid = info.get(key, {}).get("pid")
            if not pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                if e.errno != errno.ESRCH:
                    raise
        emulator.update_emulator_info(platform_name, info["version"], None)

    def _handle_progress(self, progress, total):
        if not self.started:
            self.progress_bar.maxval = total
            self.started = True
        self.progress_bar.update(progress)

    def _correct_colours(self, image):
        mapping = {
            (0, 0, 0): (0, 0, 0),
            (0, 0, 85): (0, 30, 65),
            (0, 0, 170): (0, 67, 135),
            (0, 0, 255): (0, 104, 202),
            (0, 85, 0): (43, 74, 44),
            (0, 85, 85): (39, 81, 79),
            (0, 85, 170): (22, 99, 141),
            (0, 85, 255): (0, 125, 206),
            (0, 170, 0): (94, 152, 96),
            (0, 170, 85): (92, 155, 114),
            (0, 170, 170): (87, 165, 162),
            (0, 170, 255): (76, 180, 219),
            (0, 255, 0): (142, 227, 145),
            (0, 255, 85): (142, 230, 158),
            (0, 255, 170): (138, 235, 192),
            (0, 255, 255): (132, 245, 241),
            (85, 0, 0): (74, 22, 27),
            (85, 0, 85): (72, 39, 72),
            (85, 0, 170): (64, 72, 138),
            (85, 0, 255): (47, 107, 204),
            (85, 85, 0): (86, 78, 54),
            (85, 85, 85): (84, 84, 84),
            (85, 85, 170): (79, 103, 144),
            (85, 85, 255): (65, 128, 208),
            (85, 170, 0): (117, 154, 100),
            (85, 170, 85): (117, 157, 118),
            (85, 170, 170): (113, 166, 164),
            (85, 170, 255): (105, 181, 221),
            (85, 255, 0): (158, 229, 148),
            (85, 255, 85): (157, 231, 160),
            (85, 255, 170): (155, 236, 194),
            (85, 255, 255): (149, 246, 242),
            (170, 0, 0): (153, 53, 63),
            (170, 0, 85): (152, 62, 90),
            (170, 0, 170): (149, 86, 148),
            (170, 0, 255): (143, 116, 210),
            (170, 85, 0): (157, 91, 77),
            (170, 85, 85): (157, 96, 100),
            (170, 85, 170): (154, 112, 153),
            (170, 85, 255): (149, 135, 213),
            (170, 170, 0): (175, 160, 114),
            (170, 170, 85): (174, 163, 130),
            (170, 170, 170): (171, 171, 171),
            (170, 170, 255): (167, 186, 226),
            (170, 255, 0): (201, 232, 157),
            (170, 255, 85): (201, 234, 167),
            (170, 255, 170): (199, 240, 200),
            (170, 255, 255): (195, 249, 247),
            (255, 0, 0): (227, 84, 98),
            (255, 0, 85): (226, 88, 116),
            (255, 0, 170): (225, 106, 163),
            (255, 0, 255): (222, 131, 220),
            (255, 85, 0): (230, 110, 107),
            (255, 85, 85): (230, 114, 124),
            (255, 85, 170): (227, 127, 167),
            (255, 85, 255): (225, 148, 223),
            (255, 170, 0): (241, 170, 134),
            (255, 170, 85): (241, 173, 147),
            (255, 170, 170): (239, 181, 184),
            (255, 170, 255): (236, 195, 235),
            (255, 255, 0): (255, 238, 171),
            (255, 255, 85): (255, 241, 181),
            (255, 255, 170): (255, 246, 211),
            (255, 255, 255): (255, 255, 255),
        }
        return [list(itertools.chain(*[mapping[y[x], y[x+1], y[x+2]] for x in range(0, len(y), 3)])) for y in image]

    def _roundify(self, image):
        # Convert our RGB image to fully-opaque RGBA.
        rgba = [list(itertools.chain(*[(y[x], y[x+1], y[x+2], 255) for x in range(0, len(y), 3)])) for y in image]
        # These numbers pilfered from display_spalding.c. This is just the top-left corner; it's rotationally
        # symmetric.
        roundness_by_platform = {
            'chalk': [76, 71, 66, 63, 60, 57, 55, 52, 50, 48, 46, 45, 43, 41, 40, 38, 37,
                      36, 34, 33, 32, 31, 29, 28, 27, 26, 25, 24, 23, 22, 22, 21, 20, 19,
                      18, 18, 17, 16, 15, 15, 14, 13, 13, 12, 12, 11, 10, 10, 9, 9, 8, 8, 7,
                      7, 7, 6, 6, 5, 5, 5, 4, 4, 4, 3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1,
                      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            'gabbro': [119, 110, 105, 100, 96, 93, 89, 86, 84, 81, 79, 77, 74, 72, 70, 68, 67,
                       65, 63, 62, 60, 58, 57, 55, 54, 53, 51, 50, 49, 48, 46, 45, 44, 43, 42,
                       41, 40, 39, 38, 37, 36, 35, 34, 33, 32, 31, 30, 30, 29, 28, 27, 26, 26,
                       25, 24, 23, 23, 22, 21, 21, 20, 20, 19, 18, 18, 17, 17, 16, 15, 15, 14,
                       14, 13, 13, 12, 12, 12, 11, 11, 10, 10, 9, 9, 9, 8, 8, 7, 7, 7, 6, 6,
                       6, 6, 5, 5, 5, 4, 4, 4, 4, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1,
                       1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }
        roundness = roundness_by_platform.get(self.pebble.watch_platform)
        if roundness is not None:
            roundness = list(roundness) + list(reversed(roundness))
            for row, skip in zip(rgba, roundness):
                for x in range(3, len(row), 4):
                    if not skip <= x // 4 < len(row) // 4 - skip:
                        row[x] = 0
        return rgba

    def _scale_image(self, image, scale):
        """
        Scale an RGBA image using nearest-neighbor interpolation.

        Each pixel becomes an NxN block of identical pixels.

        :param image: List of lists, where each inner list is a row of RGBA values
        :param scale: Integer scale factor (2 = double size, 3 = triple size, etc.)
        :return: Scaled image in the same format
        """
        if scale == 1:
            return image

        height = len(image)
        width = len(image[0]) // 4  # Divide by 4 since each pixel is RGBA

        scaled_image = []

        # For each row in the original image
        for row_idx in range(height):
            # Each original row needs to be replicated 'scale' times
            for _ in range(scale):
                scaled_row = []
                # For each pixel in the original row
                for pixel_idx in range(width):
                    # Get the RGBA values for this pixel
                    base_idx = pixel_idx * 4
                    r = image[row_idx][base_idx]
                    g = image[row_idx][base_idx + 1]
                    b = image[row_idx][base_idx + 2]
                    a = image[row_idx][base_idx + 3]

                    # Replicate this pixel 'scale' times horizontally
                    for _ in range(scale):
                        scaled_row.extend([r, g, b, a])

                scaled_image.append(scaled_row)

        return scaled_image

    @classmethod
    def _generate_filename(cls):
        return datetime.datetime.now().strftime("pebble_screenshot_%Y-%m-%d_%H-%M-%S.png")

    @classmethod
    def _open(cls, path):
        if sys.platform == 'darwin':
            subprocess.call(["open", path])

    @classmethod
    def add_parser(cls, parser):
        parser = super(ScreenshotCommand, cls).add_parser(parser)
        parser.add_argument('filename', nargs='?', type=str, help="Filename of screenshot")
        parser.add_argument('--no-correction', action="store_true", help="Disable colour correction.")
        parser.add_argument('--no-open', action="store_true", help="Disable automatic opening of image.")
        parser.add_argument('--all-platforms', action='store_true',
                            help="Build and capture screenshots on emulator for each platform supported by this app.")
        parser.add_argument('--scale', type=_positive_int, default=1,
                            help="Scale factor for the screenshot (must be a positive integer)")
        return parser
