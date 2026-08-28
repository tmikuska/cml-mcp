# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest

from cml_mcp.border_style import border_style_for_api, border_style_from_api


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("", ""),
        ("2,2", "2,2"),
        ("4,2", "4,2"),
    ],
)
def test_border_style_for_api_legacy_values(wire, expected):
    assert border_style_for_api(wire) == expected


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("solid", ""),
        ("dotted", "2,2"),
        ("dashed", "4,2"),
    ],
)
def test_border_style_for_api_canonical_aliases(alias, expected):
    assert border_style_for_api(alias) == expected


def test_border_style_for_api_rejects_unknown():
    with pytest.raises(ValueError):
        border_style_for_api("invalid")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4,2", "4,2"),
        ("dashed", "4,2"),
    ],
)
def test_border_style_from_api(value, expected):
    assert border_style_from_api(value) == expected
