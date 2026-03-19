# CML Fork Maintenance

This repository (`tmikuska/cml-mcp`) is a fork of the upstream open-source
MCP server (`xorrkaz/cml-mcp`). It contains CML-internal additions that are
not suitable for the public repo.

## Remotes

| Name       | URL                                    | Purpose                      |
| ---------- | -------------------------------------- | ---------------------------- |
| `origin`   | `https://github.com/xorrkaz/cml-mcp`  | Upstream open-source repo    |
| `tmikuska` | `git@github.com:tmikuska/cml-mcp.git` | CML fork (our working repo)  |

## Branch layout

| Branch               | Tracks          | Purpose                                     |
| -------------------- | --------------- | ------------------------------------------- |
| `tmikuska/main`      | `origin/main`   | CML release branch (import hook, no schemas)|
| `cml-fork-changes`   | `tmikuska/main` | CML-specific additions on top of main       |

## What stays fork-only

The following changes exist only in `cml-fork-changes` and must not be pushed
upstream:

- **`unicon_cli.py`** — CLI command execution via the internal `termws` binary
  and Unicon, used when PyATS testbed loader is unavailable.
- **`cli.py` unicon fallback** — logic to detect `TERMWS_BINARY` and fall back
  to `unicon_send_cli_command_sync` when `PyatsTFLoader` is not available.
- **`cml_mcp_remote_server_url`** in `settings.py` — setting for CML
  integration tests that connect to a running MCP server instance.
- **Remote test fixture** in `conftest.py` — `custom_httpx_client_factory` and
  the conditional `main_mcp_client` override that uses
  `cml_mcp_remote_server_url`.
- **`allow_http=True`** in `cml_client.py` — enables HTTP connections for
  internal CML environments that do not use TLS.
- **`build-cml` Justfile target** — builds the wheel via `pip wheel --no-deps`
  for the CML CI pipeline.

## What lives on `tmikuska/main` (shared with upstream)

- **Import hook** in `__init__.py` (`_CMLSchemaFinder`) — redirects
  `cml_mcp.cml.*` imports to the real `simple_common` / `simple_webserver`
  packages at runtime. No bundled schema copies exist on this branch.

## How the import hook works

On `tmikuska/main` the `src/cml_mcp/cml/` directory does **not** exist.
All schema imports of the form:

```python
from cml_mcp.cml.simple_webserver.schemas.X import Y
```

are intercepted by `_CMLSchemaFinder` (a `sys.meta_path` finder registered in
`__init__.py`) and transparently redirected to:

```python
from simple_webserver.schemas.X import Y
```

This means the MCP server uses the real CML schema packages that are installed
in the environment. No build-time `sed`, `rm`, or schema copying is required.

On `origin/main` (upstream), bundled schema copies under `src/cml_mcp/cml/`
**do** exist and are used by standalone (non-CML) users. When the import hook
detects that the real packages are available, it prefers them; otherwise the
bundled copies serve as a fallback.

## Fetching upstream changes

```bash
git fetch origin
git log --oneline origin/main..tmikuska/main   # fork-only commits
git log --oneline tmikuska/main..origin/main    # upstream-only commits
```

Review upstream-only commits and merge into `tmikuska/main`:

```bash
git checkout main
git merge origin/main
```

Then rebase `cml-fork-changes`:

```bash
git checkout cml-fork-changes
git rebase main
```

Resolve any conflicts, then verify:

```bash
just test
```

## CML CI build

The Jenkins pipeline builds the MCP server wheel with:

```bash
pip wheel --no-deps -w packaging/wheelhouse/ packaging/mcp_server/
```

No source patching is needed. The import hook handles schema resolution at
runtime.
