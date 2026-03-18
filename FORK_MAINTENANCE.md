# CML Fork Maintenance

This repository (`tmikuska/cml-mcp`) is a fork of the upstream open-source
MCP server (`xorrkaz/cml-mcp`). It contains CML-internal additions that are
not suitable for the public repo.

## Remotes

| Name       | URL                                    | Purpose                      |
| ---------- | -------------------------------------- | ---------------------------- |
| `origin`   | `https://github.com/xorrkaz/cml-mcp`  | Upstream open-source repo    |
| `tmikuska` | `git@github.com:tmikuska/cml-mcp.git` | CML fork (our working repo)  |

## What stays fork-only

The following changes exist only in the CML fork and must not be pushed upstream:

- **API schema updates** (`src/cml_mcp/cml/`) synced from the CML webserver/simple_common
  source, including `wireless.py` and any fields not yet released publicly.
- **`ConnectionType` enum** in `cli.py` giving explicit control over pyATS vs Unicon
  connection selection.
- **Extended docstring hints** ("Input: ... Prefer a JSON object") in tool files that
  guide CML-internal LLM integrations.
- **`build-cml` Justfile target** for building the wheel inside the CML CI pipeline.
- **Import hook** in `__init__.py` (`_CMLSchemaFinder`) that redirects bundled schema
  imports to real CML packages at runtime, eliminating build-time source patching.
- **Test fixture adjustments** in `conftest.py` for CML-specific integration test
  patterns.

## How the import hook works

When `cml-mcp` is installed in a CML environment (where `simple_common` and
`simple_webserver` are available as real packages), the `_CMLSchemaFinder`
meta-path hook in `__init__.py` intercepts all imports of the form:

```
from cml_mcp.cml.simple_webserver.schemas.X import Y
```

and redirects them to:

```
from simple_webserver.schemas.X import Y
```

This means the bundled schema copies under `src/cml_mcp/cml/` are never loaded
inside CML. No build-time `sed` or `rm` patching is required.

In standalone mode (open-source users without CML installed), the hook is not
activated. The bundled schemas are used via the `sys.path` fallback, exactly as
upstream intended.

## Fetching upstream changes

```bash
git fetch origin
git log --oneline origin/main..tmikuska/main   # fork-only commits
git log --oneline tmikuska/main..origin/main    # upstream-only commits
```

Review upstream-only commits and cherry-pick (or merge) those that are relevant:

```bash
git cherry-pick <commit>   # for individual commits
# or
git merge origin/main      # to bring in all upstream changes at once
```

Resolve conflicts in schema files by preferring the fork's version (since it
tracks the real CML schemas), then verify with:

```bash
just test
```

## Syncing schemas from CML

When CML schemas change, update the bundled copies:

1. Copy updated schema files from the CML source tree into
   `src/cml_mcp/cml/simple_common/` and `src/cml_mcp/cml/simple_webserver/schemas/`.
2. Run `just test` to verify the mock tests pass with the new schemas.
3. Commit with a message like "Update schemas to CML X.Y".

## CML CI build

The Jenkins pipeline builds the MCP server wheel with:

```bash
pip wheel --no-deps -w packaging/wheelhouse/ packaging/mcp_server/
```

No source patching is needed. The import hook handles schema resolution at runtime.
