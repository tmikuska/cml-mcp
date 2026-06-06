# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Tests for the two-stage confirm pattern on destructive tools."""

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type


async def test_wipe_lab_without_confirm_is_rejected(main_mcp_client: Client[FastMCPTransport], created_lab: UUID4Type):
    with pytest.raises(ToolError, match="confirm=true"):
        await main_mcp_client.call_tool(name="wipe_cml_lab", arguments={"lab_id": created_lab})


async def test_wipe_lab_with_confirm_false_is_rejected(main_mcp_client: Client[FastMCPTransport], created_lab: UUID4Type):
    with pytest.raises(ToolError, match="confirm=true"):
        await main_mcp_client.call_tool(name="wipe_cml_lab", arguments={"lab_id": created_lab, "confirm": False})


async def test_wipe_lab_with_confirm_true_proceeds(main_mcp_client: Client[FastMCPTransport], created_lab: UUID4Type):
    result = await main_mcp_client.call_tool(name="wipe_cml_lab", arguments={"lab_id": created_lab, "confirm": True})
    assert result.data is True


async def test_delete_lab_without_confirm_is_rejected(main_mcp_client: Client[FastMCPTransport], created_lab: UUID4Type):
    with pytest.raises(ToolError, match="confirm=true"):
        await main_mcp_client.call_tool(name="delete_cml_lab", arguments={"lab_id": created_lab})
