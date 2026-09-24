# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Tests for CMLClient.get_binary_capped -- the streamed, size-capped binary download used by
the PCAP tool to avoid buffering an oversized capture into memory."""

import httpx
import pytest

from cml_mcp.cml_client import CMLClient, ResponseTooLargeError


def _client(handler) -> CMLClient:
    """Build a CMLClient wired to an httpx MockTransport, bypassing real auth/login."""
    client = CMLClient.__new__(CMLClient)
    client.needs_reauth = False
    client.base_url = "http://cml.example.com"
    client.api_base = "http://cml.example.com/api/v0"
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._token = "test-token"

    async def _noauth() -> None:
        return None

    client.check_authentication = _noauth  # type: ignore[method-assign]
    return client


def _handler(*, size: int, send_content_length: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"content-length": str(size)} if send_content_length else {}
        return httpx.Response(200, content=b"x" * size, headers=headers)

    return handler


async def test_returns_full_body_when_under_cap():
    client = _client(_handler(size=100, send_content_length=True))
    try:
        data = await client.get_binary_capped("/pcap/key", max_bytes=1000)
        assert data == b"x" * 100
    finally:
        await client.client.aclose()


async def test_rejects_via_content_length_before_reading_body():
    client = _client(_handler(size=2000, send_content_length=True))
    try:
        with pytest.raises(ResponseTooLargeError) as exc:
            await client.get_binary_capped("/pcap/key", max_bytes=1000)
        assert exc.value.size == 2000
        assert exc.value.max_bytes == 1000
    finally:
        await client.client.aclose()


async def test_rejects_via_running_total_when_no_content_length():
    # No Content-Length header: the cap must still be enforced on the streamed byte total.
    client = _client(_handler(size=2000, send_content_length=False))
    try:
        with pytest.raises(ResponseTooLargeError) as exc:
            await client.get_binary_capped("/pcap/key", max_bytes=1000)
        assert exc.value.size is not None and exc.value.size > 1000
    finally:
        await client.client.aclose()
