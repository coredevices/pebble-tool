
__author__ = 'katharine'

import argparse
import datetime
import itertools
import png
import os.path
from progressbar import ProgressBar, Bar, ReverseBar, FileTransferSpeed, Timer, Percentage
import subprocess
import sys

from libpebble2.exceptions import ScreenshotError
from libpebble2.services.screenshot import Screenshot

from .base import PebbleCommand
from pebble_tool.exceptions import ToolError


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
        super(ScreenshotCommand, self).__call__(args)
        screenshot = Screenshot(self.pebble)
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
        if not args.no_correction:
            image = self._correct_colours(image)
        image = self._roundify(image)
        if args.scale > 1:
            image = self._scale_image(image, args.scale)
        self.progress_bar.finish()

        filename = self._generate_filename() if args.filename is None else args.filename
        png.from_array(image, mode='RGBA;8').save(filename)
        print("Saved screenshot to {}".format(filename))
        if not args.no_open:
            self._open(os.path.abspath(filename))

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
        should_roundify = (self.pebble.watch_platform == 'chalk')
        # These numbers pilfered from display_spalding.c. This is just the top-left corner; it's rotationally
        # symmetric.
        roundness = [76, 71, 66, 63, 60, 57, 55, 52, 50, 48, 46, 45, 43, 41, 40, 38, 37,
                     36, 34, 33, 32, 31, 29, 28, 27, 26, 25, 24, 23, 22, 22, 21, 20, 19,
                     18, 18, 17, 16, 15, 15, 14, 13, 13, 12, 12, 11, 10, 10, 9, 9, 8, 8, 7,
                     7, 7, 6, 6, 5, 5, 5, 4, 4, 4, 3, 3, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1,
                     0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        roundness.extend(reversed(roundness))
        if should_roundify:
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
        parser.add_argument('--scale', type=_positive_int, default=1,
                            help="Scale factor for the screenshot (must be a positive integer)")
        return parser
