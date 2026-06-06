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
Middleware module for HTTP request handling and ACL management.
"""

import base64
import hashlib
import logging
import os
import re
import stat
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Sequence

import yaml
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import Tool
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from cml_mcp.cml_client import CMLClient
from cml_mcp.settings import settings
from cml_mcp.tools.cache import _redact_key

logger = logging.getLogger("cml-mcp.middleware")

# Adapter used to parse client-provided CML URLs into their scheme/host/port parts.
_url_adapter = TypeAdapter(AnyHttpUrl)


class _SlidingWindowRateLimiter:
    """
    Minimal in-memory sliding-window rate limiter, keyed by an arbitrary string (e.g. client
    IP or username). Not shared across worker processes; adequate for the single-worker
    deployment this server documents (see Justfile/entrypoint.sh --workers 1).
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        # Bounded-work housekeeping: once the number of tracked keys crosses this soft cap we
        # run a single full sweep dropping fully-expired buckets. This keeps _hits from growing
        # without bound under a stream of distinct one-shot keys (e.g. rotating source IPs on a
        # direct bind, or many usernames) that are never revisited to prune themselves.
        self._sweep_threshold = 1024

    def _sweep_expired(self, now: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] > self.window_seconds]
        for key in stale:
            del self._hits[key]

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if len(self._hits) >= self._sweep_threshold:
            self._sweep_expired(now)
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_attempts:
            return False
        hits.append(now)
        return True


# Per-IP and per-username rate limiters guarding the authentication path. Constructed once at
# import from the CML_MCP_RATE_LIMIT_* settings (read at process startup); a single-worker
# deployment (see Justfile/entrypoint.sh --workers 1) keeps this in-process state authoritative.
# Note: these are NOT rebuilt if settings change at runtime -- unit tests that need different
# limits construct their own _SlidingWindowRateLimiter instead of mutating settings.
_ip_rate_limiter = _SlidingWindowRateLimiter(settings.cml_mcp_rate_limit_max_attempts, settings.cml_mcp_rate_limit_window)
_user_rate_limiter = _SlidingWindowRateLimiter(settings.cml_mcp_rate_limit_max_attempts, settings.cml_mcp_rate_limit_window)


def _enforce_auth_rate_limit(client_ip: str, username: str | None = None, user_log_id: str = "-") -> None:
    """
    Charge a failed/new auth attempt against the per-IP (and, when a username is given, the
    per-username) sliding-window limiter, raising McpError if either budget is exhausted.
    Centralizes the identical reject used at every unauthenticated/failed-credential branch.
    """
    if not _ip_rate_limiter.allow(client_ip):
        logger.warning("Request rejected: rate limit exceeded for client IP %s", client_ip)
        raise McpError(ErrorData(message="Too many authentication attempts; try again later.", code=-31006))
    if username is not None and not _user_rate_limiter.allow(username):
        logger.warning("Request rejected: rate limit exceeded for user %s", user_log_id)
        raise McpError(ErrorData(message="Too many authentication attempts; try again later.", code=-31006))


# ACL data
acl_data: dict[str, Any] = {}
# Set to True when an ACL file was configured (CML_MCP_ACL_FILE) but could not be loaded safely
# (unsafe ownership/permissions, missing file, empty file, or a YAML parse error). This is
# distinct from "no ACL file configured" (acl_data stays empty and all tools are allowed): once an
# operator has opted into ACL enforcement, a broken/invalid file must fail closed (deny every
# tool) rather than silently falling back to "no ACLs".
acl_load_failed: bool = False


def _validate_acl_data(raw_acl_data: dict | None) -> dict | None:
    """
    Validate and normalize ACL configuration data.

    Args:
        raw_acl_data: Raw ACL data loaded from YAML file

    Returns:
        Validated/normalized ACL data or None if invalid
    """
    if not raw_acl_data:
        return None

    # Validate default_enabled. Unlike per-user tool-list validation below (which only skips
    # the affected user), a malformed top-level field indicates the whole file is not what the
    # operator intended -- silently substituting a default here would mean a typo'd config
    # (e.g. `default_enabled: "false"`, a YAML string, not a bool) silently becomes the
    # *opposite* of what was configured. Treat it as an invalid file (fail closed) instead.
    if "default_enabled" in raw_acl_data and not isinstance(raw_acl_data["default_enabled"], bool):
        logger.warning("Invalid default_enabled value in ACLs (must be a boolean); treating ACL file as invalid")
        return None
    default_enabled = raw_acl_data.get("default_enabled", True)

    # Validate users structure. Same reasoning as above: a malformed top-level `users` block
    # (e.g. a list instead of a mapping) means the file's rules can't be trusted, not "no
    # per-user rules configured".
    if "users" in raw_acl_data and not isinstance(raw_acl_data["users"], dict):
        logger.warning("Invalid users structure in ACLs (must be a mapping); treating ACL file as invalid")
        return None
    users = raw_acl_data.get("users", {})

    # Validate each user's tool lists
    validated_users = {}
    for username, user_config in users.items():
        if not isinstance(user_config, dict):
            logger.warning("Invalid configuration for user %s in ACLs; skipping user", username)
            continue

        enabled_tools = user_config.get("enabled_tools")
        disabled_tools = user_config.get("disabled_tools")

        # Validate tool lists if present
        if enabled_tools is not None and not isinstance(enabled_tools, list):
            logger.warning("Invalid enabled_tools for user %s in ACLs; skipping user", username)
            continue
        if disabled_tools is not None and not isinstance(disabled_tools, list):
            logger.warning("Invalid disabled_tools for user %s in ACLs; skipping user", username)
            continue

        validated_users[username] = {
            "enabled_tools": enabled_tools,
            "disabled_tools": disabled_tools,
        }

    return {
        "default_enabled": default_enabled,
        "users": validated_users,
    }


def _check_acl_file_permissions(aclf: Path) -> str | None:
    """
    Verify the ACL file is safe to trust: either owned by the running user, or owned by root
    (uid 0) -- the common case for a Docker bind mount, Kubernetes ConfigMap, or Secret, which
    are typically root-owned regardless of the container's runtime USER -- and in either case
    not writable by group or other. A root-owned, non-group/other-writable file could not have
    been tampered with by the (non-root) process reading it, so it is exactly as trustworthy as
    a self-owned file.

    Returns an error message if the file fails the check, or None if it is safe to read.
    """
    try:
        st = aclf.stat()
    except OSError as e:
        return f"could not stat ACL file: {e}"
    if st.st_uid not in (os.getuid(), 0):
        return f"ACL file {aclf} is not owned by the running user or root (uid={os.getuid()}, file uid={st.st_uid})"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"ACL file {aclf} is group- or other-writable (mode={oct(st.st_mode & 0o777)}); refusing to load"
    return None


def load_acl_data() -> None:
    """Load ACL configuration from file if configured."""
    global acl_load_failed
    if settings.cml_mcp_transport != "http" or not settings.cml_mcp_acl_file:
        return

    aclf = Path(settings.cml_mcp_acl_file)
    if not aclf.is_file():
        logger.critical(
            "CML_MCP_ACL_FILE=%s was configured but does not exist or is not a regular file. Failing closed:"
            " all tools will be denied until the file is fixed.",
            aclf,
        )
        acl_load_failed = True
        return

    perm_error = _check_acl_file_permissions(aclf)
    if perm_error:
        logger.critical("Refusing to load ACL file: %s. Failing closed: all tools will be denied.", perm_error)
        acl_load_failed = True
        return

    try:
        with aclf.open("r", encoding="utf-8") as f:
            raw_acl_data = yaml.safe_load(f)
    except Exception:
        logger.critical("Failed to parse ACL file %s. Failing closed: all tools will be denied.", aclf, exc_info=True)
        acl_load_failed = True
        return

    if not raw_acl_data:
        logger.critical(
            "ACL file %s is empty or contains no data. Failing closed: all tools will be denied.",
            aclf,
        )
        acl_load_failed = True
        return

    validated_data = _validate_acl_data(raw_acl_data)
    if not validated_data:
        logger.critical(
            "ACL file %s did not contain a valid configuration after validation. Failing closed:" " all tools will be denied.",
            aclf,
        )
        acl_load_failed = True
        return

    acl_data.update(validated_data)
    acl_load_failed = False


class CustomHttpRequestMiddleware(Middleware):
    """Custom middleware for HTTP request authentication and ACL enforcement."""

    @staticmethod
    def _validate_request_host(context: MiddlewareContext) -> None:
        """
        Validate the incoming Host/Origin headers against CML_MCP_ALLOWED_HOSTS to defend
        against DNS-rebinding attacks (a page in the victim's browser resolving an
        attacker-controlled DNS name to 127.0.0.1 and issuing same-origin requests against a
        locally bound MCP server). No-op when CML_MCP_ALLOWED_HOSTS is empty.
        """
        if not settings.cml_mcp_allowed_hosts:
            return
        try:
            request = get_http_request()
        except RuntimeError:
            return
        # Host/Origin hostnames are case-insensitive (RFC 3986/7230); comparing raw case would
        # reject legitimate requests whose Host header happens to differ only in case (e.g. a
        # proxy that canonicalizes hostnames differently), without adding any real protection --
        # an attacker gains nothing by *matching* the allow list's case exactly vs. not.
        allowed_hosts = {h.lower() for h in settings.cml_mcp_allowed_hosts}
        host = request.headers.get("host", "").lower()
        origin = request.headers.get("origin")
        if host not in allowed_hosts:
            logger.warning("Request rejected: Host header '%s' is not in CML_MCP_ALLOWED_HOSTS", host)
            raise McpError(ErrorData(message="Request rejected: untrusted Host header", code=-31005))
        if origin is not None:
            origin_host = origin.split("://", 1)[-1].lower()
            if origin_host not in allowed_hosts:
                logger.warning("Request rejected: Origin header '%s' is not in CML_MCP_ALLOWED_HOSTS", origin)
                raise McpError(ErrorData(message="Request rejected: untrusted Origin header", code=-31005))

    @staticmethod
    def _validate_url(url: AnyHttpUrl | str, allowed_urls: list[AnyHttpUrl], url_pattern: str | None) -> None:
        if not allowed_urls and not url_pattern:
            raise McpError(
                ErrorData(
                    message="At least one of CML_ALLOWED_URLS or CML_URL_PATTERN must be set when using HTTP transport to accept"
                    " client-provided CML server URLs",
                    code=-31003,
                )
            )

        # Parse with Pydantic's AnyHttpUrl so matching uses the scheme/host/port only.
        # AnyHttpUrl.host ignores any userinfo component, so a spoofed host in the
        # userinfo (e.g. https://good.server@bad.server, whose real host is bad.server)
        # cannot bypass the allow list or pattern. AnyHttpUrl.port is always populated
        # with the scheme default (80/443) when not explicitly specified.
        try:
            target = url if isinstance(url, AnyHttpUrl) else _url_adapter.validate_python(str(url))
        except ValidationError:
            raise McpError(
                ErrorData(
                    message=f"CML server URL '{url}' is malformed",
                    code=-31004,
                )
            )
        if allowed_urls:
            if not any((target.scheme, target.host, target.port) == (a.scheme, a.host, a.port) for a in allowed_urls):
                raise McpError(
                    ErrorData(
                        message=f"CML server URL '{url}' is not in the list of allowed URLs",
                        code=-31003,
                    )
                )
        if url_pattern:
            # Match against a canonical origin (no userinfo, path, or query).
            # re.fullmatch (not re.match) is required: re.match only anchors the
            # start of the string, so an operator pattern that omits a trailing
            # "$" (e.g. "^https://cml\\.example\\.com") would still match a
            # suffix-extended host such as "https://cml.example.com.attacker.tld:443"
            # or a userinfo-prefixed host, letting a remote caller redirect the
            # credential POST to an attacker-controlled origin.
            canonical = f"{target.scheme}://{target.host}:{target.port}"
            if not re.fullmatch(url_pattern, canonical):
                raise McpError(
                    ErrorData(
                        message=f"CML server URL '{url}' does not match the required pattern",
                        code=-31004,
                    )
                )

    @staticmethod
    async def check_tool_enabled(tool_name: str, client: CMLClient) -> bool:
        """
        Check if a tool is enabled based on ACL configuration.
        ACL data is pre-validated at startup, so this method can trust the structure.
        """
        if acl_load_failed:
            # An ACL file was configured but could not be loaded safely (see load_acl_data()).
            # Fail closed rather than silently falling back to "no ACLs configured".
            return False
        if not acl_data:
            return True  # No ACLs defined, all tools enabled

        default_enabled = acl_data["default_enabled"]
        users = acl_data["users"]

        if client.username in users:
            user_config = users[client.username]
            enabled_tools = user_config["enabled_tools"]
            disabled_tools = user_config["disabled_tools"]

            # Prefer allow list over block list.
            if enabled_tools is not None and tool_name in enabled_tools:
                return True
            if disabled_tools is not None and tool_name in disabled_tools:
                return False
            # Tool is in neither list
            if enabled_tools is not None:
                return False  # Not in allow list
            if disabled_tools is not None:
                return True  # Not in block list

        return default_enabled

    async def on_request(self, context: MiddlewareContext, call_next) -> Any:
        # Import here to avoid circular dependency
        from cml_mcp.tools.dependencies import _request_client, new_request_id, set_request_user_hash

        # Generate the audit-log correlation id as early as possible so every log line for
        # this request -- including rejections below -- can be tied together.
        new_request_id()
        set_request_user_hash("-")  # Reset; set once the caller is authenticated, below.
        _request_client.set(None)

        try:
            return await self._on_request_impl(context, call_next)
        finally:
            # Belt-and-suspenders reset: guarantees these context vars never leak into
            # whatever runs next on this task/context, even for early-reject paths (bad
            # Host, rate limit, malformed credentials) that never reach the nested
            # try/finally further down that guards the authenticated call_next() path.
            set_request_user_hash("-")
            _request_client.set(None)

    async def _on_request_impl(self, context: MiddlewareContext, call_next) -> Any:
        from cml_mcp.tools.dependencies import _request_client, cml_client_cache, set_request_user_hash

        CustomHttpRequestMiddleware._validate_request_host(context)

        try:
            client_ip = get_http_request().client.host  # type: ignore[union-attr]
        except (RuntimeError, AttributeError):
            client_ip = "unknown"

        headers = get_http_headers(
            include={
                "x-cml-server-url",
                "x-cml-verify-ssl",
                "x-authorization",
            }
        )
        cml_url = headers.get("x-cml-server-url")
        auth_header = headers.get("x-authorization")
        # Allow only the base MCP protocol handshake (initialize/ping/notifications) through
        # unauthenticated; actual capability discovery (tools/list) requires auth by default
        # since tool descriptions/schemas can reveal internal capabilities. Set
        # CML_MCP_ALLOW_ANON_DISCOVERY=true to let tools/list through unauthenticated as well
        # (e.g. for a skills registry that needs to enumerate tools with no credentials).
        _always_anon_methods = {"initialize", "notifications/initialized", "ping"}
        anon_discovery_allowed = context.method in _always_anon_methods or (
            context.method == "tools/list" and settings.cml_mcp_allow_anon_discovery
        )
        # The MCP protocol handshake (initialize/ping/notifications) never touches CML at all, so
        # it must be reachable unauthenticated regardless of whether a default CML_URL happens to
        # be configured server-side -- that configuration is irrelevant to whether the handshake
        # can proceed. Only fall through to the credential checks below when this request isn't
        # eligible for the anonymous-discovery bypass (or the caller supplied its own
        # X-CML-Server-URL/X-Authorization, in which case it's trying to authenticate and should
        # go through the normal path instead of being silently treated as anonymous).
        if anon_discovery_allowed and not cml_url and not auth_header:
            logger.debug("No CML credentials provided; allowing '%s' for MCP protocol discovery", context.method)
            _request_client.set(None)
            return await call_next(context)
        if not cml_url and not settings.cml_url and not auth_header:
            # No credentials supplied for a method that requires them: this is a genuine
            # unauthenticated-access attempt, so it counts against the per-IP rate limit.
            _enforce_auth_rate_limit(client_ip)
            logger.warning(
                "Request rejected: missing CML credentials for method '%s' (anonymous discovery disabled)",
                context.method,
            )
            raise McpError(
                ErrorData(
                    message="Unauthorized: CML credentials are required. Provide X-CML-Server-URL and" " X-Authorization headers.",
                    code=-31002,
                )
            )

        if not cml_url:
            if settings.cml_url:
                cml_url = str(settings.cml_url)
                client_provided_url = False
            else:
                logger.warning("Request rejected: missing X-CML-Server-URL header and no default CML_URL configured")
                raise McpError(
                    ErrorData(
                        message="Missing X-CML-Server-URL header and no default CML_URL configured",
                        code=-31002,
                    )
                )
        else:
            # Validate the server URL is allowed.
            CustomHttpRequestMiddleware._validate_url(cml_url, settings.cml_allowed_urls, settings.cml_url_pattern)
            client_provided_url = True
        # SSL verification can only be adjusted by clients that supply their own remote CML URL.
        # When falling back to the statically configured CML_URL, the server's CML_VERIFY_SSL
        # setting is authoritative and cannot be downgraded via the X-CML-Verify-SSL header.
        if client_provided_url:
            verify_ssl_header = headers.get("x-cml-verify-ssl", "").lower()
            verify_ssl = verify_ssl_header == "true"
        else:
            verify_ssl = settings.cml_verify_ssl

        if not auth_header or " " not in auth_header:
            # Fall back to default credentials from settings only when explicitly enabled via
            # CML_MCP_ALLOW_UNAUTHENTICATED. The fallback lets any client that can reach the
            # HTTP port act as the configured identity, so it is opt-in. It is further restricted
            # to the statically configured CML_URL: a request that supplies its own
            # X-CML-Server-URL must authenticate, so the configured credentials can never be
            # forwarded to (and harvested by) a client-chosen server, even an allow-listed one.
            if settings.cml_mcp_allow_unauthenticated and not client_provided_url and settings.cml_username and settings.cml_password:
                logger.debug("Using default CML credentials from settings (unauthenticated mode enabled)")
                username = settings.cml_username
                password = settings.cml_password
            else:
                # Missing/invalid credential format is a failed auth attempt: count it.
                _enforce_auth_rate_limit(client_ip)
                logger.warning("Request rejected: missing or invalid X-Authorization header")
                raise McpError(
                    ErrorData(
                        message="Unauthorized: Missing or invalid X-Authorization header",
                        code=-31002,
                    )
                )
        else:
            parts = auth_header.split(None, 1)
            if len(parts) != 2 or parts[0].lower() != "basic":
                _enforce_auth_rate_limit(client_ip)
                logger.warning("Request rejected: malformed X-Authorization header")
                raise McpError(
                    ErrorData(
                        message="Invalid X-Authorization header format. Expected 'Basic <credentials>'",
                        code=-31001,
                    )
                )
            try:
                decoded = base64.b64decode(parts[1]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                _enforce_auth_rate_limit(client_ip)
                logger.warning("Request rejected: failed to decode X-Authorization credentials")
                raise McpError(
                    ErrorData(
                        message="Failed to decode Basic authentication credentials",
                        code=-31002,
                    )
                )
        # Look for the user's client in the cache.
        # Hash the password so it never appears in log output or dict keys.
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        client_cache_key = f"{username}:{pwd_hash}:{cml_url}:{verify_ssl}"
        # Redacted identifier for logs: never emit the raw username or cache key (which embeds
        # the username), only a stable hash so operators can correlate log lines without PII.
        # Same hashing the cache uses for its own log lines, so identifiers line up across modules.
        log_identifier = _redact_key(client_cache_key)
        set_request_user_hash(log_identifier)
        request_client = await cml_client_cache.get(client_cache_key)
        if not request_client:
            # A cache miss means this request is a genuine new authentication attempt (about to
            # call CML's login endpoint) -- charge it against the per-IP and per-username
            # rate limits. Reusing an already-authenticated cached session (the common case for
            # ongoing tool calls) does NOT consume this budget, so normal usage after the first
            # request in a session is never throttled by these limiters.
            _enforce_auth_rate_limit(client_ip, username=username, user_log_id=log_identifier)
            # Create a new client for this request.
            request_client = CMLClient(cml_url, username, password, transport="http", verify_ssl=verify_ssl)
            try:
                await request_client.login()
            except Exception as e:
                logger.warning("Authentication failed for %s: %s", log_identifier, e)
                raise McpError(ErrorData(message=f"Unauthorized: {str(e)}", code=-31002))

            await cml_client_cache.set(client_cache_key, request_client)

        # Store the client in context variable for this request
        _request_client.set(request_client)
        try:
            result = await call_next(context)
            logger.debug("Request to %s completed successfully", cml_url)
            return result
        except Exception as request_error:
            # Log request processing errors for diagnostics
            logger.warning(
                "Request to %s failed: %s: %s",
                cml_url,
                type(request_error).__name__,
                request_error,
            )
            # If the client failed to re-authenticate mid-request, evict it from the
            # cache so the next request gets a fresh client rather than retrying a
            # broken entry on every call until the TTL expires.
            if request_client.needs_reauth:
                logger.debug(
                    "Evicting stale cache entry for %s after re-auth failure",
                    log_identifier,
                )
                await cml_client_cache.invalidate(client_cache_key)
            raise
        finally:
            # Clear the context vars so they don't leak into any subsequent work
            # on the same task.  Do NOT close the client here — it lives in the cache.
            _request_client.set(None)
            set_request_user_hash("-")

    async def on_list_tools(self, context: MiddlewareContext, call_next) -> Sequence[Tool]:
        # Import here to avoid circular dependency
        from cml_mcp.tools.dependencies import get_cml_client_dep

        result = await call_next(context)

        # If no client available (unauthenticated discovery), return all tools
        # so that skills registries can enumerate available capabilities -- unless the ACL
        # file failed to load, in which case the operator's intent was to fail closed and deny
        # everything, and that must also apply to anonymous capability enumeration, not just
        # authenticated tool calls.
        try:
            client = get_cml_client_dep()
        except RuntimeError:
            if acl_load_failed:
                logger.warning("ACL file failed to load; denying anonymous tools/list (failing closed)")
                return []
            logger.debug("No CML client available during tools/list; returning all tools without ACL filtering")
            return result

        filtered_tools = [tool for tool in result if await CustomHttpRequestMiddleware.check_tool_enabled(tool.name, client)]

        return filtered_tools

    # Argument keys, in priority order, whose value is logged as the "resource" in the audit
    # log line below. These are the common id-shaped kwargs used across tool modules (lab id,
    # node id, link id, user/group id, etc.) -- see the "Flat primitive arguments" convention in
    # AGENTS.md. Deliberately excludes anything that looks like a credential.
    # Child-resource id kwargs that must win over the ambient lab_id when a tool takes both
    # (e.g. wipe_cml_node(lab_id, node_id)); every other tool has a single *_id arg that the
    # generic fallback below already picks up, so only the disambiguating keys are listed here.
    _AUDIT_RESOURCE_ID_KEYS = (
        "node_id",
        "link_id",
        "annotation_id",
    )

    @staticmethod
    def _extract_resource_id(arguments: dict | None) -> str:
        """Best-effort extraction of a resource identifier from tool call arguments, for audit logging."""
        if not arguments:
            return "-"
        for key in CustomHttpRequestMiddleware._AUDIT_RESOURCE_ID_KEYS:
            value = arguments.get(key)
            if value:
                return str(value)
        for key, value in arguments.items():
            if key.endswith("_id") and value:
                return str(value)
        return "-"

    async def on_call_tool(self, context: MiddlewareContext, call_next) -> Any:
        # Import here to avoid circular dependency
        from cml_mcp.tools.dependencies import get_cml_client_dep, get_request_id, get_request_user_hash

        tool_name = context.message.name
        arguments = getattr(context.message, "arguments", None)
        resource_id = CustomHttpRequestMiddleware._extract_resource_id(arguments)
        request_id = get_request_id()

        def audit(outcome: str, **extra: Any) -> None:
            # Structured, single-line audit log per tool invocation. Deliberately logs only the
            # redacted user hash (never username/password), the tool name, a best-effort
            # resource id, and the outcome -- no request bodies or credentials.
            extra_str = " " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
            logger.info(
                "AUDIT request_id=%s user=%s tool=%s resource=%s outcome=%s%s",
                request_id,
                get_request_user_hash(),
                tool_name,
                resource_id,
                outcome,
                extra_str,
            )

        try:
            client = get_cml_client_dep()
        except RuntimeError:
            audit("denied", reason="unauthenticated")
            raise ToolError("CML credentials required. Provide X-CML-Server-URL and X-Authorization headers to call tools.")
        if not await CustomHttpRequestMiddleware.check_tool_enabled(tool_name, client):
            audit("denied", reason="acl")
            raise ToolError(f"Tool '{tool_name}' is disabled by server configuration")

        try:
            result = await call_next(context)
        except Exception:
            audit("failure")
            raise
        audit("success")
        return result
