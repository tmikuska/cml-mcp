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
Runtime authentication/session-management tools for CML MCP server.
"""

import logging

from fastmcp.exceptions import ToolError

from cml_mcp.cml_client import CMLTokenExpiredError
from cml_mcp.settings import settings
from cml_mcp.tools.dependencies import get_cml_client_dep

logger = logging.getLogger("cml-mcp.tools.auth")


def register_tools(mcp):
    """Register authentication/session-management tools with the FastMCP server."""

    @mcp.tool(
        annotations={
            "title": "Set CML API Token",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def set_cml_token(token: str) -> bool:
        """
        Replace the CML API token used for the current session with a new one, without restarting the MCP server.

        Use this after a tool call fails because the configured CML API token expired or was revoked. The new
        token is validated against the CML server immediately; if it is rejected, an error is returned and the
        previous authentication state is not restored (obtain a valid token and call this tool again).

        This only affects the current running session/connection. It does not persist across MCP server
        restarts -- to make the change permanent, set a new CML_API_TOKEN environment variable (stdio mode)
        or send the new token in the X-Authorization: Bearer header on future requests (HTTP mode).

        Required: token (the new long-lived CML API access token).
        Returns: true if the new token was accepted.

        Examples:
        - "My CML session expired, here's a new token: <token>"
        - "Update the CML API token to <token>"
        - "Use this new token instead of the old one"
        """
        client = get_cml_client_dep()
        try:
            await client.set_token(token)
        except CMLTokenExpiredError as e:
            raise ToolError(str(e))
        except Exception as e:
            logger.exception("Error setting new CML API token")
            raise ToolError(e)

        # In HTTP mode, this client is shared via cml_client_cache under a key derived
        # from the *old* token's hash. set_token() rotates the client's credentials in
        # place, so without re-keying, a request that still presents the old (now
        # stale/revoked) token would keep hitting this same cache entry and
        # transparently succeed with the new credentials. Move it to a key derived
        # from the new token instead, so only the new token can reach it.
        if settings.cml_mcp_transport == "http" and getattr(client, "_cache_key", None):
            from cml_mcp.tools.dependencies import cml_client_cache
            from cml_mcp.tools.middleware import token_cache_key

            new_cache_key = token_cache_key(token, client._cache_key_suffix)
            await cml_client_cache.rekey(client._cache_key, new_cache_key)
            client._cache_key = new_cache_key

        return True
