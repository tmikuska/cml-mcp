#!/usr/bin/env python3
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
Docker HEALTHCHECK for cml-mcp.

- stdio transport: verify a 'cml-mcp' process is running (there is no network
  endpoint to probe in this mode).
- http transport: verify a local TCP connection to CML_MCP_BIND:CML_MCP_PORT
  succeeds (a lightweight liveness check; it does not exercise application logic
  or require credentials).

Exits 0 (healthy) or 1 (unhealthy), per the Docker HEALTHCHECK contract.
"""

import os
import socket
import sys


def check_stdio() -> bool:
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmdline = f.read().decode(errors="ignore")
            except OSError:
                continue
            if "cml-mcp" in cmdline or "cml_mcp" in cmdline:
                return True
    except OSError:
        pass
    return False


def check_http() -> bool:
    bind = os.environ.get("CML_MCP_BIND", "127.0.0.1")
    port = int(os.environ.get("CML_MCP_PORT", "9000"))
    # A wildcard bind isn't itself connectable; probe the matching loopback address instead.
    if bind == "0.0.0.0":
        host = "127.0.0.1"
    elif bind == "::":
        host = "::1"
    else:
        host = bind
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def main() -> int:
    transport = os.environ.get("CML_MCP_TRANSPORT", "stdio")
    healthy = check_http() if transport == "http" else check_stdio()
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
