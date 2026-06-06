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
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx

from cml_mcp.settings import settings

MCP_CLIENT_IDENTIFIER = "CmlMCP"


class ResponseTooLargeError(Exception):
    """
    Raised by :meth:`CMLClient.get_binary_capped` when a streamed binary download exceeds the
    caller-supplied byte cap (either via the upstream ``Content-Length`` header or the running
    total of streamed chunks). Carries the observed/declared size and the cap so callers can
    build a user-facing message without re-buffering the body.
    """

    def __init__(self, size: int | None, max_bytes: int) -> None:
        self.size = size
        self.max_bytes = max_bytes
        detail = f"{size} bytes" if size is not None else "an unknown number of bytes"
        super().__init__(f"Response body is {detail}, exceeding the {max_bytes}-byte cap")


def _validate_host(host: str) -> str:
    """
    Pre-validate a CML server URL before it is used to construct an httpx client.

    Rejects anything that is not a well-formed http/https URL with a hostname, so a
    malformed or scheme-less value (e.g. from a misconfigured env var or a client
    header that slipped past middleware validation) fails fast with a clear error
    instead of being silently mangled by httpx/urllib3.
    """
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Invalid CML server URL '{host}': must be an absolute http(s) URL with a hostname")
    return host


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


class CMLClient:
    """
    Async client for interacting with the CML API.
    Handles authentication and provides methods to fetch system and lab information.
    """

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        jwt: str | None = None,
        transport: str = "stdio",
        verify_ssl: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.jwt = jwt
        self.transport = transport
        self.verify_ssl = verify_ssl

        self._token = None
        self.admin = None
        self.needs_reauth = False

        # HTTP-mode-only bookkeeping (always None in stdio mode): the cml_client_cache
        # key this instance is currently stored under, and the non-secret suffix
        # (CML URL + verify_ssl) of that key. Set by middleware.py after caching a new
        # client, and used by the set_cml_jwt tool to re-key this client in the cache
        # after set_jwt() rotates its credentials in place, rather than leaving it
        # reachable under its stale original key.
        self._cache_key: str | None = None
        self._cache_key_suffix: str | None = None

        self.base_url = _validate_host(host.rstrip("/"))
        self.api_base = f"{self.base_url}/api/v0"
        # follow_redirects is explicitly disabled (rather than relying on httpx's
        # current default of False) so a future httpx version bump can't silently
        # start following redirects and let a malicious/compromised CML endpoint
        # redirect the login POST (which carries the user's credentials) to an
        # arbitrary, non-allow-listed host.
        # Explicit connect/read/write/pool timeouts (rather than a single scalar) so a slow
        # or hung connect phase can't block a request indefinitely longer than intended.
        timeout = httpx.Timeout(
            connect=settings.cml_api_connect_timeout,
            read=settings.cml_api_timeout,
            write=settings.cml_api_timeout,
            pool=settings.cml_api_timeout,
        )
        self.client = httpx.AsyncClient(verify=verify_ssl, timeout=timeout, follow_redirects=False)
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

    async def _resolve_identity_from_token(self) -> None:
        """
        Best-effort resolution of self.username from the current bearer token when no
        username was supplied (pure API-token auth). This keeps username-keyed features
        (HTTP-mode ACLs, is_admin(), pyATS testbed sync in tools/cli.py) working when the
        client authenticates with only CML_JWT / an X-Authorization Bearer header.
        Failures here are non-fatal: self.username simply stays None and those features
        degrade gracefully.
        """
        if self.username or not self.token:
            return
        payload = _decode_jwt_payload_unverified(self.token)
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("Could not resolve token owner: no 'sub' claim found in the CML JWT")
            return
        # The 'sub' claim is read from an unverified token, so validate it is a well-formed
        # UUID before interpolating it into the users/{id} request path -- this both rejects
        # junk claims early and prevents a crafted claim from reshaping the request URL.
        try:
            user_id = str(uuid.UUID(str(user_id)))
        except (ValueError, TypeError):
            logger.warning("Could not resolve token owner: 'sub' claim is not a valid user id")
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

        If a long-lived API token was supplied (self.jwt), it is activated and
        immediately validated against the server -- this is the persistent "long-term
        token" auth path intended to replace username/password. If the token is
        rejected, CMLTokenExpiredError is raised with an actionable message instead of
        an opaque 401. Otherwise, the traditional username/password login flow is used.
        """
        if self.jwt:
            self.token = self.jwt
            self.needs_reauth = False
            try:
                await self._probe_authok()
            except httpx.HTTPStatusError as e:
                self.token = None
                self.needs_reauth = True
                if e.response.status_code == 401:
                    raise CMLTokenExpiredError(
                        "The configured CML API token is invalid, expired, or has been revoked. Obtain a new "
                        "token from CML and either call the 'set_cml_jwt' tool to update the running session "
                        "without restarting, or set a new CML_JWT and restart the MCP server."
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

    async def set_jwt(self, new_token: str) -> None:
        """
        Replace the API token used for authentication with a new one and validate it
        immediately, without restarting the process or reconnecting the client.

        Lets a caller (e.g. the 'set_cml_jwt' MCP tool) recover from a mid-session or
        post-restart token expiry by supplying a fresh long-lived CML API token.
        Raises CMLTokenExpiredError if the new token itself is rejected by the server.
        """
        self.jwt = new_token
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

    async def get_binary_capped(self, endpoint: str, max_bytes: int, params: dict | None = None) -> bytes:
        """
        Stream a binary GET response, aborting as soon as it is known to exceed ``max_bytes``
        so an oversized upstream body can never be fully buffered into memory.

        The cap is enforced twice: first against the upstream ``Content-Length`` header (an early
        rejection before any body is read), then against the running total of streamed chunks (in
        case the header is missing or understates the size). Raises :class:`ResponseTooLargeError`
        when the cap is exceeded; otherwise returns the full body as bytes.
        """
        await self.check_authentication()
        url = f"{self.api_base}{endpoint}"
        try:
            async with self.client.stream("GET", url, params=params) as resp:
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > max_bytes:
                            raise ResponseTooLargeError(int(declared), max_bytes)
                    except ValueError:
                        # Malformed Content-Length: ignore the header and rely on the running cap.
                        pass
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ResponseTooLargeError(total, max_bytes)
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.RequestError as e:
            logger.exception("Error streaming GET request to %s", url)
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
