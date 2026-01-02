"""
:Description: Smoke tests for the `pebble_tool` CLI.
"""
from __future__ import annotations

import pytest

from pebble_tool import run_tool

@pytest.mark.parametrize(
    ["args", "expected"],
    [
        # Help menu should print and exit with success
        (["--help"], 0),
        # Command that does not exist should exit with an error code.
        (["dne"], 2),
    ]
)
def test_smoke_cli(args: list[str], expected: int) -> None:
    """
    Simple smoke test that ensures that the help menu is accessible and invalid commands are rejected. Successfully
    executing the help menu should imply that most of the CLI's code has been _imported_, hopefully catching very
    silly mistakes.

    :param args: Arguments to pass to the `pebble_tools` CLI.
    :param expected: Expected POSIX-style error code.
    """
    with pytest.raises(SystemExit) as e:
        run_tool(args=args)

    assert e.value.code == expected