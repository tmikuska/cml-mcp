import logging
import os

from unicon import Connection

logger = logging.getLogger("cml-mcp.tools.unicon_cli")

TERMWS_BINARY = "/usr/local/bin/termws"
UNICON_AVAILABLE = os.path.exists(TERMWS_BINARY)
TIMEOUT = 300


def unicon_send_cli_command_sync(
    pyats_data: dict,
    nodes_data: list,
    label: str,
    commands: str,
    config_command: bool,
) -> str:
    """Execute CLI commands via Unicon using the internal termws binary.

    Args:
        pyats_data: Pre-fetched pyATS testbed dict (from GET /labs/{lid}/pyats_testbed).
        nodes_data: Pre-fetched node list (from GET /labs/{lid}/nodes?data=true&operational=true).
        label: Node label to target.
        commands: CLI commands to execute.
        config_command: True for config mode, False for exec mode.
    """
    device_pyats_data = pyats_data["devices"][label]

    for node in nodes_data:
        if node["label"] == label:
            consoles = node["operational"]["serial_consoles"]
            console_key = consoles[0]["console_key"]
            break
    else:
        raise Exception("Cannot retrieve node console key. Is the node running?")

    connect_command = f"{TERMWS_BINARY} -host [::1] -port 8006 -internal {console_key}"
    connection = None
    try:
        connection = Connection(
            hostname=label,
            start=[connect_command],
            os=device_pyats_data["os"],
            series=device_pyats_data.get("series"),
            credentials=device_pyats_data["credentials"],
            log_stdout=False,
            log_buffer=True,
            learn_hostname=True,
            learn_tokens=False,
            connection_timeout=10,
            prompt_recovery=True,
        )
        connection.settings.GRACEFUL_DISCONNECT_WAIT_SEC = 0
        connection.settings.POST_DISCONNECT_WAIT_SEC = 0
        connection.settings.LEARN_DEVICE_TOKENS = False

        if config_command:
            result = connection.configure(commands, timeout=TIMEOUT)
        else:
            result = connection.execute(commands, timeout=TIMEOUT)

        return result
    except Exception as exc:
        logger.exception(f"Error sending CLI command '{commands}' to node {label}: {str(exc)}")
        raise
    finally:
        if connection is not None:
            connection.disconnect()
