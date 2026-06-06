# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest

from cml_mcp.cml_client import _controller_supports_native_cli, _strip_controller_version


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.11.0+build.1", (2, 11, 0)),
        ("2.11.0dev0", (2, 11, 0)),
        ("2.11.0.dev0", (2, 11, 0)),
        ("2.1.0-dev0+build8.7ee86bf8", (2, 1, 0)),
        ("v2.11.0dev0-4", (2, 11, 0)),
        ("2.11.0", (2, 11, 0)),
        ("2.12.1", (2, 12, 1)),
        ("invalid", None),
    ],
)
def test_strip_controller_version(version, expected):
    assert _strip_controller_version(version) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.11.0+build.1", True),
        ("2.11.0dev0", True),
        ("2.11.0.dev0", True),
        ("v2.11.0dev0-4", True),
        ("2.11.0", True),
        ("2.12.1", True),
        ("2.10.2", False),
        ("2.10.99", False),
        ("invalid", False),
    ],
)
def test_controller_supports_native_cli(version, expected):
    assert _controller_supports_native_cli(version) is expected
