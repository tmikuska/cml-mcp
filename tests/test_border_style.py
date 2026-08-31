# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest
from virl2_client.utils import Version

from cml_mcp.border_style import border_style_for_api, border_style_from_api


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("", "solid"),
        ("2,2", "dotted"),
        ("4,2", "dashed"),
    ],
)
def test_border_style_from_api_legacy_controller(wire, expected):
    assert border_style_from_api(wire, Version("2.10.0")) == expected


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("solid", "solid"),
        ("dotted", "dotted"),
        ("dashed", "dashed"),
    ],
)
def test_border_style_from_api_modern_controller(wire, expected):
    assert border_style_from_api(wire, Version("2.11.0")) == expected


def test_border_style_from_api_modern_controller_rejects_legacy():
    with pytest.raises(ValueError, match="Unexpected border_style"):
        border_style_from_api("", Version("2.11.0"))


@pytest.mark.parametrize(
    ("canonical", "controller", "expected"),
    [
        ("solid", "2.10.0", ""),
        ("dotted", "2.10.0", "2,2"),
        ("dashed", "2.10.0", "4,2"),
        ("solid", "2.11.0", "solid"),
        ("dotted", "2.11.0", "dotted"),
    ],
)
def test_border_style_for_api(canonical, controller, expected):
    assert border_style_for_api(canonical, Version(controller)) == expected


def test_border_style_for_api_accepts_legacy_user_input():
    assert border_style_for_api("2,2", Version("2.11.0")) == "dotted"


def test_border_style_for_api_uses_canonical_on_dev_build():
    assert border_style_for_api("dashed", Version("2.11.0-dev0+build.3")) == "dashed"


def test_border_style_for_api_rejects_unknown():
    with pytest.raises(KeyError):
        border_style_for_api("invalid", Version("2.10.0"))
