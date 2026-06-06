# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Unit tests for the CML JWT (long-lived token) authentication feature:

- settings.validate_stdio_auth  -- CML_JWT vs CML_USERNAME/CML_PASSWORD mutual exclusion
- cml_client._decode_jwt_payload_unverified / login() / set_jwt() / identity resolution
- middleware.token_cache_key    -- token-auth cache key format
- cache.ThreadSafeCache.rekey / _sweep_once -- runtime re-key + background eviction
"""

import base64
import json
import time
import uuid

import httpx
import pytest

from cml_mcp.cml_client import CMLTokenExpiredError, _decode_jwt_payload_unverified
from cml_mcp.settings import validate_stdio_auth
from cml_mcp.tools.cache import CacheEntry, ThreadSafeCache
from cml_mcp.tools.middleware import token_cache_key


def _make_jwt(claims: dict) -> str:
    """Build a syntactically valid (unsigned) 3-segment JWT with the given payload claims."""

    def _seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_seg({'alg': 'none'})}.{_seg(claims)}.sig"


class _FakeCachedClient:
    """Minimal stand-in for a cached CMLClient: only needs an awaitable close()."""

    def __init__(self, raise_on_close: bool = False) -> None:
        self.closed = False
        self._raise_on_close = raise_on_close

    async def close(self) -> None:
        self.closed = True
        if self._raise_on_close:
            raise RuntimeError("close failed")


# --------------------------------------------------------------------------- #
# settings.validate_stdio_auth
# --------------------------------------------------------------------------- #
class TestValidateStdioAuth:
    def test_jwt_only_is_accepted(self):
        validate_stdio_auth("https://cml.example.com", None, None, "a.b.c")

    def test_userpass_only_is_accepted(self):
        validate_stdio_auth("https://cml.example.com", "user", "pass", None)

    def test_jwt_and_userpass_are_mutually_exclusive(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            validate_stdio_auth("https://cml.example.com", "user", "pass", "a.b.c")

    def test_no_credentials_is_rejected(self):
        with pytest.raises(ValueError, match="Either CML_JWT"):
            validate_stdio_auth("https://cml.example.com", None, None, None)

    def test_username_without_password_is_rejected(self):
        with pytest.raises(ValueError, match="must both be set together"):
            validate_stdio_auth("https://cml.example.com", "user", None, None)

    def test_missing_url_is_rejected(self):
        with pytest.raises(ValueError, match="CML_URL must be set"):
            validate_stdio_auth(None, None, None, "a.b.c")


# --------------------------------------------------------------------------- #
# cml_client._decode_jwt_payload_unverified
# --------------------------------------------------------------------------- #
class TestDecodeJwtPayload:
    def test_decodes_sub_claim(self):
        token = _make_jwt({"sub": "abc123", "exp": 42})
        assert _decode_jwt_payload_unverified(token) == {"sub": "abc123", "exp": 42}

    def test_handles_payload_needing_base64_padding(self):
        # A claim set whose base64url payload length is not a multiple of 4 exercises the
        # padding re-insertion; it must still decode rather than raise.
        token = _make_jwt({"sub": "x"})
        assert _decode_jwt_payload_unverified(token).get("sub") == "x"

    def test_opaque_non_jwt_returns_empty(self):
        assert _decode_jwt_payload_unverified("not-a-jwt") == {}

    def test_garbage_payload_returns_empty(self):
        assert _decode_jwt_payload_unverified("aaa.!!!not-base64!!!.bbb") == {}


# --------------------------------------------------------------------------- #
# middleware.token_cache_key
# --------------------------------------------------------------------------- #
class TestTokenCacheKey:
    def test_is_namespaced_and_hashes_token(self):
        key = token_cache_key("secret-token", "https://cml.example.com:443:True")
        assert key.startswith("token:")
        assert key.endswith(":https://cml.example.com:443:True")
        assert "secret-token" not in key  # raw token must never appear in the key

    def test_same_inputs_are_stable(self):
        assert token_cache_key("t", "s") == token_cache_key("t", "s")

    def test_different_tokens_differ(self):
        assert token_cache_key("t1", "s") != token_cache_key("t2", "s")


# --------------------------------------------------------------------------- #
# cache.ThreadSafeCache.rekey
# --------------------------------------------------------------------------- #
class TestRekey:
    async def test_moves_entry_without_closing_client(self):
        cache = ThreadSafeCache(ttl=3600)
        client = _FakeCachedClient()
        cache._cache["old"] = CacheEntry(value=client)

        await cache.rekey("old", "new")

        assert "old" not in cache._cache
        assert cache._cache["new"].value is client
        assert client.closed is False

    async def test_noop_when_old_key_absent(self):
        cache = ThreadSafeCache(ttl=3600)
        await cache.rekey("missing", "new")  # must not raise
        assert cache._cache == {}

    async def test_noop_when_keys_equal(self):
        cache = ThreadSafeCache(ttl=3600)
        client = _FakeCachedClient()
        cache._cache["k"] = CacheEntry(value=client)
        await cache.rekey("k", "k")
        assert cache._cache["k"].value is client
        assert client.closed is False

    async def test_displaced_entry_at_new_key_is_closed(self):
        cache = ThreadSafeCache(ttl=3600)
        moved = _FakeCachedClient()
        displaced = _FakeCachedClient()
        cache._cache["old"] = CacheEntry(value=moved)
        cache._cache["new"] = CacheEntry(value=displaced)

        await cache.rekey("old", "new")

        assert cache._cache["new"].value is moved
        assert displaced.closed is True
        assert moved.closed is False


# --------------------------------------------------------------------------- #
# cache.ThreadSafeCache._sweep_once
# --------------------------------------------------------------------------- #
class TestSweepOnce:
    async def test_evicts_and_closes_only_expired_entries(self):
        cache = ThreadSafeCache(ttl=100)
        fresh = _FakeCachedClient()
        stale = _FakeCachedClient()
        cache._cache["fresh"] = CacheEntry(value=fresh)
        cache._cache["stale"] = CacheEntry(value=stale, timestamp=time.time() - 1000)

        await cache._sweep_once()

        assert "stale" not in cache._cache and stale.closed is True
        assert "fresh" in cache._cache and fresh.closed is False

    async def test_one_failing_close_does_not_abort_the_sweep(self):
        # return_exceptions=True keeps a single client.close() failure from propagating out of
        # the sweep (which would permanently stop future sweeps) -- every expired entry is still
        # evicted from the dict.
        cache = ThreadSafeCache(ttl=100)
        bad = _FakeCachedClient(raise_on_close=True)
        good = _FakeCachedClient()
        cache._cache["bad"] = CacheEntry(value=bad, timestamp=time.time() - 1000)
        cache._cache["good"] = CacheEntry(value=good, timestamp=time.time() - 1000)

        await cache._sweep_once()  # must not raise

        assert cache._cache == {}
        assert good.closed is True


# --------------------------------------------------------------------------- #
# CMLClient JWT login / set_jwt / identity resolution
# --------------------------------------------------------------------------- #
def _jwt_client(cls, handler, *, jwt: str):
    """Build a real CMLClient wired to an httpx MockTransport, configured for JWT auth."""
    client = cls.__new__(cls)
    client.username = None
    client.password = None
    client.jwt = jwt
    client.admin = None
    client.needs_reauth = False
    client._token = None
    client.base_url = "http://cml.example.com"
    client.api_base = "http://cml.example.com/api/v0"
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _identity_handler(paths: list, *, authok_status: int = 200, username: str = "alice"):
    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/authok"):
            return httpx.Response(authok_status)
        if "/users/" in request.url.path:
            return httpx.Response(200, json={"username": username})
        return httpx.Response(404)

    return handler


class TestJwtLogin:
    async def test_login_activates_token_and_resolves_username(self, real_cml_client_class):
        user_id = str(uuid.uuid4())
        jwt = _make_jwt({"sub": user_id})
        paths = []
        client = _jwt_client(real_cml_client_class, _identity_handler(paths), jwt=jwt)
        try:
            await client.login()
            assert client.token == jwt
            assert client.needs_reauth is False
            assert client.username == "alice"
            assert any(p.endswith(f"/users/{user_id}") for p in paths)
        finally:
            await client.client.aclose()

    async def test_login_raises_token_expired_on_401(self, real_cml_client_class):
        jwt = _make_jwt({"sub": str(uuid.uuid4())})
        client = _jwt_client(real_cml_client_class, _identity_handler([], authok_status=401), jwt=jwt)
        try:
            with pytest.raises(CMLTokenExpiredError):
                await client.login()
            assert client.token is None
            assert client.needs_reauth is True
        finally:
            await client.client.aclose()

    async def test_non_uuid_sub_skips_user_lookup(self, real_cml_client_class):
        # A 'sub' claim that is not a valid UUID must not be interpolated into a users/{id}
        # request; identity resolution simply degrades to username=None.
        jwt = _make_jwt({"sub": "../../etc/passwd"})
        paths = []
        client = _jwt_client(real_cml_client_class, _identity_handler(paths), jwt=jwt)
        try:
            await client.login()
            assert client.token == jwt
            assert client.username is None
            assert not any("/users/" in p for p in paths)
        finally:
            await client.client.aclose()

    async def test_set_jwt_swaps_and_validates_new_token(self, real_cml_client_class):
        old = _make_jwt({"sub": str(uuid.uuid4())})
        new = _make_jwt({"sub": str(uuid.uuid4())})
        client = _jwt_client(real_cml_client_class, _identity_handler([]), jwt=old)
        try:
            await client.set_jwt(new)
            assert client.jwt == new
            assert client.token == new
            assert client.needs_reauth is False
        finally:
            await client.client.aclose()

    async def test_set_jwt_rejected_token_raises(self, real_cml_client_class):
        old = _make_jwt({"sub": str(uuid.uuid4())})
        bad = _make_jwt({"sub": str(uuid.uuid4())})
        client = _jwt_client(real_cml_client_class, _identity_handler([], authok_status=401), jwt=old)
        try:
            with pytest.raises(CMLTokenExpiredError):
                await client.set_jwt(bad)
            assert client.token is None
            assert client.needs_reauth is True
        finally:
            await client.client.aclose()
