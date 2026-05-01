# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Annotation border_style helpers for MCP tools."""

from __future__ import annotations

from cml_mcp.cml.simple_webserver.schemas.common import (
    canonical_border_style,
    migrate_legacy_border_style,
)


def border_style_from_api(value: str) -> str:
    """Normalize API or legacy topology border_style to canonical MCP values."""
    return migrate_legacy_border_style(value)


def border_style_for_api(value: str) -> str:
    """Serialize MCP border_style for CML REST/import (canonical wire only)."""
    return canonical_border_style(migrate_legacy_border_style(value))


def _map_topology_border_styles(payload: dict, transform) -> dict:
    for key in ("annotations", "smart_annotations"):
        for item in payload.get(key, []):
            if isinstance(item, dict) and "border_style" in item:
                item["border_style"] = transform(str(item["border_style"]))
    return payload


def wire_topology_border_styles(payload: dict) -> dict:
    """Ensure topology import payloads use canonical border_style wire values."""
    return _map_topology_border_styles(payload, border_style_for_api)


def normalize_topology_border_styles(payload: dict) -> dict:
    """Convert legacy border_style values in a topology payload to canonical MCP values."""
    return _map_topology_border_styles(payload, border_style_from_api)
