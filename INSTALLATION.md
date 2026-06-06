# Installation Guide

This guide will help you set up the CML MCP server so you can control Cisco Modeling Labs using AI assistants like Claude Desktop. Whether you're new to CML or an experienced power user, we'll get you up and running.

**Choose Your Installation Method:**

- **Just want to try it out?** → Use the [uvx quick start](#using-uvx-easiest) (easiest, no manual installation needed)
- **Need to share with your team?** → Try [HTTP Transport mode](#http-transport)

## Table of Contents

- [Requirements](#requirements)
- [Standard I/O (stdio) Transport](#standard-io-stdio-transport)
  - [Using uvx (Easiest)](#using-uvx-easiest)
  - [Using FastMCP CLI](#using-fastmcp-cli)
- [HTTP Transport](#http-transport)
  - [Recommended deployment: behind a TLS-terminating reverse proxy](#recommended-deployment-behind-a-tls-terminating-reverse-proxy)
  - [Running the HTTP Server](#running-the-http-server)
  - [Network exposure](#network-exposure)
  - [Configuring MCP Clients](#configuring-mcp-clients)
  - [Docker with HTTP](#docker-with-http-transport)

## Requirements

### What You'll Need

**On Your Computer:**

- **Python 3.12, 3.13 or 3.14** - The programming language runtime ([download here](https://www.python.org/downloads/) if needed)
- **[uv](https://docs.astral.sh/uv/)** - A modern Python package manager that makes installation easy ([install instructions](https://docs.astral.sh/uv/getting-started/installation/))

**Your CML Server:**

- **Cisco Modeling Labs (CML) 2.11** — this server talks to the current controller only. Device CLI uses the native `POST /labs/{id}/nodes/{id}/cli` API (no pyATS extra, no `virl2_client`, no device SSH credentials).

**Optional Enhancements:**

- **Node.js 18 or later** - Only needed for [HTTP Transport mode](#http-transport) with shared deployments

Windows, macOS, and Linux all use the same `uvx cml-mcp` config. CLI commands go through the CML REST API, so WSL is not required.

## Standard I/O (stdio) Transport

This is the standard way to connect your AI assistant (like Claude Desktop) directly to the CML MCP server. Think of it like a direct phone line between the AI and your CML server.

**When to use this:** For personal use on your own computer, or when your AI client is on the same machine where you want to run the server.

### Using uvx (Easiest)

**What is uvx?** It's a tool that automatically downloads and runs Python packages without manual installation steps. Perfect for getting started quickly!

> [!NOTE]
> **About command paths in MCP configs:** MCP clients like Claude Desktop and Cursor launch tools in a restricted environment that does not always inherit your terminal's `PATH`. If you get a "command not found" error for `uvx`, `uv`, `npx`, or another command, replace the command name in the `"command"` field with its full path. To find the full path:
>
> - **macOS/Linux:** Run `which uvx` (or `which uv`, `which npx`) in your terminal
> - **Windows (Command Prompt):** Run `where uvx`
> - **Windows (PowerShell):** Run `(Get-Command uvx).Source`
>
> For example, replace `"uvx"` with `"/Users/alice/.local/bin/uvx"` on macOS, or `"C:\Users\alice\.local\bin\uvx.exe"` on Windows.

#### uvx configuration

This configuration works on Linux, macOS, and Windows. You can create labs, add devices, set startup config, and run CLI on BOOTED nodes via the controller's native `/cli` API.

**Step 1:** Locate your MCP client's configuration file (e.g., for Claude Desktop, it's `claude_desktop_config.json`)

**Step 2:** Add this configuration:

```json
{
  "mcpServers": {
    "Cisco Modeling Labs CML": {
      "command": "uvx",
      "args": [
        "cml-mcp"
      ],
      "env": {
        "CML_URL": "<URL_OF_CML_SERVER>",
        "CML_USERNAME": "<USERNAME_ON_CML_SERVER>",
        "CML_PASSWORD": "<PASSWORD_ON_CML_SERVER>",
        "CML_VERIFY_SSL": "false",
        "DEBUG": "false"
      }
    }
  }
}
```

**Remember to customize:**

- Replace `<URL_OF_CML_SERVER>` with your actual CML server URL (e.g., `https://cml.mylab.com` or `https://10.10.20.50`)
- Use your actual CML username and password
- Set `DEBUG` to `"true"` if you need troubleshooting information (keeps it in logs)

**Restart required:** After saving the configuration file, restart your MCP client for changes to take effect.

#### Docker

For any platform using Docker:

```json
{
  "mcpServers": {
    "Cisco Modeling Labs CML": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--pull",
        "always",
        "-e",
        "CML_URL",
        "-e",
        "CML_USERNAME",
        "-e",
        "CML_PASSWORD",
        "-e",
        "CML_VERIFY_SSL",
        "-e",
        "DEBUG",
        "xorrkaz/cml-mcp:latest"
      ],
      "env": {
        "CML_URL": "<URL_OF_CML_SERVER>",
        "CML_USERNAME": "<USERNAME_ON_CML_SERVER>",
        "CML_PASSWORD": "<PASSWORD_ON_CML_SERVER>",
        "CML_VERIFY_SSL": "false",
        "DEBUG": "false"
      }
    }
  }
}
```

### Using FastMCP CLI

An alternative is to use FastMCP CLI to install the server into your favorite client. FastMCP CLI supports Claude Desktop, Claude Code, Cursor, and manual JSON generation.

1. Clone this repository:

    ```sh
    git clone https://github.com/xorrkaz/cml-mcp.git
    cd cml-mcp
    ```

2. Run `uv sync` to install all the correct dependencies, including FastMCP 3.x.

3. Create a `.env` file with the following variables set:

    ```sh
    CML_URL=<URL_OF_CML_SERVER>
    CML_USERNAME=<USERNAME_ON_CML_SERVER>
    CML_PASSWORD=<PASSWORD_ON_CML_SERVER>
    CML_VERIFY_SSL=false  # Default is true; CML's self-signed cert requires false
    DEBUG=false  # Set to true to enable debug logging
    ```

4. Run the FastMCP CLI command to install the server. For example:

    ```sh
    fastmcp install claude-desktop src/cml_mcp/server.py:server_mcp --project `realpath .` --env-file .env
    ```

## HTTP Transport

HTTP transport mode runs the MCP server as a standalone web service that multiple people can connect to. Instead of each person running their own server, you run one central server that everyone shares.

**When to use HTTP mode:**

- ✅ You want to run the server on a dedicated machine (not your laptop)
- ✅ Multiple team members need to connect to the same CML environment
- ✅ You want to deploy in a containerized environment (Kubernetes, Docker Swarm)
- ✅ You need centralized control and logging
- ✅ You want to implement access control lists (who can delete labs, create users, etc.)

**When to use stdio mode instead:**

- ✅ You're the only user
- ✅ You want the simplest setup
- ✅ You're just trying out the tool

### Recommended deployment: behind a TLS-terminating reverse proxy

The MCP server itself only speaks plain HTTP and binds to `127.0.0.1` by default. **Always
put a TLS-terminating reverse proxy in front of it** rather than exposing the server directly,
and rather than disabling the loopback-only default. A minimal Caddy example:

```
# Caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:9000
}
```

Caddy automatically obtains and renews a certificate via Let's Encrypt. An equivalent nginx
config:

```nginx
server {
    listen 443 ssl;
    server_name mcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

With this layout, `cml-mcp` keeps its default `CML_MCP_BIND=127.0.0.1` — it never accepts a
connection that didn't come through the proxy on the same host. You should **not** need to set
`CML_MCP_INSECURE` or bind to `0.0.0.0` for this deployment model. Only set `CML_MCP_BIND` to a
non-loopback address (and the required `CML_MCP_INSECURE=true`) if the proxy runs on a different
host and you understand the exposure that implies — see [Network exposure](#network-exposure)
below.

### Running the HTTP Server

**Quick start:** To run the server in HTTP mode, you'll set some environment variables and then start the server with `uvicorn` (a Python web server).

#### Step 1: Install the package

```sh
uv venv
source .venv/bin/activate
uv pip install cml-mcp
```

Or for development, clone the repository and sync dependencies:

```sh
git clone https://github.com/xorrkaz/cml-mcp.git
cd cml-mcp
uv sync
```

#### Step 2: Set environment variables

You can either export these directly in your shell or create a `.env` file (recommended for persistence):

```sh
# Set environment variables
export CML_URL=<URL_OF_CML_SERVER>
export CML_MCP_TRANSPORT=http
export CML_MCP_BIND=127.0.0.1  # Default; loopback-only unless CML_MCP_INSECURE=true (see below)
export CML_MCP_PORT=9000       # Optional, defaults to 9000

# Run the server with uvicorn
uvicorn cml_mcp.server:app --host 127.0.0.1 --port 9000 --workers 1
```

Or create a `.env` file with these settings:

```sh
CML_URL=<URL_OF_CML_SERVER>  # Optional in HTTP mode if using X-CML-Server-URL header
CML_MCP_TRANSPORT=http
CML_MCP_BIND=127.0.0.1  # Default; loopback-only. See "Network exposure" below to change.
CML_MCP_PORT=9000
CML_VERIFY_SSL=false  # Default is true; CML's self-signed cert requires false
DEBUG=false  # Set to true to enable debug logging
# For multiple CML hosts support, use one of:
CML_ALLOWED_URLS=https://cml1.example.com,https://cml2.example.com  # Comma-separated list
# OR
CML_URL_PATTERN=^https://cml\.example\.com:443$  # Regex pattern (must fully match, including the port; the "$" anchor is required)
# Optional: Enable access control lists for tool restrictions
CML_MCP_ACL_FILE=/path/to/acl.yaml  # Path to ACL configuration file
# Optional: Session cache idle TTL in seconds (default: 3600). Authenticated CML sessions
# are cached and reused across requests. The TTL is an idle timer — it resets on every
# request, so active sessions stay alive indefinitely. Sessions are automatically closed
# when they expire. If a cached session's CML token expires mid-use, the server will
# re-authenticate transparently. Lower this value to reclaim resources sooner after
# users become inactive, or to force re-authentication after credential rotation.
CML_SESSION_TTL=3600
```

Then run:

```sh
# Activate the virtual environment if not already active
source .venv/bin/activate

# Run the server
cml-mcp
```

By default the server binds only to `127.0.0.1:9000` — it is not reachable from other hosts.
For production or shared deployments, place a TLS-terminating reverse proxy (nginx, Caddy, etc.)
on the same host in front of it, as shown above, rather than changing the bind address.

### Network exposure

`CML_MCP_BIND` defaults to `127.0.0.1` (loopback-only). If you set it to any non-loopback
address (e.g. `0.0.0.0` or a specific interface IP) so the server accepts connections directly
from other hosts, you must also set `CML_MCP_INSECURE=true` — otherwise the server refuses to
start. This is a deliberate speed bump: binding non-loopback means the raw, unauthenticated-at-
the-TCP-layer HTTP listener is reachable from other machines, and you are responsible for
whatever network controls (firewall rules, a reverse proxy with TLS, VPN-only routing, etc.)
make that safe. Prefer the reverse-proxy-on-loopback layout above instead of setting this.

Related hardening knobs, all optional:

- `CML_MCP_ALLOWED_HOSTS` — comma-separated list of `Host`/`Origin` values the server will
  accept, to defend against DNS-rebinding attacks. Strongly recommended whenever the server is
  reachable from a browser-capable client.
- `CML_MCP_RATE_LIMIT_MAX_ATTEMPTS` / `CML_MCP_RATE_LIMIT_WINDOW` — per-IP and per-username
  sliding-window rate limits on the auth path (default: a small number of attempts per window).
- `CML_MCP_ALLOW_ANON_DISCOVERY` — by default, `tools/list` requires authentication like any
  other MCP call. Set this to `true` only if an unauthenticated client (e.g. a skills registry
  crawler) needs to enumerate tool names without credentials.
- `CML_API_TIMEOUT` / `CML_API_CONNECT_TIMEOUT` — configurable connect/read/write/pool timeouts
  for outbound requests to the CML controller (defaults are reasonable for most deployments).
- `CML_PCAP_MAX_SIZE_BYTES` — caps the size of packet captures `get_packet_capture_data` will
  return (default 64 MiB), to avoid a single large capture exhausting memory.



### Authentication in HTTP Mode

**Important security note:** In HTTP mode, credentials work differently than stdio mode to keep your passwords secure.

**How it works:**

- Instead of storing CML passwords in environment variables (where they could be visible), each client sends their credentials securely with each request using HTTP headers
- This means different users can connect to the same server with their own CML credentials

**What you need to know:**

- **CML Credentials**: Instead of being set via environment variables (`CML_USERNAME`/`CML_PASSWORD`), CML credentials are provided via the `X-Authorization` HTTP header using either Basic authentication format (`Basic <base64_username:password>`) or a long-lived CML API token as a Bearer token (`Bearer <cml_jwt>`).
- **Optional fallback credentials (opt-in)**: By default, HTTP requests with no `X-Authorization` header are rejected. To allow such requests to fall back to a server-configured identity, set `CML_MCP_ALLOW_UNAUTHENTICATED=true` **and** set either `CML_USERNAME`/`CML_PASSWORD` or `CML_JWT`. The fallback applies **only** to requests that use the statically configured `CML_URL` — a request that supplies its own `X-CML-Server-URL` is always required to authenticate, so the configured credentials can never be forwarded to a client-chosen (potentially attacker-controlled) server. **Security warning:** with the fallback enabled, any client that can reach the HTTP port can act as the configured identity without authenticating. Only enable this for trusted single-tenant deployments (e.g. a personal lab). The server logs a warning at startup whenever the fallback is active.
- **Multiple CML Hosts**: When running in HTTP mode, clients can connect to different CML servers by providing the `X-CML-Server-URL` header. For security, you must configure allowed URLs via the `CML_ALLOWED_URLS` environment variable (comma-separated list) or `CML_URL_PATTERN` (regex pattern). Matching compares only the **scheme, host, and port** of the requested URL — any userinfo (e.g. `https://trusted.example.com@evil.example.com`), path, or query is ignored, so a credentials-style prefix cannot be used to spoof an allowed host. `CML_URL_PATTERN` is matched with `re.fullmatch` against the whole canonical `scheme://host:port` origin, where the port is always filled in with the scheme default (80/443) when the client's URL omits it — **the pattern must therefore include the port and always be anchored with both `^` and a trailing `$`**, e.g. `^https://cml\.example\.com:443$`, otherwise it will either never match or match more hosts than intended.
- **SSL verification**: The `X-CML-Verify-SSL` header (`true`/`false`) is honored **only** when the client supplies its own CML server via `X-CML-Server-URL`. Requests that fall back to the statically configured `CML_URL` always use the server's `CML_VERIFY_SSL` setting and cannot downgrade SSL verification via the header.
- **Unauthenticated tool discovery**: MCP protocol initialization (`initialize`) and tool listing (`tools/list`) do **not** require credentials. This allows AI clients such as Cisco AI Canvas to discover available tools before the user has supplied CML credentials. Actual tool calls always require authentication.

Example headers:

```http
X-Authorization: Basic <base64_encoded_cml_username:cml_password>
```

Or, using a long-lived CML API token instead of username/password:

```http
X-Authorization: Bearer <cml_jwt>
```

```http
X-CML-Server-URL: https://cml-server.example.com
X-CML-Verify-SSL: false
```

### Configuring MCP Clients

**The challenge:** Most AI clients (like Claude Desktop) expect a direct connection to the MCP server, but your server is now running as a web service.

**The solution:** Use `mcp-remote`, a small bridge program that connects your AI client to the HTTP server. It translates between the client's expected format and HTTP.

**What you need:** Node.js **18 or later** installed on your computer (includes the `npx` command that runs `mcp-remote`)

- [Download Node.js](https://nodejs.org/en/download/) if you don't have it (version 18+ required)

**Step 1:** Prepare your credentials (you'll need them Base64-encoded)

#### Encoding Credentials

**Why Base64?** It's a standard way to safely transmit credentials in HTTP headers. Don't worry—it's easy to generate!

**Linux/Mac - Use the terminal:**

```sh
# For CML credentials (X-Authorization header)
echo -n "username:password" | base64
```

**Windows (use WSL):**

```sh
wsl bash -c 'echo -n "username:password" | base64'
```

**Windows (PowerShell):**

```powershell
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("username:password"))
```

**Step 2:** Configure your MCP client

Now that you have your Base64-encoded credentials, add this to your MCP client configuration (e.g., Claude Desktop's `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "Cisco Modeling Labs CML": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<server_host>/mcp",
        "--header",
        "X-Authorization:${CML_AUTH_HEADER}"
      ],
      "env": {
        "CML_AUTH_HEADER": "Basic <base64_encoded_cml_credentials>"
      }
    }
  }
}
```

**Why use env vars for the header values?** There is a known bug in Cursor and Claude Desktop on Windows where spaces inside `args` entries are not escaped correctly, silently mangling header values. Putting the credential string in an `env` var (where spaces are safe) and referencing it with no space around the `:` in `args` avoids this on all platforms.

**Customize your configuration:**

- `<server_host>`: The hostname or HTTPS address of your reverse proxy (e.g., `cml-mcp.mycompany.com`)
- `<base64_encoded_cml_credentials>`: Paste the Base64 string you generated for your CML username:password

> [!TIP]
> **`npx` not found?** `npx` is included with Node.js but may not be on the PATH seen by your MCP client. Run `which npx` (macOS/Linux) or `where npx` (Windows) in a terminal to get the full path, then replace `"npx"` in the `"command"` field with that value (e.g., `"/usr/local/bin/npx"`).

#### HTTPS with Self-Signed Certificates

If your reverse proxy uses a self-signed certificate, add `NODE_TLS_REJECT_UNAUTHORIZED` to disable Node.js TLS validation:

```json
{
  "mcpServers": {
    "Cisco Modeling Labs CML": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<server_host>/mcp",
        "--header",
        "X-Authorization:${CML_AUTH_HEADER}"
      ],
      "env": {
        "CML_AUTH_HEADER": "Basic <base64_encoded_cml_credentials>",
        "NODE_TLS_REJECT_UNAUTHORIZED": "0"
      }
    }
  }
}
```

### Docker with HTTP Transport

You can also run the server in HTTP mode using Docker. Inside the container, the server must
bind to `0.0.0.0` (not the `127.0.0.1` default) so Docker's port-publishing (`-p`) can reach it
— the container's network namespace already isolates it from the host and other containers, so
this is the expected way to use `CML_MCP_INSECURE` in this deployment. Prefer publishing only to
a loopback host port (`-p 127.0.0.1:9000:9000`) and terminating TLS with a reverse proxy on the
host, rather than publishing directly to `0.0.0.0:9000` on the host:

```sh
docker run -d \
  --rm \
  --name cml-mcp \
  -p 127.0.0.1:9000:9000 \
  -e CML_URL=<URL_OF_CML_SERVER> \
  -e CML_MCP_TRANSPORT=http \
  -e CML_MCP_BIND=0.0.0.0 \
  -e CML_MCP_INSECURE=true \
  xorrkaz/cml-mcp:latest
```

This exposes the HTTP server on `127.0.0.1:9000` on the host only; put a TLS-terminating reverse
proxy in front of that (as in [Recommended deployment](#recommended-deployment-behind-a-tls-terminating-reverse-proxy))
before allowing external MCP clients to connect.

#### Using ACLs with Docker

Mount your ACL file to a path inside the container (`/app/acl.yaml` is a convenient
convention) and set `CML_MCP_ACL_FILE` to that path explicitly. The file must be owned by
the container's runtime uid (1000) and not group/other-writable, or the server will refuse
to load it and fail closed (deny all tools) -- so `chown 1000:1000` (and `chmod go-w`) it on
the host before mounting:

```sh
chown 1000:1000 /path/to/your/acl.yaml
chmod go-w /path/to/your/acl.yaml

docker run -d \
  --rm \
  --name cml-mcp \
  -p 127.0.0.1:9000:9000 \
  -v /path/to/your/acl.yaml:/app/acl.yaml:ro \
  -e CML_URL=<URL_OF_CML_SERVER> \
  -e CML_MCP_TRANSPORT=http \
  -e CML_MCP_BIND=0.0.0.0 \
  -e CML_MCP_INSECURE=true \
  -e CML_MCP_ACL_FILE=/app/acl.yaml \
  xorrkaz/cml-mcp:latest
```

`CML_MCP_ACL_FILE` has no built-in default -- if you don't set it, ACLs are simply not
enforced (all tools allowed, same as running without Docker). Only set it once you actually
have an ACL file mounted at that path; setting it without mounting a real file makes the
server fail closed and deny every tool.

## Access Control Lists (HTTP Mode Only)

**What are ACLs and why would I use them?**

Access Control Lists let you control who can do what in your CML environment when running in HTTP mode. Think of it like permissions in a file system—you can decide which team members can delete labs, create users, or just view information.

**Real-world scenarios:**

- 🎓 **Training environment:** Students can create and manage their own labs, but can't delete other users' labs or modify system settings
- 👥 **Team environment:** Junior engineers can view and work with labs, but only senior staff can delete resources
- 🔒 **Controlled access:** Contractors can execute show commands but can't modify configurations or delete anything

**Important:** ACLs only work in HTTP transport mode. If you're using stdio mode (direct connection), everyone has full access.

### ACL Configuration

To enable ACLs, set the `CML_MCP_ACL_FILE` environment variable to point to your ACL YAML file:

```sh
export CML_MCP_ACL_FILE=/path/to/acl.yaml
```

Or add it to your `.env` file:

```sh
CML_MCP_ACL_FILE=/path/to/acl.yaml
```

### ACL File Format

The ACL file uses the following structure:

```yaml
---
# If a user is not explicitly mentioned in the file, they are denied tool use when
# default_enabled is False. By default (when default_enabled is True or omitted),
# users not explicitly named in the file are allowed to call all tools.
default_enabled: False
users:
    # Admin is allowed to call all tools.
    admin: {}
    # jdoe is allowed all tools but delete_cml_lab and delete_cml_node.
    jdoe:
        disabled_tools:
            - delete_cml_lab
            - delete_cml_node
    # jsmith is only allowed to call send_cli_command.
    jsmith:
        enabled_tools:
            - send_cli_command
```

### Configuration Options

- **`default_enabled`** (boolean, default: `true`): Controls the default behavior for users not explicitly listed in the ACL file.
  - `true`: Users not in the ACL file can access all tools (permissive default)
  - `false`: Users not in the ACL file cannot access any tools (restrictive default)

- **`users`** (object): A mapping of CML usernames to their tool access rules. Each user can have:
  - **`enabled_tools`** (list): An allow list of tool names the user can access. If specified, the user can only access these tools.
  - **`disabled_tools`** (list): A block list of tool names the user cannot access. The user can access all other tools.
  - If both lists are specified, `enabled_tools` takes precedence
  - An empty user configuration (`{}`) allows access to all tools regardless of `default_enabled`

### ACL Behavior

The ACL system follows this evaluation order:

1. If no ACL file is configured, all users can access all tools
2. If a user has an `enabled_tools` list, they can only access those specific tools
3. If a user has a `disabled_tools` list (and no `enabled_tools`), they can access all tools except those listed
4. If a user is not in the ACL file, the `default_enabled` setting determines their access
5. If a user is in the ACL file with an empty configuration, they can access all tools

### Example Use Cases

**Restrictive Environment** - Only allow specific users:

```yaml
default_enabled: False
users:
    admin: {}  # Admin has full access
    developer:
        enabled_tools:
            - get_cml_labs
            - get_nodes_for_cml_lab
            - send_cli_command
```

**Permissive Environment** - Block specific actions:

```yaml
default_enabled: True
users:
    intern:
        disabled_tools:
            - delete_cml_lab
            - delete_cml_node
            - delete_cml_user
            - delete_cml_group
```

For a complete example, see [acl.yaml.example](https://github.com/xorrkaz/cml-mcp/blob/main/acl.yaml.example) in the repository.

### Tool Names Reference

To configure ACLs, you'll need to know the exact tool names. Here are all available tools:

**Lab Management:** `get_cml_labs`, `create_empty_lab`, `create_full_lab_topology`, `modify_cml_lab`, `set_cml_lab_permissions`, `start_cml_lab`, `stop_cml_lab`, `wipe_cml_lab`, `delete_cml_lab`, `get_cml_lab_by_title`, `download_lab_topology`, `clone_cml_lab`

**Node Management:** `get_cml_node_definitions`, `get_node_definition_detail`, `add_node_to_cml_lab`, `get_nodes_for_cml_lab`, `configure_cml_node`, `start_cml_node`, `stop_cml_node`, `wipe_cml_node`, `delete_cml_node`, `get_console_log`, `send_cli_command`

**Interface & Link Management:** `add_interface_to_node`, `get_interfaces_for_node`, `connect_two_nodes`, `get_all_links_for_lab`, `apply_link_conditioning`, `start_cml_link`, `stop_cml_link`

**Annotations:** `get_annotations_for_cml_lab`, `add_text_annotation`, `add_rectangle_annotation`, `add_ellipse_annotation`, `add_line_annotation`, `delete_annotation_from_lab`

**Packet Capture:** `start_packet_capture`, `stop_packet_capture`, `check_packet_capture_status`, `get_captured_packet_overview`, `get_packet_capture_data`

**User & Group Management:** `get_cml_users`, `create_cml_user`, `delete_cml_user`, `get_cml_groups`, `create_cml_group`, `delete_cml_group`

**System Information:** `get_cml_information`, `get_cml_status`, `get_cml_statistics`, `get_cml_licensing_details`

## Troubleshooting mcp-remote

### Diagnosing connection problems

Add `--debug` to the `args` list to write a detailed connection log to `~/.mcp-auth/<server_hash>_debug.log`:

```json
"args": ["-y", "mcp-remote", "https://<server_host>/mcp", "--debug", ...]
```

### Headers are being mangled

If credentials appear corrupted, you are likely hitting the Cursor / Windows Claude Desktop spaces-in-args bug. Use the env var pattern shown in [Configuring MCP Clients](#configuring-mcp-clients) (no space around `:` in the arg, value in `env`).

### Check your Node.js version

`mcp-remote` requires Node.js 18 or later. Run `node --version` to check. Claude Desktop uses your system Node, even if a newer version is installed via a version manager.

## Environment Variables Reference

### Required (stdio mode)

- `CML_URL` - URL of your CML server (e.g., `https://cml.example.com`)
- `CML_USERNAME` - Username for CML authentication
- `CML_PASSWORD` - Password for CML authentication

  > **HTTP mode:** `CML_USERNAME` and `CML_PASSWORD` are *optional* and act as fallback credentials when an incoming request omits the `X-Authorization` header (and only when `CML_MCP_ALLOW_UNAUTHENTICATED=true`). The fallback applies **only** to the statically configured `CML_URL`; a request that supplies its own `X-CML-Server-URL` is never given these credentials, so the configured identity cannot be exfiltrated to a client-chosen server. Leave them unset unless you intentionally want any unauthenticated client to assume those credentials.

- `CML_JWT` - Long-lived CML API token (personal access token), used **instead of** `CML_USERNAME`/`CML_PASSWORD`. Mutually exclusive with them: in stdio mode the server refuses to start if both are set, or if neither is set. Obtain a token via CML's Settings > API Tokens (or the `POST /api/v0/access_tokens` API), then set `CML_JWT` to the raw token value. Recommended for long-running agent sessions since it survives MCP server restarts without requiring a password. If the token expires or is revoked, tool calls fail with a clear error instructing you to obtain a new token; you can then either call the `set_cml_jwt` MCP tool to update the running session in place (no restart required), or set a new `CML_JWT` and restart the server.

  > **HTTP mode:** `CML_JWT` is *optional* and, like `CML_USERNAME`/`CML_PASSWORD`, only used as fallback-identity credentials when `CML_MCP_ALLOW_UNAUTHENTICATED=true`. Per-request token auth is provided via `X-Authorization: Bearer <token>` instead (see [HTTP Transport Mode](#http-transport-mode) above).


### Optional

- `CML_VERIFY_SSL` - Verify SSL certificates (default: `true`). CML ships with a self-signed certificate, so most users must set this to `false` (or install a CA-signed certificate / point `CA_BUNDLE` at the self-signed cert).
- `DEBUG` - Enable debug logging (default: `false`)

### HTTP Transport Mode

- `CML_MCP_TRANSPORT` - Set to `http` for HTTP mode (default: `stdio`)
- `CML_MCP_BIND` - IP address to bind HTTP server (default: `127.0.0.1`, loopback-only). Setting this to any non-loopback address requires `CML_MCP_INSECURE=true` — see [Network exposure](#network-exposure).
- `CML_MCP_PORT` - Port for HTTP server (default: `9000`)
- `CML_MCP_INSECURE` - Required (`true`) alongside a non-loopback `CML_MCP_BIND`; the server refuses to start otherwise. Leave unset for the default loopback-only bind.
- `CML_MCP_ALLOWED_HOSTS` - Comma-separated list of acceptable `Host`/`Origin` header values, to defend against DNS-rebinding attacks. Recommended whenever the server is reachable from a browser-capable client.
- `CML_MCP_ALLOW_ANON_DISCOVERY` - Allow unauthenticated `tools/list` calls (default: `false`, i.e. discovery requires auth like any other tool call)
- `CML_MCP_RATE_LIMIT_MAX_ATTEMPTS` / `CML_MCP_RATE_LIMIT_WINDOW` - Per-IP and per-username sliding-window rate limit for the auth path: max attempts per window (seconds)
- `CML_API_TIMEOUT` / `CML_API_CONNECT_TIMEOUT` - Read/write/pool and connect timeouts (seconds) for outbound requests to the CML controller
- `CML_PCAP_MAX_SIZE_BYTES` - Maximum packet-capture size `get_packet_capture_data` will return (default: 64 MiB); larger captures are rejected with a clear error
- `CML_ALLOWED_URLS` - Comma-separated list of allowed CML URLs in HTTP mode (matched on scheme/host/port only)
- `CML_URL_PATTERN` - Regex pattern for allowed CML URLs, applied to a canonical `scheme://host:port` origin (alternative to `CML_ALLOWED_URLS`)
- `CML_MCP_ACL_FILE` - Path to YAML file for access control lists (tool restrictions per user)
- `CML_SESSION_TTL` - Idle time-to-live (in seconds) for cached CML sessions in HTTP mode (default: `3600`). The timer resets on every request — sessions only expire after this many seconds of inactivity. Expired sessions are closed automatically. If a cached session's CML token expires mid-use, the server re-authenticates transparently. Lower this value to reclaim resources sooner after users become inactive, or to force re-authentication after credential rotation.

### Test Environment Variables

- `USE_MOCKS` - Use mock data instead of live CML server (default: `true`)
