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
Shared helper for sanitizing upstream CML HTTP errors before they cross the MCP
trust boundary back to the calling LLM/client.

The raw CML response body (e.g. a Django/DRF traceback, an internal path, or a
validation error echoing request internals) must never be forwarded verbatim to
the client. This module maps status codes to a small set of generic, actionable
messages and logs the full upstream body server-side only.
"""

import logging

import httpx
from fastmcp.exceptions import ToolError

logger = logging.getLogger("cml-mcp.tools.errors")

_GENERIC_MESSAGES = {
    400: "The request was rejected by the CML server as invalid.",
    401: "Authentication with the CML server failed or has expired.",
    403: "The CML server denied permission for this operation.",
    404: "The requested CML resource was not found.",
    409: "The request conflicts with the current state of the CML resource.",
    422: "The CML server rejected the request payload as unprocessable.",
    429: "The CML server is rate-limiting requests. Try again later.",
}


def sanitize_http_error(e: httpx.HTTPStatusError) -> ToolError:
    """
    Build a ToolError with a generic, status-code-keyed message, and log the full
    upstream response body server-side (never returned to the client).

    Args:
        e: The httpx.HTTPStatusError raised by the failed request.

    Returns:
        A ToolError safe to raise back to the MCP client.
    """
    status_code = e.response.status_code
    logger.warning("Upstream CML HTTP error %s: %s", status_code, e.response.text)
    if status_code in _GENERIC_MESSAGES:
        message = _GENERIC_MESSAGES[status_code]
    elif 500 <= status_code < 600:
        message = "The CML server encountered an internal error processing this request."
    else:
        message = "The CML server rejected this request."
    return ToolError(f"HTTP error {status_code}: {message}")
