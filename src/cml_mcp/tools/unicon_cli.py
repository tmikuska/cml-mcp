import logging

import yaml
from simple_core.config_extraction.utils import TERMWS_BINARY, remove_unicon_loggers
from unicon import Connection
from unicon.core.errors import ConnectionError as UniconConnectionError
from unicon.core.errors import StateMachineError as UniconStateMachineError
from unicon.core.errors import TimeoutError as UniconTimeoutError

from cml_mcp.cml.simple_webserver.schemas.common import UUID4Type
from cml_mcp.cml.simple_webserver.schemas.nodes import NodeLabel
from cml_mcp.cml_client import CMLClient

logger = logging.getLogger("cml-mcp.tools.unicon_cli")

TIMEOUT = 300


def unicon_send_cli_command_sync(
    client: CMLClient,
    lid: UUID4Type,
    label: NodeLabel,  # pyright: ignore[reportInvalidTypeForm]
    commands: str,
    config_command: bool,
    console: int = 0,
) -> str:
    resp = client.vclient._session.get(f"/labs/{lid}")
    lab_data = resp.json()
    if "lab_exec" not in lab_data["effective_permissions"]:
        raise Exception("You do not have permission to execute commands in this lab's nodes")

    resp = client.vclient._session.get(f"/labs/{lid}/pyats_testbed")
    pyats_data = yaml.safe_load(resp.text)
    device_pyats_data = pyats_data["devices"][label]

    resp = client.vclient._session.get(f"/labs/{lid}/nodes", params={"data": True, "operational": True})
    if resp.status_code != 200:
        raise Exception("Cannot retrieve node console key. Is the node running?")

    nodes = resp.json()
    for node in nodes:
        if node["label"] == label:
            consoles = node["operational"]["serial_consoles"]
            try:
                console_key = consoles[console]["console_key"]
            except IndexError:
                raise Exception(f"Console index {console} does not exist for node {label}")
            break
    else:
        raise Exception("Cannot retrieve node console key. Is the node running?")

    connect_command = f"{TERMWS_BINARY} -host [::1] -port 8006 {console_key}"
    connection = None
    try:
        connection = Connection(
            hostname=label,
            start=[connect_command],
            os=device_pyats_data["os"],
            series=device_pyats_data.get("series"),  # can be None
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
        connection.settings.ENV = {"TOKEN": client.vclient._session.auth.token}

        if config_command:
            result = connection.configure(commands, timeout=TIMEOUT)
        else:
            result = connection.execute(commands, timeout=TIMEOUT)

        return result
    except (UniconTimeoutError, UniconStateMachineError, UniconConnectionError) as exc:
        logger.error(
            "Error sending CLI command to node %s in lab %s: %s: %s",
            label,
            lid,
            type(exc).__name__,
            exc,
        )
        raise
    except Exception:
        logger.exception("Error sending CLI command to node %s in lab %s", label, lid)
        raise
    finally:
        if connection is not None:
            try:
                connection.disconnect()
            except Exception as disconnect_exc:
                logger.warning("Failed to disconnect from %s after CLI command: %s", label, disconnect_exc)
            if connection.log:
                remove_unicon_loggers(connection.log.name)
