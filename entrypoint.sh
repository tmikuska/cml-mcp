#!/usr/bin/env bash
set -e

# Do not default CML_URL. A baked-in hostname (e.g. cml.host.internal) can be
# squatted on a customer LAN, silently redirecting credential POSTs.
CML_MCP_TRANSPORT=${CML_MCP_TRANSPORT:-stdio}
CML_MCP_BIND=${CML_MCP_BIND:-0.0.0.0}
CML_MCP_PORT=${CML_MCP_PORT:-9000}
DEBUG=${DEBUG:-false}

export CML_MCP_TRANSPORT
export CML_MCP_BIND
export CML_MCP_PORT
export DEBUG
if [ -n "${CML_URL:-}" ]; then
  export CML_URL
fi

if [ "$CML_MCP_TRANSPORT" = "stdio" ]; then
  # stdio mode requires URL and credentials in environment
  if [ -z "${CML_URL:-}" ] || [ -z "${CML_USERNAME:-}" ] || [ -z "${CML_PASSWORD:-}" ]; then
    echo "ERROR: CML_URL, CML_USERNAME, and CML_PASSWORD must be set for stdio transport."
    echo "       In HTTP mode, credentials are provided via X-Authorization header."
    exit 1
  fi
  exec cml-mcp
else
  # HTTP mode - credentials provided per-request via headers
  exec uvicorn cml_mcp.server:app \
    --host "${CML_MCP_BIND}" \
    --port "${CML_MCP_PORT}" \
    --workers 1 \
    --access-log
fi
