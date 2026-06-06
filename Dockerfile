# Build stage
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE CISCO_LICENSE.md ./
COPY src/ ./src/
RUN uv sync --no-dev --locked

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

# Set Python unbuffered mode
ENV PYTHONUNBUFFERED=1

# CML_URL is intentionally NOT given a default here. A baked-in hostname like
# "cml.host.internal" is a name an attacker could squat on a customer LAN,
# silently redirecting credential POSTs to a host they control. Operators must
# supply CML_URL explicitly (e.g. via `-e CML_URL=...` or an env file).
ENV CML_MCP_TRANSPORT=stdio
ENV DEBUG=false
# CML_MCP_ACL_FILE is intentionally NOT given a default here. Baking in a path that doesn't
# exist unless mounted would make load_acl_data() fail closed (deny every tool) for any HTTP
# container that isn't using ACLs at all. Operators who want ACL enforcement must set
# CML_MCP_ACL_FILE explicitly (conventionally /app/acl.yaml) *and* bind-mount a real,
# correctly-owned file to that path -- see "Using ACLs with Docker" in INSTALLATION.md.

# Run as a dedicated, unprivileged, non-root user rather than the container
# default root, so a compromised process cannot write outside /app or bind
# privileged ports.
RUN groupadd --gid 1000 cml-mcp && \
    useradd --uid 1000 --gid cml-mcp --home-dir /app --shell /usr/sbin/nologin --no-create-home cml-mcp

WORKDIR /app
COPY --from=builder --chown=cml-mcp:cml-mcp /app/.venv /app/.venv
COPY --chown=cml-mcp:cml-mcp src/ ./src/
COPY --chown=cml-mcp:cml-mcp entrypoint.sh healthcheck.py ./
RUN chmod +x /app/entrypoint.sh

USER 1000:1000

ENV PATH="/app/.venv/bin:$PATH"

# healthcheck.py inspects CML_MCP_TRANSPORT: for stdio it verifies the cml-mcp process
# is running; for http it makes a local TCP connection to CML_MCP_BIND:CML_MCP_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]

ENTRYPOINT ["/app/entrypoint.sh"]
