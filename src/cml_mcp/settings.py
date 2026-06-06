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

import json
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Annotated

from pydantic import AnyHttpUrl, Field, IPvAnyAddress, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_comma_separated(value: object) -> object:
    """Allow list-typed settings to be supplied as a comma-separated env var string.

    pydantic-settings normally requires JSON-array syntax (e.g. '["a","b"]') for env vars
    backing list fields; a plain comma-separated string (the documented, human-friendly
    format) would otherwise crash the server at startup with a SettingsError. Fields using
    this validator are also annotated with NoDecode so pydantic-settings skips its own
    JSON-decode attempt and hands us the raw string, so we still support the original
    JSON-array syntax ourselves (some existing deployments/.env files may already use it) by
    trying json.loads() first and only falling back to a comma split when that fails. Lists/
    None pass through unchanged (e.g. values coming from Python code).
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded, list):
                    return decoded
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class TransportEnum(StrEnum):
    """Transport types supported by the MCP server."""

    HTTP = "http"
    STDIO = "stdio"


class Settings(BaseSettings):
    """Settings for the CML MCP server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cml_url: AnyHttpUrl | None = Field(default=None, description="URL of the Cisco Modeling Labs server")
    cml_username: str | None = Field(default=None, description="Username for CML server authentication")
    cml_password: str | None = Field(default=None, description="Password for CML server authentication")
    cml_jwt: str | None = Field(
        default=None,
        description=(
            "Long-lived CML API token (personal access token) used instead of CML_USERNAME/CML_PASSWORD. "
            "Mutually exclusive with CML_USERNAME/CML_PASSWORD. Can be replaced at runtime via the "
            "'set_cml_jwt' MCP tool without restarting the server."
        ),
    )
    cml_verify_ssl: bool = Field(
        default=True,
        description="Whether to verify the CML server's SSL certificate",
    )
    cml_mcp_transport: TransportEnum = Field(
        default=TransportEnum.STDIO,
        description="Transport type for the MCP server",
    )
    cml_mcp_bind: IPvAnyAddress = Field(
        default_factory=lambda: IPv4Address("127.0.0.1"),
        description=(
            "IP address to bind the MCP server when transport is HTTP. Defaults to the loopback"
            " address; binding to a non-loopback address requires CML_MCP_INSECURE=true."
        ),
    )
    cml_mcp_port: int = Field(
        default=9000,
        description="Port to bind the MCP server when transport is HTTP",
    )
    cml_mcp_insecure: bool = Field(
        default=False,
        description=(
            "Must be explicitly set to True to allow CML_MCP_BIND to be a non-loopback address."
            " Without it, the HTTP transport refuses to start with a public bind address, since"
            " the built-in auth/ACL layer is not a substitute for a properly secured network"
            " boundary (e.g. a TLS-terminating reverse proxy)."
        ),
    )
    cml_allowed_urls: Annotated[list[AnyHttpUrl], NoDecode] = Field(
        default_factory=list,
        description=(
            "Comma-separated list of allowed CML server URLs when transport is HTTP (e.g."
            " 'https://cml1.example.com,https://cml2.example.com'). Empty list allows any URL."
        ),
    )

    @field_validator("cml_allowed_urls", mode="before")
    @classmethod
    def _parse_cml_allowed_urls(cls, value: object) -> object:
        return _split_comma_separated(value)

    cml_url_pattern: str | None = Field(
        default=None,
        description=(
            "Regex pattern that the CML server URL must fully match (via re.fullmatch) against its canonical"
            " 'scheme://host:port' origin when transport is HTTP (e.g., '^https://cml\\.example\\.com:443$')."
            " The port is always filled in with the scheme default (80/443) when omitted by the client, so include"
            " it in the pattern. Always anchor with both '^' and a trailing '$'; an unanchored pattern can be"
            " satisfied by a suffix-extended or userinfo-prefixed host."
        ),
    )
    cml_mcp_acl_file: str | None = Field(
        default=None,
        description="Path to a YAML file specifying access control lists for various MCP capabilities (only used in HTTP transport mode).",
    )
    cml_mcp_allow_unauthenticated: bool = Field(
        default=False,
        description=(
            "HTTP transport only. When True, requests that omit X-Authorization fall back to CML_USERNAME/CML_PASSWORD"
            " from the server environment. The fallback applies only to the statically configured CML_URL; requests"
            " that supply their own X-CML-Server-URL never receive these credentials, so the configured identity"
            " cannot be exfiltrated to a client-chosen server. This lets any client that can reach the port act as"
            " that identity. Only enable for trusted single-tenant deployments."
        ),
    )
    cml_session_ttl: int = Field(
        default=3600,
        description="Idle time in seconds before a cached CML client session expires (only applicable in HTTP transport mode).",
    )
    cml_mcp_allow_anon_discovery: bool = Field(
        default=False,
        description=(
            "HTTP transport only. The base MCP protocol handshake (initialize/ping/notifications) is always"
            " reachable unauthenticated -- it never touches CML. When this is True, unauthenticated requests"
            " may additionally call tools/list, so skills registries can enumerate capabilities without"
            " credentials. Actual tool calls always require authentication regardless of this setting. Default"
            " False requires auth for tools/list."
        ),
    )
    cml_mcp_rate_limit_max_attempts: int = Field(
        default=10,
        description=(
            "Maximum authentication attempts allowed per IP address (and separately per username) within"
            " CML_MCP_RATE_LIMIT_WINDOW seconds."
        ),
    )
    cml_mcp_rate_limit_window: int = Field(
        default=60,
        description="Sliding window, in seconds, used for CML_MCP_RATE_LIMIT_MAX_ATTEMPTS auth rate limiting.",
    )
    cml_mcp_allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Comma-separated list of Host/Origin header values (host[:port]) accepted on incoming HTTP"
            " requests, used to defend against DNS-rebinding attacks. Empty list disables Host/Origin"
            " validation (not recommended for non-loopback binds)."
        ),
    )

    @field_validator("cml_mcp_allowed_hosts", mode="before")
    @classmethod
    def _parse_cml_mcp_allowed_hosts(cls, value: object) -> object:
        return _split_comma_separated(value)

    cml_api_timeout: float = Field(
        default=10.0,
        description="Default read/write/pool timeout, in seconds, for requests made to the CML REST API.",
    )
    cml_api_connect_timeout: float = Field(
        default=10.0,
        description="Connect timeout, in seconds, for requests made to the CML REST API.",
    )
    cml_pcap_max_size_bytes: int = Field(
        default=64 * 1024 * 1024,
        description="Maximum size, in bytes, of a PCAP file that get_packet_capture_data will download and return (default 64 MiB).",
    )


def validate_stdio_auth(cml_url: str | None, cml_username: str | None, cml_password: str | None, cml_jwt: str | None) -> None:
    """Validate the stdio-transport auth configuration, raising ValueError on any conflict.

    Extracted from the module-level startup check so the mutual-exclusion rules between
    CML_JWT and CML_USERNAME/CML_PASSWORD can be unit-tested without re-importing the module.
    """
    if not cml_url:
        raise ValueError("CML_URL must be set when using stdio transport")
    has_userpass = bool(cml_username or cml_password)
    has_jwt = bool(cml_jwt)
    if has_userpass and has_jwt:
        raise ValueError(
            "CML_JWT cannot be combined with CML_USERNAME/CML_PASSWORD. Configure exactly one authentication method for stdio transport."
        )
    if not has_userpass and not has_jwt:
        raise ValueError("Either CML_JWT, or both CML_USERNAME and CML_PASSWORD, must be set when using stdio transport")
    if has_userpass and not (cml_username and cml_password):
        raise ValueError("CML_USERNAME and CML_PASSWORD must both be set together when not using CML_JWT")


settings = Settings()
if settings.cml_mcp_transport == TransportEnum.HTTP and not settings.cml_mcp_insecure:
    _bind_addr = settings.cml_mcp_bind
    if not (getattr(_bind_addr, "is_loopback", False)):
        raise ValueError(
            "CML_MCP_BIND is set to a non-loopback address "
            f"({_bind_addr}) but CML_MCP_INSECURE is not true. Refusing to start an HTTP transport with a public"
            " bind unless CML_MCP_INSECURE=true is explicitly set."
        )
if settings.cml_mcp_transport == TransportEnum.STDIO:
    validate_stdio_auth(settings.cml_url, settings.cml_username, settings.cml_password, settings.cml_jwt)
elif settings.cml_mcp_transport == TransportEnum.HTTP:
    # Fail fast at startup rather than lazily on the first request that supplies an
    # X-CML-Server-URL header. Without at least one of these, the middleware has no
    # way to constrain which host a client-supplied URL can point to, which would let
    # a remote caller redirect credential POSTs to an attacker-controlled server.
    if not settings.cml_url and not settings.cml_allowed_urls and not settings.cml_url_pattern:
        raise ValueError("At least one of CML_URL, CML_ALLOWED_URLS, or CML_URL_PATTERN must be set when using HTTP transport")
