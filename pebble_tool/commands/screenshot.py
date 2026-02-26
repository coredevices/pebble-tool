
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
import socket
from progressbar import ProgressBar, Bar, ReverseBar, FileTransferSpeed, Timer, Percentage
import signal
import subprocess
import sys
import tempfile
import time
import shutil
import zipfile
from PIL import Image

from libpebble2.communication import PebbleConnection
from libpebble2.exceptions import ScreenshotError
from libpebble2.services.screenshot import Screenshot
from libpebble2.protocol.system import TimeMessage, SetLocaltime, SetUTC

from .base import PebbleCommand, BaseCommand
from .install import ToolAppInstaller
from pebble_tool.commands.sdk.project.build import BuildCommand
from pebble_tool.exceptions import ToolError
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.sdk import sdk_manager
import pebble_tool.sdk.emulator as emulator
from pebble_tool.sdk.emulator import ManagedEmulatorTransport


def _positive_int(value):
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid integer")
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"'{value}' must be >= 1")
    return ivalue


class ScreenshotCommand(PebbleCommand):
    """Takes a screenshot from the watch."""
    command = 'screenshot'

    def __init__(self):
        self.progress_bar = ProgressBar(widgets=[Percentage(), Bar(marker='=', left='[', right=']'), ' ',
                                                 FileTransferSpeed(), ' ', Timer(format='%s')])
        self.started = False

    def __call__(self, args):
        if args.gif_all_platforms:
            if args.all_platforms:
                raise ToolError("--gif-all-platforms cannot be used with --all-platforms.")
            BaseCommand.__call__(self, args)
            self._capture_gif_all_platforms(args)
            return

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

    @classmethod
    def _resolve_gif_start_dt(cls, args):
        now = datetime.datetime.now()
        raw = getattr(args, "gif_start_time", None) or "10:09:58"
        try:
            hh, mm, ss = [int(part) for part in str(raw).split(":")]
            return now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        except Exception:
            raise ToolError("Invalid gif start time '{}'. Expected HH:MM:SS.".format(raw))

    def _grab_processed_image(self, args, show_progress=True, skip_colour_correction=False):
        screenshot = Screenshot(self.pebble)
        self.started = False
        if show_progress:
            screenshot.register_handler("progress", self._handle_progress)
            self.progress_bar.start()
        try:
            image = screenshot.grab_image()
        except ScreenshotError as e:
            if self.pebble.firmware_version.major == 3 and self.pebble.firmware_version.minor == 2:
                # PBL-21154: Screenshots failing with error code 2 (out of memory)
                raise ToolError(str(e) + " (screenshots are known to be broken using firmware 3.2; try the emulator.)")
            else:
                raise ToolError(str(e) + " (try rebooting the watch)")
        if not args.no_correction and not skip_colour_correction:
            image = self._correct_colours(image)
        image = self._roundify(image)
        scale = getattr(args, 'scale', 1)
        if scale > 1:
            image = self._scale_image(image, scale)
        if show_progress:
            self.progress_bar.finish()
        return image

    def _grab_pillow_image_fast(self):
        screenshot = Screenshot(self.pebble)
        try:
            image = screenshot.grab_image()
        except ScreenshotError as e:
            if self.pebble.firmware_version.major == 3 and self.pebble.firmware_version.minor == 2:
                raise ToolError(str(e) + " (screenshots are known to be broken using firmware 3.2; try the emulator.)")
            else:
                raise ToolError(str(e) + " (try rebooting the watch)")

        if not image:
            raise ToolError("No screenshot data received.")
        height = len(image)
        width = len(image[0]) // 3
        data = bytearray()
        for row in image:
            data.extend(row)
        return Image.frombytes("RGB", (width, height), bytes(data), "raw", "RGB").convert("RGBA")

    @classmethod
    def _qemu_monitor_command(cls, monitor_port, command, timeout=1.0):
        with socket.create_connection(("127.0.0.1", int(monitor_port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            try:
                sock.recv(4096)
            except Exception:
                pass
            sock.sendall((command.rstrip() + "\n").encode("utf-8"))
            try:
                sock.recv(4096)
            except Exception:
                pass

    @classmethod
    def _grab_qemu_monitor_image_fast(cls, monitor_port, temp_dir, frame_index):
        ppm_path = os.path.join(temp_dir, "frame_{:04d}.ppm".format(frame_index))
        cls._qemu_monitor_command(monitor_port, "screendump {}".format(ppm_path), timeout=1.0)

        deadline = time.time() + 0.75
        while time.time() < deadline:
            if os.path.exists(ppm_path) and os.path.getsize(ppm_path) > 0:
                img = Image.open(ppm_path)
                out = img.copy()
                img.close()
                return out.convert("RGBA")
            time.sleep(0.01)
        raise ToolError("Timed out waiting for screendump at {}".format(ppm_path))

    def _capture_rollover_gif(self, args, filename, start_dt):
        duration_seconds = 7
        target_fps = args.gif_fps
        frame_interval = 1.0 / float(target_fps)

        frames = []
        frame_timestamps = []
        prime_dt = start_dt - datetime.timedelta(seconds=1)
        # Prime time-setting a few times so the watch applies it before capture starts.
        for _ in range(3):
            self._set_time(pebble=self.pebble, target_datetime=prime_dt, use_localtime=False)
            time.sleep(0.25)
        # Let the watch tick into the requested start time naturally.
        time.sleep(1.05)

        transport = getattr(self.pebble, "transport", None)
        monitor_port = getattr(transport, "qemu_monitor_port", None)
        use_monitor_capture = bool(monitor_port)

        temp_dir = tempfile.mkdtemp(prefix="pebble-screendump-")
        capture_start = time.perf_counter()
        next_capture_time = capture_start
        try:
            while True:
                now = time.perf_counter()
                if now < next_capture_time:
                    time.sleep(next_capture_time - now)
                if time.perf_counter() - capture_start >= duration_seconds:
                    break
                if use_monitor_capture:
                    try:
                        frames.append(self._grab_qemu_monitor_image_fast(monitor_port, temp_dir, len(frames)))
                    except Exception as e:
                        print("Monitor capture failed ({}); falling back to watch screenshot.".format(e))
                        use_monitor_capture = False
                        frames.append(self._grab_pillow_image_fast())
                else:
                    frames.append(self._grab_pillow_image_fast())
                frame_timestamps.append(time.perf_counter())
                next_capture_time += frame_interval
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        output_dir = os.path.dirname(filename)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        if not frames:
            raise ToolError("No frames captured for GIF.")

        if len(frame_timestamps) > 1:
            durations = [
                max(20, int((frame_timestamps[i + 1] - frame_timestamps[i]) * 1000))
                for i in range(len(frame_timestamps) - 1)
            ]
            durations.append(durations[-1])
        else:
            durations = [1000]

        frames[0].save(
            filename,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            disposal=2,
        )
        print("Saved rollover GIF to {}".format(filename))
        # Intentionally do not auto-open GIFs in --gif-all-platforms mode.

    def _capture_gif_all_platforms(self, args):
        project, pbw_path = self._ensure_project_pbw(args)
        platforms, app_version = self._extract_pbw_metadata(pbw_path, project)
        captured_files = []

        screenshots_dir = os.path.join(project.project_dir, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        start_dt = self._resolve_gif_start_dt(args)

        for platform_name in platforms:
            print("Starting rollover GIF capture for {}...".format(platform_name))
            pebble = None
            previous_pebble = getattr(self, "pebble", None)
            try:
                pebble = self._connect_emulator(platform_name, args.sdk, vnc_enabled=False)
                self.pebble = pebble
                ToolAppInstaller(pebble, pbw_path, quiet=True).install()
                filename = self._platform_filename(screenshots_dir, platform_name, app_version, extension="gif")
                self._capture_rollover_gif(args, filename, start_dt)
                captured_files.append(filename)
            except Exception as e:
                print("Failed GIF capture for {}: {}".format(platform_name, e))
                raise
            finally:
                self._close_pebble_connection(pebble)
                self.pebble = previous_pebble
                self._shutdown_platform_emulator(platform_name, args.sdk)
        return captured_files

    def _capture_all_platforms(self, args):
        project, pbw_path = self._ensure_project_pbw(args)
        platforms, app_version = self._extract_pbw_metadata(pbw_path, project)
        captured_files = []

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
                captured_files.append(filename)
            finally:
                self._close_pebble_connection(pebble)
                self.pebble = previous_pebble
                self._shutdown_platform_emulator(platform_name, args.sdk)
        return captured_files

    def _connect_emulator(self, platform_name, sdk_version, vnc_enabled=False):
        transport = ManagedEmulatorTransport(platform_name, sdk_version, vnc_enabled)
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
    def _platform_filename(cls, screenshots_dir, platform_name, app_version, extension="png"):
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_version = cls._sanitize_for_filename(app_version)
        safe_platform = cls._sanitize_for_filename(platform_name)
        return os.path.join(screenshots_dir, "{}_{}_{}.{}".format(safe_platform, safe_version, timestamp, extension))

    @classmethod
    def _set_time(cls, pebble, target_datetime, use_localtime=False):
        target = target_datetime.replace(microsecond=0)
        ts = int(target.timestamp())
        if use_localtime:
            pebble.send_packet(TimeMessage(message=SetLocaltime(time=ts)))
            return
        tz_offset = -time.altzone if time.localtime(ts).tm_isdst and time.daylight else -time.timezone
        tz_offset_minutes = tz_offset // 60
        tz_name = "UTC%+d" % (tz_offset_minutes // 60)
        pebble.send_packet(TimeMessage(message=SetUTC(unix_time=ts, utc_offset=tz_offset_minutes, tz_name=tz_name)))

    @classmethod
    def _set_time_1010(cls, pebble):
        now = datetime.datetime.now()
        target = now.replace(hour=10, minute=10, second=0, microsecond=0)
        # Send twice with a short settle delay to make sure the rendered face reflects the new time.
        cls._set_time(pebble, target)
        time.sleep(0.35)
        cls._set_time(pebble, target)
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

    @classmethod
    def _close_pebble_connection(cls, pebble):
        if not pebble:
            return
        ws = getattr(getattr(pebble, "transport", None), "ws", None)
        if ws is None:
            return
        try:
            ws.close()
        except Exception:
            pass

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
        parser.add_argument('--gif-all-platforms', action='store_true',
                            help="Build and capture a rollover GIF on emulator for each platform supported by this app.")
        parser.add_argument('--gif-fps', type=_positive_int, default=10,
                            help="FPS cap for --gif-all-platforms capture (default: 10).")
        parser.add_argument('--all-platforms', action='store_true',
                            help="Build and capture screenshots on emulator for each platform supported by this app.")
        return parser
