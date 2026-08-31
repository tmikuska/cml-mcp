# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Annotation border_style wire conversion for MCP tools."""

from __future__ import annotations

from typing import Literal

from virl2_client.utils import Version

# Match the CML release that ships canonical border_style on the REST API.
# If 2.11.0 releases without that change, bump this to the release that includes it.
CANONICAL_BORDER_STYLE_MIN_VERSION = Version("2.11.0")

CANONICAL_BORDER_STYLES = ("solid", "dotted", "dashed")
BorderStyleLiteral = Literal["solid", "dotted", "dashed"]

_LEGACY_TO_CANONICAL: dict[str, str] = {
    "": "solid",
    "2,2": "dotted",
    "4,2": "dashed",
}

_CANONICAL_TO_LEGACY: dict[str, str] = {
    v: k for k, v in _LEGACY_TO_CANONICAL.items()
}


def _to_canonical(value: str) -> str:
    if value in CANONICAL_BORDER_STYLES:
        return value
    return _LEGACY_TO_CANONICAL[value]


def border_style_from_api(value: str, controller_version: Version) -> str:
    if controller_version >= CANONICAL_BORDER_STYLE_MIN_VERSION:
        if value not in CANONICAL_BORDER_STYLES:
            raise ValueError(f"Unexpected border_style from controller: {value!r}")
        return value
    return _LEGACY_TO_CANONICAL[value]


def border_style_for_api(value: str, controller_version: Version) -> str:
    canonical = _to_canonical(value)
    if controller_version >= CANONICAL_BORDER_STYLE_MIN_VERSION:
        return canonical
    return _CANONICAL_TO_LEGACY[canonical]
