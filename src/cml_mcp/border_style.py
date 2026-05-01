# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Annotation border_style helpers for MCP tools."""

from __future__ import annotations

from typing import Literal

CANONICAL_BORDER_STYLES = ("solid", "dotted", "dashed")
BorderStyleLiteral = Literal["solid", "dotted", "dashed"]

_LEGACY_TO_CANONICAL: dict[str, str] = {
    "": "solid",
    "2,2": "dotted",
    "4,2": "dashed",
}
_CANONICAL_TO_LEGACY: dict[str, str] = {
    canonical: legacy for legacy, canonical in _LEGACY_TO_CANONICAL.items()
}


def migrate_legacy_border_style(value: str) -> str:
    """Normalize legacy or canonical border_style wire values to MCP canonical form."""
    if value in CANONICAL_BORDER_STYLES:
        return value
    try:
        return _LEGACY_TO_CANONICAL[value]
    except KeyError as exc:
        raise ValueError(f"Unknown border_style: {value!r}") from exc


def canonical_border_style(value: str) -> str:
    """Return canonical border_style wire values for CML REST/import."""
    return migrate_legacy_border_style(value)


def border_style_from_api(value: str) -> str:
    """Normalize API or legacy topology border_style to canonical MCP values."""
    return migrate_legacy_border_style(value)


def border_style_for_api(value: str) -> str:
    """Serialize MCP border_style for CML REST/import (legacy wire on 2.10.x)."""
    return _CANONICAL_TO_LEGACY[migrate_legacy_border_style(value)]


def _map_topology_border_styles(payload: dict, transform) -> dict:
    for key in ("annotations", "smart_annotations"):
        for item in payload.get(key, []):
            if isinstance(item, dict) and "border_style" in item:
                item["border_style"] = transform(str(item["border_style"]))
    return payload


def wire_topology_border_styles(payload: dict) -> dict:
    """Ensure topology import payloads use legacy border_style wire values."""
    return _map_topology_border_styles(payload, border_style_for_api)


def normalize_topology_border_styles(payload: dict) -> dict:
    """Convert legacy border_style values in a topology payload to canonical MCP values."""
    return _map_topology_border_styles(payload, border_style_from_api)
