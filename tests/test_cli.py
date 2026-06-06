# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ToolError

from cml_mcp.tools.cli import _send_cli_command

LAB_ID = "90f84e38-a71c-4d57-8d90-00fa8a197385"
NODE_ID = "11111111-1111-4111-8111-111111111111"


def _client_with_node(label: str = "R1") -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value=[{"label": label, "id": NODE_ID}])
    client.post = AsyncMock(return_value="ok")
    return client


async def test_native_cli_sends_multiline_config_as_one_post():
    client = _client_with_node()
    command = "interface GigabitEthernet0/1\nip address 10.0.0.1 255.255.255.0"
    result = await _send_cli_command(client, LAB_ID, "R1", command, True, 1)
    assert result == "ok"
    client.post.assert_awaited_once_with(
        f"/labs/{LAB_ID}/nodes/{NODE_ID}/cli",
        data={
            "config_command": True,
            "command": command,
            "serial_port": 1,
            "timeout": 300,
        },
        timeout=310,
    )


async def test_native_cli_rejects_blank_commands():
    client = _client_with_node()
    with pytest.raises(ToolError, match="No CLI command"):
        await _send_cli_command(client, LAB_ID, "R1", "\n  \n", False, 0)
    client.post.assert_not_awaited()
