# CML Fork Maintenance

This repository (`tmikuska/cml-mcp`) is a fork of the upstream open-source
MCP server (`xorrkaz/cml-mcp`). It contains CML-internal additions that are
not suitable for the public repo.

## Remotes

| Name       | URL                                    | Purpose                      |
| ---------- | -------------------------------------- | ---------------------------- |
| `origin`   | `https://github.com/xorrkaz/cml-mcp`   | Upstream open-source repo    |
| `tmikuska` | `git@github.com:tmikuska/cml-mcp.git`  | CML fork (our working repo)  |

## Branch layout

| Branch          | Base          | Purpose                                                |
| --------------- | ------------- | ------------------------------------------------------ |
| `origin/main`   | —             | Upstream: bundled schemas, standalone users            |
| `tmikuska/main` | `origin/main` | CML release: import hook, internal additions          |

The fork is kept as a single squashed commit on top of `origin/main` so that
upstream rebases are linear and easy to audit. There is no permanent
`release/X.Y.Z` branch; per-release patches (e.g. import-name aliases for CML
versions where a helper has not yet been renamed in `simple_core`) are
cherry-picked onto an ephemeral release branch when needed and discarded
afterwards.

## What lives where

### `origin/main` (upstream, standalone)

- Bundled schema copies under `src/cml_mcp/cml/`
- Pre-existing back-compat for older CML server schemas:
  - Schema relaxations in `system.py`, `users.py`, `groups.py`, `links.py`
  - `field_validator` for `opt_in` boolean coercion
  - `exclude_defaults=True` in PATCH bodies in `tools/labs.py` and
    `tools/users_groups.py`

These stay as-is when rebased into the fork; they are harmless on newer CML
versions and protect older servers that still send the old shapes.

### `tmikuska/main` (CML internal)

Everything from `origin/main`, **plus**:

- **Import hook** in `__init__.py` (`_CMLSchemaFinder`) — redirects
  `cml_mcp.cml.*` imports to the real `simple_common` / `simple_webserver`
  packages installed in the CML environment. The bundled schema copies
  under `src/cml_mcp/cml/` are deleted on this branch.
- **`unicon_cli.py`** — CLI command execution via the internal `termws`
  binary and Unicon, used because PyATS is not available inside CML.
- **`cli.py` Unicon dispatch** — falls back to `unicon_send_cli_command_sync`
  when the PyATS testbed loader cannot be imported.
- **`allow_http=True`** in `cml_client.py` — MCP runs locally on the CML
  server and connects to `localhost`, so HTTP is acceptable.
- **Test infrastructure** — top-level `conftest.py`, `tests/test_cache.py`,
  `tests/test_cml_mcp_compat.py`, and updates to `tests/mocks/*.json` so
  fixtures match the strict runtime schemas pulled in via the import hook
  (e.g. drop deprecated `groups: []`, add required `autostart` /
  `node_staging`, switch `opt_in` to its enum string,
  `link_capture_key: null`).
- **`build-cml` Justfile target** — builds the wheel with
  `pip wheel --no-deps` for the CML CI pipeline.

## How the import hook works

On `tmikuska/main` the `src/cml_mcp/cml/` directory does **not** exist.
All schema imports of the form:

```python
from cml_mcp.cml.simple_webserver.schemas.X import Y
```

are intercepted by `_CMLSchemaFinder` (a `sys.meta_path` finder registered
in `__init__.py`) and transparently redirected to:

```python
from simple_webserver.schemas.X import Y
```

The hook resolves the real module via `importlib.import_module` and then
replaces the freshly-created stub in `sys.modules` with it, so dunders
(`__name__`, `__file__`, `__loader__`, `__spec__`, `__path__`) and identity
comparisons all reflect the real package.

The MCP server therefore uses the real CML schema packages installed in the
environment. No build-time `sed`, `rm`, or schema copying is required.

## Updating from upstream

```bash
git fetch origin
git log --oneline origin/main..tmikuska/main   # fork-only commit(s)
git log --oneline tmikuska/main..origin/main   # upstream-only commits
```

Rebase `tmikuska/main` onto the new upstream:

```bash
git checkout tmikuska/main
git rebase origin/main
```

Conflict hot-spots:

- `src/cml_mcp/__init__.py` — upstream may add new top-level imports above
  the import hook.
- `src/cml_mcp/cml/...` — these files only exist upstream; resolve by
  keeping them deleted on the fork.
- `tools/labs.py` and `tools/users_groups.py` — keep upstream's
  `exclude_defaults=True` / `model_dump(exclude_unset=True)` idioms; the
  fork must not strip them.
- `tests/mocks/*.json` — if upstream regenerates fixtures, re-apply the
  schema corrections listed above.

Run the test suite, then force-push with lease:

```bash
just test
git push --force-with-lease tmikuska main
```

After pushing, bump the submodule pointer in `simple/packaging/mcp_server`.

## Per-release alias patches

When a CML release (e.g. `release/v2.10.0`) imports a helper that has been
renamed on `simple_core`'s master, add a one-line alias commit on a
throwaway branch cherry-picked from `tmikuska/main`, push it as
`tmikuska/release/X.Y.Z` for the duration of that release line, and delete
the branch once the helper is backported (or once the release is retired).

Example for the `_remove_unicon_loggers` → `remove_unicon_loggers` rename:

```python
# src/cml_mcp/tools/unicon_cli.py
from simple_core.config_extraction.utils import (
    TERMWS_BINARY,
    _remove_unicon_loggers as remove_unicon_loggers,
)
```

## Schema changes in `simple_webserver`

Schemas originate in the `simple` repo
(`webserver/simple_webserver/schemas/`). On the fork there is **nothing to
do** when they change — the import hook resolves them at runtime against
whatever is installed in the environment. The only potential follow-up is
updating mock fixtures under `tests/mocks/` if a new required field is
introduced or an existing field is removed.

## CML CI build

The Jenkins pipeline builds the MCP server wheel with:

```bash
pip wheel --no-deps -w packaging/wheelhouse/ packaging/mcp_server/
```

No source patching is needed. The import hook handles schema resolution at
runtime.
