# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import httpx
import pytest
from fastmcp.exceptions import ToolError

from cml_mcp.tools.errors import sanitize_http_error


def _make_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://cml.example.com/api/v0/labs")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


class TestSanitizeHttpError:
    def test_upstream_body_never_appears_in_message(self):
        secret_body = "Traceback (most recent call last):\n  File /internal/path/views.py line 42, secret detail"
        err = sanitize_http_error(_make_error(500, secret_body))
        assert isinstance(err, ToolError)
        assert secret_body not in str(err)

    def test_known_status_code_gets_generic_message(self):
        err = sanitize_http_error(_make_error(404, "<html>some internal trace</html>"))
        assert "not found" in str(err).lower()

    def test_unknown_4xx_status_code_gets_fallback_message(self):
        err = sanitize_http_error(_make_error(418, "I'm a teapot, here's my stack trace"))
        assert "teapot" not in str(err).lower()
        assert "rejected this request" in str(err).lower()

    def test_5xx_status_code_gets_generic_server_error_message(self):
        err = sanitize_http_error(_make_error(503, "upstream db connection string: postgres://user:pass@host"))
        assert "postgres" not in str(err)
        assert "internal error" in str(err).lower()

    @pytest.mark.parametrize("status_code", [400, 401, 403, 409, 422, 429])
    def test_all_mapped_status_codes_produce_toolerror(self, status_code):
        err = sanitize_http_error(_make_error(status_code, "leaky body"))
        assert isinstance(err, ToolError)
        assert "leaky body" not in str(err)
