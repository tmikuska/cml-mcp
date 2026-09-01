# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest

from cml_mcp.border_style import (
    border_style_for_api,
    border_style_from_api,
    normalize_topology_border_styles,
    wire_topology_border_styles,
)


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("", "solid"),
        ("2,2", "dotted"),
        ("4,2", "dashed"),
    ],
)
def test_border_style_from_api_legacy_wire(wire, expected):
    assert border_style_from_api(wire) == expected


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


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("", ""),
        ("2,2", "2,2"),
        ("4,2", "4,2"),
    ],
)
def test_border_style_for_api_legacy_passthrough(wire, expected):
    assert border_style_for_api(wire) == expected


def test_border_style_for_api_rejects_unknown():
    with pytest.raises(KeyError):
        border_style_for_api("invalid")


def test_border_style_from_api_accepts_canonical():
    assert border_style_from_api("dashed") == "dashed"


def test_wire_topology_border_styles():
    payload = {
        "annotations": [{"type": "line", "border_style": "dashed"}],
        "smart_annotations": [{"tag": "core", "border_style": "dotted"}],
    }
    wire_topology_border_styles(payload)
    assert payload["annotations"][0]["border_style"] == "4,2"
    assert payload["smart_annotations"][0]["border_style"] == "2,2"


def test_normalize_topology_border_styles():
    payload = {
        "annotations": [{"type": "line", "border_style": "4,2"}],
        "smart_annotations": [{"tag": "core", "border_style": ""}],
    }
    normalize_topology_border_styles(payload)
    assert payload["annotations"][0]["border_style"] == "dashed"
    assert payload["smart_annotations"][0]["border_style"] == "solid"


def test_topology_border_style_write_read_roundtrip():
    legacy = {
        "annotations": [{"type": "line", "border_style": "4,2"}],
        "smart_annotations": [{"tag": "core", "border_style": ""}],
    }
    normalize_topology_border_styles(legacy)
    assert legacy["annotations"][0]["border_style"] == "dashed"
    assert legacy["smart_annotations"][0]["border_style"] == "solid"
    wire_topology_border_styles(legacy)
    assert legacy["annotations"][0]["border_style"] == "4,2"
    assert legacy["smart_annotations"][0]["border_style"] == ""


@pytest.mark.asyncio
async def test_create_full_topology_posts_legacy_wire_from_topology_model():
    from cml_mcp.cml.simple_webserver.schemas.topologies import Topology
    from cml_mcp.tools.labs import create_full_topology_from_obj

    posted: dict = {}

    class FakeClient:
        async def post(self, endpoint, data=None, **kwargs):
            posted["endpoint"] = endpoint
            posted["data"] = data
            return {"id": "00000000-0000-4000-8000-000000000001"}

    payload = {
        "lab": {"version": "0.1.0", "title": "Import Test", "node_staging": None},
        "nodes": [],
        "links": [],
        "annotations": [
            {
                "type": "line",
                "border_color": "#0000FF",
                "border_style": "dashed",
                "color": "#0000FF",
                "thickness": 2,
                "x1": 100.0,
                "y1": 100.0,
                "x2": 200.0,
                "y2": 200.0,
                "z_index": 1,
                "line_start": "arrow",
                "line_end": "circle",
            }
        ],
        "smart_annotations": [{"tag": "core", "border_style": "dotted"}],
    }
    topology = Topology(**payload)
    await create_full_topology_from_obj(topology, FakeClient())  # type: ignore[arg-type]
    assert posted["endpoint"] == "/import"
    assert posted["data"]["annotations"][0]["border_style"] == "4,2"
    assert posted["data"]["smart_annotations"][0]["border_style"] == "2,2"


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

    import virl2_client
    from fastmcp import FastMCP

    virl2_client.ClientLibrary = lambda *args, **kwargs: type(
        "FakeClientLibrary",
        (),
        {"check_controller_version": lambda self: "2.10.0"},
    )()

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
