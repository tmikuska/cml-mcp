# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
CLI command and console log tools for CML MCP server.
"""

import asyncio
import io
import logging
import os
import re

import httpx
import yaml
from fastmcp.exceptions import ToolError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.nodes import NodeLabel
from cml_mcp.tools.dependencies import get_cml_client_dep
from cml_mcp.types import ConsoleLogOutput

try:
    from cml_mcp.tools.unicon_cli import UNICON_AVAILABLE, unicon_send_cli_command_sync
except ImportError:
    UNICON_AVAILABLE = False

try:
    from pyats.topology.loader.base import TestbedFileLoader as _PyatsTFLoader
    from pyats.topology.loader.markup import TestbedMarkupProcessor as _PyatsTMProcessor
except ImportError:
    _PyatsTFLoader = None
    _PyatsTMProcessor = None

logger = logging.getLogger("cml-mcp.tools.cli")

_DEFAULT_SSH_OPTIONS = "-o IdentitiesOnly=yes -o IdentityAgent=none"


def _send_cli_command_sync(
    testbed_yaml: str,
    username: str,
    password: str,
    label: str,
    commands: str,
    config_command: bool,
) -> str:
    """
    Synchronous helper for send_cli_command to isolate blocking operations in a thread.
    Loads a PyATS testbed from server-provided YAML and executes commands directly.
    """
    loader = _PyatsTFLoader(
        markupprocessor=_PyatsTMProcessor(
            reference=True,
            callable=False,
            env_var=False,
            include_file=False,
            ask=False,
            encode=False,
            cli_var=False,
            extend_list=False,
        ),
        enable_extensions=False,
    )
    testbed = loader.load(io.StringIO(testbed_yaml))

    terminal = testbed.devices.terminal_server
    terminal.credentials.default.username = username
    terminal.credentials.default.password = password
    terminal.connections.cli.ssh_options = _DEFAULT_SSH_OPTIONS

    pyats_device = testbed.devices[label]
    pyats_device.connect(logfile=os.devnull, log_stdout=False, learn_hostname=True)
    try:
        if config_command:
            results = pyats_device.configure(commands, log_stdout=False)
        else:
            results = pyats_device.execute(commands, log_stdout=False)

        if isinstance(results, dict):
            output = ""
            for cmd, cmd_output in results.items():
                output += f"Command: {cmd}\nOutput:\n{cmd_output}\n"
        else:
            output = str(results)

        return output
    finally:
        pyats_device.destroy()


def register_tools(mcp):
    """Register all CLI and console tools with the FastMCP server."""

    @mcp.tool(
        annotations={"title": "Get Console Logs for a CML Node", "readOnlyHint": True},
    )
    async def get_console_log(
        lid: UUID4Type,
        nid: UUID4Type,
    ) -> list[ConsoleLogOutput]:
        """
        Get console output history by lab and node UUID. Node must be started.
        Returns list of log entries with time (ms since start) and message. Includes all console ports.
        Useful for troubleshooting, monitoring boot progress, and verifying CLI command results.
        """
        client = get_cml_client_dep()
        return_lines = []
        for i in range(0, 2):  # Assume a maximum of 2 consoles per node
            try:
                resp = await client.get(f"/labs/{lid}/nodes/{nid}/consoles/{i}/log")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    continue  # Console index does not exist, try the next one
                else:
                    raise ToolError(f"HTTP error {e.response.status_code}: {e.response.text}")
            except Exception as e:
                logger.error(f"Error getting console log for node {nid} in lab {lid}: {str(e)}", exc_info=True)
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

        return return_lines

    @mcp.tool(
        annotations={"title": "Send CLI Command to CML Node", "readOnlyHint": False, "destructiveHint": True},
    )
    async def send_cli_command(
        lid: UUID4Type,
        label: NodeLabel,  # pyright: ignore[reportInvalidTypeForm]
        commands: str,
        config_command: bool = False,
    ) -> str:
        """
        Send CLI commands to running node by lab UUID and node label (not UUID). Node must be in BOOTED state.
        CRITICAL: Can modify device state. Review commands before executing, especially with config_command=true.
        Separate multiple commands with newlines.
        config_command=false (default): exec/operational mode. config_command=true: config mode (omit "configure terminal"/"end").
        Returns command output text.
        """
        client = get_cml_client_dep()

        use_unicon = _PyatsTFLoader is None and UNICON_AVAILABLE
        if not use_unicon and _PyatsTFLoader is None:
            raise ToolError(
                "PyATS and Genie are required to send commands to running devices. "
                "Install with: pip install 'cml-mcp[pyats]'"
            )

        try:
            testbed_raw = await client.get(f"/labs/{lid}/pyats_testbed", is_binary=True)
            testbed_yaml = testbed_raw.decode("utf-8")

            if use_unicon:
                pyats_data = yaml.safe_load(testbed_yaml)
                nodes_data = await client.get(f"/labs/{lid}/nodes", params={"data": True, "operational": True})
                output = await asyncio.to_thread(
                    unicon_send_cli_command_sync,
                    pyats_data,
                    nodes_data,
                    str(label),
                    commands,
                    config_command,
                )
            else:
                output = await asyncio.to_thread(
                    _send_cli_command_sync,
                    testbed_yaml,
                    client.username,
                    client.password,
                    str(label),
                    commands,
                    config_command,
                )
            return output
        except Exception as e:
            logger.error(f"Error sending CLI command '{commands}' to node {label} in lab {lid}: {str(e)}", exc_info=True)
            raise ToolError(e)
