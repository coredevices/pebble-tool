"""
Tests for the ScreenshotCommand.
"""
from __future__ import annotations

import argparse

import pytest

from pebble_tool.commands.screenshot import ScreenshotCommand


def _parse(argv):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    ScreenshotCommand.add_parser(subparsers)
    return parser.parse_args(["screenshot"] + argv)


class TestScaleArgument:
    def test_default(self):
        assert _parse([]).scale == 1

    def test_accepts_integer(self):
        assert _parse(["--scale", "6", "--no-open", "out.png"]).scale == 6

    @pytest.mark.parametrize("value", ["0", "-1", "1.5", "big"])
    def test_rejects_non_positive_integer(self, value):
        with pytest.raises(SystemExit):
            _parse(["--scale", value])


class TestScaleImage:
    def test_nearest_neighbour(self):
        image = [[1, 2, 3, 4, 5, 6, 7, 8]]  # one row, two RGBA pixels
        scaled = ScreenshotCommand._scale_image(None, image, 2)
        assert scaled == [[1, 2, 3, 4, 1, 2, 3, 4, 5, 6, 7, 8, 5, 6, 7, 8]] * 2

    def test_scale_one_is_identity(self):
        image = [[1, 2, 3, 4]]
        assert ScreenshotCommand._scale_image(None, image, 1) is image
