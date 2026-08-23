# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.

# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.

"""
CLI command and console log tools for CML MCP server.
"""

import asyncio
import logging
import re

import httpx
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.nodes import NodeLabel
from cml_mcp.tools.dependencies import get_cml_client_dep
from cml_mcp.tools.pyats_cli import PYATS_AVAILABLE, pyats_send_cli_command_sync
from cml_mcp.types import ConsoleLogOutput

logger = logging.getLogger("cml-mcp.tools.cli")


async def _resolve_node_id_by_label(client, lab_id: UUID4Type, label: str) -> UUID4Type:
    """
    Find a node's UUID by its label within a lab, using the CML REST API.
    """
    nodes = await client.get(f"/labs/{lab_id}/nodes", params={"data": True, "operational": False})
    for node in nodes:
        if node["label"] == label:
            return node["id"]
    raise ToolError(f"No node with label '{label}' was found in lab {lab_id}")


async def _send_cli_command_native(client, lab_id: UUID4Type, label: str, commands: str, config_command: bool, console: int) -> str:
    """
    Send CLI commands via the native CML API (POST /labs/{lab_id}/nodes/{node_id}/cli, CML 2.11+).
    That endpoint only accepts a single command per request, so multi-line input is split up
    and sent one line at a time.
    """
    node_id = await _resolve_node_id_by_label(client, lab_id, label)
    command_lines = [line for line in commands.splitlines() if line.strip()]
    if not command_lines:
        raise ToolError("No CLI command was provided")

    outputs = []
    for command in command_lines:
        result = await client.post(
            f"/labs/{lab_id}/nodes/{node_id}/cli",
            data={
                "config_command": config_command,
                "command": command,
                "serial_port": console,
                "timeout": 300,
            },
            timeout=310,  # Slightly above the server-side max CLI timeout (300s).
        )
        outputs.append(result if len(command_lines) == 1 else f"Command: {command}\nOutput:\n{result}\n")

    return "".join(outputs) if len(command_lines) > 1 else outputs[0]


async def _send_cli_command_pyats(client, lab_id: UUID4Type, label: str, commands: str, config_command: bool) -> str:
    """
    Fallback for CML controllers older than 2.11 that lack the native /cli endpoint: load the
    server-provided pyATS testbed and connect to the node directly over SSH.
    """
    if not PYATS_AVAILABLE:
        raise ToolError(
            "This CML server is older than 2.11 and does not support the native CLI API. "
            "Sending CLI commands requires pyATS, which is not installed. "
            "Install with: pip install 'cml-mcp[pyats]'"
        )

    testbed_raw = await client.get(f"/labs/{lab_id}/pyats_testbed", is_binary=True)
    testbed_yaml = testbed_raw.decode("utf-8")

    return await asyncio.to_thread(
        pyats_send_cli_command_sync,
        testbed_yaml,
        client.username,
        client.password,
        label,
        commands,
        config_command,
    )


def register_tools(mcp):
    """Register all CLI and console tools with the FastMCP server."""

    @mcp.tool(
        annotations={"title": "Get Console Logs for a CML Node", "readOnlyHint": True},
    )
    async def get_console_log(
        lab_id: UUID4Type,
        node_id: UUID4Type,
        console: int = 0,
    ) -> list[ConsoleLogOutput]:
        """
        Get the console output history for a node by lab and node UUID. The node must be started.

        Returns log entries (time in ms since start + message) from the selected serial console
        (default 0). Some nodes (e.g. Docker-based) expose multiple consoles -- use console=1 for
        the second port. Useful for boot troubleshooting and verifying CLI command results.

        Examples:
        - "Show me the console output for router R1"
        - "Get the boot log for the firewall node"
        - "Tail the second console (console 1) on the Alpine container"
        """

        client = get_cml_client_dep()
        return_lines = []
        try:
            resp = await client.get(f"/labs/{lab_id}/nodes/{node_id}/consoles/{console}/log")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                raise ToolError(f"Console index {console} does not exist for node {node_id}")
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            logger.exception("Error getting console log for node %s in lab %s", node_id, lab_id)
            raise ToolError(e)
        lines = re.split(r"\r?\n", resp)
        for line in lines:
            if not line.startswith("|"):
                if len(return_lines) > 0:
                    # Append to the last message if the line does not start with a timestamp
                    return_lines[-1].message += "\n" + line
                continue
            _, log_time, msg = line.split("|", 2)
            return_lines.append(ConsoleLogOutput(time=int(log_time), message=msg))
        # See DEVELOPMENT.md "Object-typed return values": dump after construction so FastMCP doesn't double-marshal.
        return [entry.model_dump(exclude_unset=True) for entry in return_lines]

    @mcp.tool(
        annotations={"title": "Send CLI Command to CML Node", "readOnlyHint": False, "destructiveHint": True},
    )
    async def send_cli_command(
        lab_id: UUID4Type,
        label: NodeLabel,  # pyright: ignore[reportInvalidTypeForm]
        commands: str,
        config_command: bool = False,
        console: int = 0,
    ) -> str:
        """
        Send CLI commands to a running node. Identify the node by lab UUID and node label (NOT
        node UUID). Node must be in BOOTED state. Returns command output text.

        Uses the CML server-side CLI API when available (CML 2.11+); transparently falls back
        to a direct pyATS/SSH connection on older controllers (console selection only applies
        to the native API path).

        - Separate multiple commands with newlines.
        - config_command=false (default): exec/operational mode (e.g. "show version").
        - config_command=true: configuration mode -- DO NOT include "configure terminal" or "end".
        - Optional console: pick a non-default serial console (e.g. console=1 for some Docker nodes).

        CRITICAL: Can modify device state. Review commands carefully before executing, especially
        when config_command=true.

        Examples:
        - "Run 'show ip route' on router R1 in lab abc123"
        - "Configure interface Gi0/1 with IP 10.0.0.1/24 on R1"
        - "Show the running config of the firewall"
        """
        client = get_cml_client_dep()

        try:
            if await client.supports_native_cli():
                return await _send_cli_command_native(client, lab_id, str(label), commands, config_command, console)
            return await _send_cli_command_pyats(client, lab_id, str(label), commands, config_command)
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except ToolError:
            raise
        except Exception as e:
            logger.exception("Error sending CLI command to node %s in lab %s", label, lab_id)
            raise ToolError(e)
