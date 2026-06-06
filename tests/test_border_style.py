# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest

from cml_mcp.border_style import (
    border_style_for_api,
    border_style_from_api,
    normalize_topology_border_styles,
    wire_topology_border_styles,
)

_CANONICAL_WIRE = [
    ("solid", "solid"),
    ("dotted", "dotted"),
    ("dashed", "dashed"),
]
_NON_CANONICAL_WIRE = ["", "2,2", "4,2", "invalid"]


@pytest.mark.parametrize(("wire", "expected"), _CANONICAL_WIRE)
def test_border_style_helpers_accept_canonical(wire, expected):
    assert border_style_from_api(wire) == expected
    assert border_style_for_api(wire) == expected


@pytest.mark.parametrize("wire", _NON_CANONICAL_WIRE)
def test_border_style_helpers_reject_non_canonical(wire):
    for helper in (border_style_from_api, border_style_for_api):
        with pytest.raises(ValueError):
            helper(wire)


def test_wire_topology_border_styles_keeps_canonical():
    payload = {
        "annotations": [{"type": "line", "border_style": "dashed"}],
        "smart_annotations": [{"tag": "core", "border_style": "dotted"}],
    }
    wire_topology_border_styles(payload)
    assert payload["annotations"][0]["border_style"] == "dashed"
    assert payload["smart_annotations"][0]["border_style"] == "dotted"


def test_normalize_topology_border_styles():
    payload = {
        "annotations": [{"type": "line", "border_style": "dashed"}],
        "smart_annotations": [{"tag": "core", "border_style": "solid"}],
    }
    normalize_topology_border_styles(payload)
    assert payload["annotations"][0]["border_style"] == "dashed"
    assert payload["smart_annotations"][0]["border_style"] == "solid"


def test_topology_border_style_roundtrip_stays_canonical():
    topology = {
        "annotations": [{"type": "line", "border_style": "dashed"}],
        "smart_annotations": [{"tag": "core", "border_style": "solid"}],
    }
    normalize_topology_border_styles(topology)
    assert topology["annotations"][0]["border_style"] == "dashed"
    assert topology["smart_annotations"][0]["border_style"] == "solid"
    wire_topology_border_styles(topology)
    assert topology["annotations"][0]["border_style"] == "dashed"
    assert topology["smart_annotations"][0]["border_style"] == "solid"


def _find_empty_string_enums(schema: object) -> list[str]:
    paths: list[str] = []

    def walk(obj: object, path: str = "") -> None:
        if isinstance(obj, dict):
            enum = obj.get("enum")
            if isinstance(enum, list) and "" in enum:
                paths.append(path or "<root>")
            for key, value in obj.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                walk(value, f"{path}[{index}]")

    walk(schema)
    return paths


@pytest.mark.asyncio
async def test_tool_input_schemas_have_no_empty_string_enums():
    """Gemini and strict MCP gateways reject enum values that include \"\"."""
    import importlib

    from fastmcp import FastMCP

    module_names = [
        "system",
        "users_groups",
        "node_definitions",
        "labs",
        "nodes",
        "interfaces",
        "links",
        "annotations",
        "pcap",
    ]
    offenders: dict[str, list[str]] = {}
    for module_name in module_names:
        module = importlib.import_module(f"cml_mcp.tools.{module_name}")
        mcp = FastMCP("schema-test")
        module.register_tools(mcp)
        tools = await mcp.list_tools()
        for tool in tools:
            schema = tool.parameters
            if hasattr(schema, "model_dump"):
                schema = schema.model_dump()
            bad_paths = _find_empty_string_enums(schema)
            if bad_paths:
                offenders[tool.name] = bad_paths

    assert offenders == {}, offenders
