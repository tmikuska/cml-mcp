# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""
PyATS-based CLI execution, used as a fallback for CML controllers older than
2.11 that do not expose POST /labs/{lab_id}/nodes/{node_id}/cli.

Loads the server-provided pyATS testbed (GET /labs/{lab_id}/pyats_testbed) and
connects to the node directly over SSH via the testbed's terminal-server
proxy. Unlike the CML-internal `termws` binary, this works for any standalone
user connecting to a remote CML controller.

`pyats`/`genie` are declared as an optional extra (`cml-mcp[pyats]`) rather
than a hard dependency, since most users are on CML 2.11+ and never need this
fallback -- PYATS_AVAILABLE lets callers detect and report a clear error when
it's missing instead of a raw ImportError.
"""

import io
import logging
import os

logger = logging.getLogger("cml-mcp.tools.pyats_cli")

_DEFAULT_SSH_OPTIONS = "-o IdentitiesOnly=yes -o IdentityAgent=none"

try:
    from pyats.topology.loader.base import TestbedFileLoader
    from pyats.topology.loader.markup import TestbedMarkupProcessor

    PYATS_AVAILABLE = True
except ImportError:
    TestbedFileLoader = None
    TestbedMarkupProcessor = None
    PYATS_AVAILABLE = False


def pyats_send_cli_command_sync(
    testbed_yaml: str,
    username: str,
    password: str,
    label: str,
    commands: str,
    config_command: bool,
) -> str:
    """
    Synchronous helper for send_cli_command to isolate blocking operations in a thread.
    Loads a pyATS testbed from server-provided YAML and executes commands directly over SSH.
    """
    loader = TestbedFileLoader(
        markupprocessor=TestbedMarkupProcessor(
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
