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

import base64
import json
import logging
import os
import re
from enum import StrEnum
from typing import Any

import httpx

API_TIMEOUT = 10  # seconds
MCP_CLIENT_IDENTIFIER = "CmlMCP"

# Leading X.Y.Z triple from controller version strings (same idea as
# virl2_client.utils.Version.parse_version_str).
_CONTROLLER_VERSION_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{1,2})")


class CMLVersion(StrEnum):
    """
    Known CML controller versions that gate optional mcp_server features,
    mirroring the ``CMLVersion`` enum in webserver/simple_webserver's
    api_versioning schemas (used there to annotate OpenAPI routes with
    ``x-cml-introduced``/``x-cml-changed``). Here each member instead acts as a
    minimum-version requirement that can be compared against the connected
    controller's parsed version, e.g. ``CMLVersion.V2_12.supported_by(version)``.
    """

    V2_11 = "2.11"
    V2_12 = "2.12"

    def as_tuple(self) -> tuple[int, int, int]:
        major, minor = (int(part) for part in self.value.split("."))
        return major, minor, 0

    def supported_by(self, connected_version: tuple[int, int, int] | None) -> bool:
        """Return True if connected_version satisfies this minimum version requirement."""
        return connected_version is not None and connected_version >= self.as_tuple()


# POST /labs/{lab_id}/nodes/{node_id}/cli was introduced in CML 2.11. Servers
# older than this do not have the endpoint at all, so CLI execution falls back
# to a direct pyATS/SSH connection via the optional cml-mcp[pyats] extra.
_MIN_NATIVE_CLI_VERSION = CMLVersion.V2_11

# CML_API_TOKEN / personal-access-token authentication was added in CML 2.12.
# Older controllers don't support it, so we fail fast with a clear message
# rather than a confusing auth error.
_MIN_API_TOKEN_VERSION = CMLVersion.V2_12


def _strip_controller_version(version: str) -> tuple[int, int, int] | None:
    """Normalize a controller version to (major, minor, patch).

    Strips build/local suffixes the same way as tests/integration/test_server.py
    (``split('-')[0].split('+')[0]``), then parses the leading X.Y.Z triple and
    ignores dev/rc suffixes such as ``dev0`` (as virl2_client Version does).
    """
    normalized = version.strip().lstrip("v").split("-")[0].split("+")[0]
    match = _CONTROLLER_VERSION_RE.match(normalized)
    if not match:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def _controller_supports_native_cli(version: str) -> bool:
    """Return True when the connected controller is on the 2.11+ API line."""
    return _MIN_NATIVE_CLI_VERSION.supported_by(_strip_controller_version(version))


# Set up logging for this module only
logger = logging.getLogger("cml-mcp.cml_client")
loglevel = logging.DEBUG if os.getenv("DEBUG", "false").lower() == "true" else logging.INFO
logger.setLevel(loglevel)
# Configure handler with format for this module only
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


class CMLTokenExpiredError(Exception):
    """Raised when API-token based authentication fails because the configured CML API
    token is invalid, expired, or has been revoked, and there are no username/password
    credentials to fall back on for automatic re-authentication.

    Tool modules already wrap client calls in a generic ``except Exception`` handler
    that converts the exception to a ``ToolError`` with ``str(e)`` as the message, so
    raising this with a clear, actionable message here is sufficient to surface a
    helpful error to the calling LLM/agent without touching every tool module.
    """


class CMLFeatureUnsupportedError(Exception):
    """Raised when a requested feature (currently: CML API token authentication)
    requires a newer CML controller version than the connected server reports, or when
    the controller version cannot be determined to check.
    """


def _decode_jwt_payload_unverified(token: str) -> dict:
    """Best-effort, signature-unverified decode of a JWT's payload segment.

    Used only to opportunistically recover the token owner's user id (the ``sub``
    claim) for local bookkeeping -- resolving ``self.username`` so that
    username-keyed features (ACLs, ``is_admin()``, pyATS testbed sync) keep working
    when a client authenticates with only a bearer API token and no username. The CML
    server independently verifies the token's signature and expiry on every request,
    so an unverified local decode here carries no security implication: worst case we
    simply fail to resolve an identity and those features degrade gracefully.
    """
    try:
        _header, payload_b64, _signature = token.split(".", 2)
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload_bytes)
    except Exception:
        return {}


class CMLClient(object):
    """
    Async client for interacting with the CML API.
    Handles authentication and provides methods to fetch system and lab information.
    """

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        api_token: str | None = None,
        transport: str = "stdio",
        verify_ssl: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.api_token = api_token
        self.transport = transport
        self.verify_ssl = verify_ssl

        self._token = None
        self.admin = None
        self.needs_reauth = False
        self._supports_native_cli: bool | None = None
        self.current_connected_cml_version: tuple[int, int, int] | None = None
        self._version_fetch_attempted = False

        # HTTP-mode-only bookkeeping (always None in stdio mode): the cml_client_cache
        # key this instance is currently stored under, and the non-secret suffix
        # (CML URL + verify_ssl) of that key. Set by middleware.py after caching a new
        # client, and used by the set_cml_token tool to re-key this client in the cache
        # after set_token() rotates its credentials in place, rather than leaving it
        # reachable under its stale original key.
        self._cache_key: str | None = None
        self._cache_key_suffix: str | None = None

        self.base_url = host.rstrip("/")
        self.api_base = f"{self.base_url}/api/v0"
        self.client = httpx.AsyncClient(verify=verify_ssl, timeout=API_TIMEOUT)
        self.client.headers.update({"X-CML-CLIENT": MCP_CLIENT_IDENTIFIER})

    @property
    def token(self) -> str | None:
        return self._token

    @token.setter
    def token(self, value: str | None) -> None:
        self._token = value
        if not value:
            self.client.headers.pop("Authorization", None)
        else:
            self.client.headers.update({"Authorization": f"Bearer {self._token}"})

    async def _probe_authok(self) -> None:
        """
        GET /api/v0/authok using the currently set self.token.
        Raises httpx.HTTPStatusError (401 if the token is invalid/expired/revoked)
        or httpx.RequestError on transport failures.
        """
        url = f"{self.base_url}/api/v0/authok"
        resp = await self.client.get(url)
        resp.raise_for_status()

    async def _fetch_connected_version(self) -> None:
        """
        Fetch and cache the connected CML controller's version via the unauthenticated
        GET /system_information endpoint (same call virl2_client's own
        check_controller_version() makes). Called once, as early as possible (the
        first time login() runs), and cached in self.current_connected_cml_version for
        the lifetime of this client, since the controller version cannot change
        mid-session (same assumption as supports_native_cli()).

        Failures here are non-fatal: self.current_connected_cml_version simply stays
        None, and version-gated features (e.g. CML API token auth) fail with a clear
        error only when they are actually used.
        """
        if self._version_fetch_attempted:
            return
        self._version_fetch_attempted = True
        try:
            resp = await self.client.get(f"{self.base_url}/api/v0/system_information")
            resp.raise_for_status()
            version_str = resp.json().get("version", "")
            self.current_connected_cml_version = _strip_controller_version(version_str)
        except Exception:
            logger.exception("Failed to fetch the connected CML controller's version")
            self.current_connected_cml_version = None

    async def _resolve_identity_from_token(self) -> None:
        """
        Best-effort resolution of self.username from the current bearer token when no
        username was supplied (pure API-token auth). This keeps username-keyed features
        (HTTP-mode ACLs, is_admin(), pyATS testbed sync in tools/cli.py) working when the
        client authenticates with only CML_API_TOKEN / an X-Authorization Bearer header.
        Failures here are non-fatal: self.username simply stays None and those features
        degrade gracefully.
        """
        if self.username or not self.token:
            return
        payload = _decode_jwt_payload_unverified(self.token)
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("Could not resolve token owner: no 'sub' claim found in the CML API token")
            return
        try:
            resp = await self.client.get(f"{self.api_base}/users/{user_id}")
            resp.raise_for_status()
            self.username = resp.json().get("username")
        except Exception:
            logger.warning("Failed to resolve username for the API-token authenticated session", exc_info=True)

    async def login(self) -> None:
        """
        Authenticate with the CML API and store the token for future requests.

        If a long-lived API token was supplied (self.api_token), it is activated and
        immediately validated against the server -- this is the persistent "long-term
        token" auth path intended to replace username/password. If the token is
        rejected, CMLTokenExpiredError is raised with an actionable message instead of
        an opaque 401. Otherwise, the traditional username/password login flow is used.
        """
        await self._fetch_connected_version()

        if self.api_token:
            if not _MIN_API_TOKEN_VERSION.supported_by(self.current_connected_cml_version):
                connected_str = (
                    ".".join(str(part) for part in self.current_connected_cml_version)
                    if self.current_connected_cml_version is not None
                    else "an unknown version (could not be determined)"
                )
                raise CMLFeatureUnsupportedError(
                    f"CML API token authentication requires CML {_MIN_API_TOKEN_VERSION.value} or later; this "
                    f"server is running {connected_str}. Use CML_USERNAME/CML_PASSWORD instead."
                )
            self.token = self.api_token
            self.needs_reauth = False
            try:
                await self._probe_authok()
            except httpx.HTTPStatusError as e:
                self.token = None
                self.needs_reauth = True
                if e.response.status_code == 401:
                    raise CMLTokenExpiredError(
                        "The configured CML API token is invalid, expired, or has been revoked. Obtain a new "
                        "token from CML and either call the 'set_cml_token' tool to update the running session "
                        "without restarting, or set a new CML_API_TOKEN and restart the MCP server."
                    ) from e
                logger.exception("Failed to validate CML API token")
                raise
            except httpx.RequestError:
                self.token = None
                self.needs_reauth = True
                logger.exception("Failed to validate CML API token")
                raise
            await self._resolve_identity_from_token()
            logger.info("Activated CML API token for authentication")
            return

        url = f"{self.base_url}/api/v0/authenticate"
        try:
            resp = await self.client.post(
                url,
                json={"username": self.username, "password": self.password},
            )
            resp.raise_for_status()
            self.token = resp.json()
            self.needs_reauth = False
            logger.info("Authenticated with CML API")
        except Exception as e:
            logger.exception("Failed to authenticate with CML API")
            self.needs_reauth = True
            raise e

    async def set_token(self, new_token: str) -> None:
        """
        Replace the API token used for authentication with a new one and validate it
        immediately, without restarting the process or reconnecting the client.

        Lets a caller (e.g. the 'set_cml_token' MCP tool) recover from a mid-session or
        post-restart token expiry by supplying a fresh long-lived CML API token.
        Raises CMLTokenExpiredError if the new token itself is rejected by the server, or
        CMLFeatureUnsupportedError if the connected CML controller is older than the
        minimum version that supports API token authentication (see _MIN_API_TOKEN_VERSION).
        """
        self.api_token = new_token
        self.token = None
        self.username = None
        self.admin = None
        self.needs_reauth = False
        await self.login()

    async def check_authentication(self) -> None:
        """
        Check if the current session is authenticated.
        If not, re-authenticate.
        """
        if self.token:
            try:
                await self._probe_authok()
                return  # Already authenticated
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:  # Unauthorized, re-authenticate
                    logger.debug("Authentication failed, re-authenticating")
                    self.token = None
                else:
                    logger.exception("Error checking authentication")
                    raise e
            except httpx.RequestError as e:
                logger.exception("Error checking authentication")
                raise e

        # If token is None or authentication failed, re-authenticate
        if not self.token:
            logger.debug("[Re-]authenticating with CML API")
            await self.login()

    async def is_admin(self) -> bool:
        """
        Check if the current user is an admin.
        Returns True if the user is an admin, False otherwise.
        """
        if self.admin is not None:
            return self.admin

        await self.check_authentication()
        try:
            resp = await self.client.get(f"{self.base_url}/api/v0/users/{self.username}/id")
            resp.raise_for_status()
            user_id = resp.json()
            resp = await self.client.get(f"{self.base_url}/api/v0/users/{user_id}")
            resp.raise_for_status()
            self.admin = resp.json().get("admin", False)
            return self.admin
        except Exception:
            logger.exception("Error checking admin status")
            return False

    async def supports_native_cli(self) -> bool:
        """
        Check whether the connected CML controller exposes the native
        POST /labs/{lab_id}/nodes/{node_id}/cli endpoint (added in CML 2.11).

        The result is derived from /system_information's "version" field and
        cached for the lifetime of this client, since the controller version
        cannot change mid-session.
        """
        if self._supports_native_cli is not None:
            return self._supports_native_cli

        try:
            info = await self.get("/system_information")
            self._supports_native_cli = _controller_supports_native_cli(info["version"])
        except Exception:
            logger.exception("Could not determine CML server version; assuming native CLI API is unavailable")
            self._supports_native_cli = False

        return self._supports_native_cli

    async def get(self, endpoint: str, params: dict | None = None, is_binary: bool = False) -> Any:
        """
        Make a GET request to the CML API.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            return resp.json() if not is_binary else resp.content
        except httpx.RequestError as e:
            logger.exception("Error making GET request to %s", url)
            raise e

    async def post(
        self,
        endpoint: str,
        data: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> Any | None:
        """
        Make a POST request to the CML API.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        request_kwargs: dict[str, Any] = {"json": data, "params": params}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        try:
            resp = await self.client.post(url, **request_kwargs)
            resp.raise_for_status()
            if resp.status_code == 204:  # No content
                return None
            return resp.json()
        except httpx.RequestError as e:
            logger.exception("Error making POST request to %s", url)
            raise e

    async def put(self, endpoint: str, data: dict | None = None) -> Any | None:
        """
        Make a PUT request to the CML API.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        try:
            resp = await self.client.put(url, json=data)
            resp.raise_for_status()
            if resp.status_code == 204:  # No content
                return None
            return resp.json()
        except httpx.RequestError as e:
            logger.exception("Error making PUT request to %s", url)
            raise e

    async def delete(self, endpoint: str) -> dict | None:
        """
        Make a DELETE request to the CML API.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        try:
            resp = await self.client.delete(url)
            resp.raise_for_status()
            if resp.status_code == 204:  # No content
                return None
            return resp.json()
        except httpx.RequestError as e:
            logger.exception("Error making DELETE request to %s", url)
            raise e

    async def patch(self, endpoint: str, data: dict | None = None) -> Any | None:
        """
        Make a PATCH request to the CML API.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        try:
            resp = await self.client.patch(url, json=data)
            resp.raise_for_status()
            if resp.status_code == 204:  # No content
                return None
            return resp.json()
        except httpx.RequestError as e:
            logger.exception("Error making PATCH request to %s", url)
            raise e

    async def close(self) -> None:
        """Close the HTTP client and clean up all resources."""
        try:
            # Close the httpx async client which should clean up connection pools and semaphores
            await self.client.aclose()
            logger.debug("HTTP client closed successfully")
        except Exception:
            logger.exception("Error closing HTTP client")
