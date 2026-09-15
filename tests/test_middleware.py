# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

import pytest
from mcp.shared.exceptions import McpError
from pydantic import AnyHttpUrl, TypeAdapter

from cml_mcp.tools.middleware import CustomHttpRequestMiddleware

_url_adapter = TypeAdapter(AnyHttpUrl)


def _allowed(*urls: str) -> list[AnyHttpUrl]:
    return [_url_adapter.validate_python(u) for u in urls]


class TestValidateUrlPattern:
    """CML_URL_PATTERN must be matched with re.fullmatch, not re.match."""

    def test_fullmatch_rejects_suffix_extended_hostname(self):
        # An operator pattern that forgets the trailing "$" must still be
        # rejected for a hostname that merely starts with the allowed prefix.
        pattern = r"^https://cml\.example\.com"
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://cml.example.com.attacker.tld", [], pattern)

    def test_fullmatch_accepts_exact_origin(self):
        # The canonical origin used for matching always includes the (default-filled)
        # port, so the pattern must account for it.
        pattern = r"^https://cml\.example\.com:443$"
        # Should not raise.
        CustomHttpRequestMiddleware._validate_url("https://cml.example.com", [], pattern)

    def test_fullmatch_rejects_userinfo_prefixed_host(self):
        # https://cml.example.com@attacker.tld has a real host of attacker.tld;
        # AnyHttpUrl strips the userinfo before the pattern is applied, so the
        # canonical origin built for matching is attacker.tld's, not
        # cml.example.com's, and must be rejected.
        pattern = r"^https://cml\.example\.com$"
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://cml.example.com@attacker.tld", [], pattern)


class TestValidateUrlUserinfo:
    """URLs containing @ userinfo must never let the userinfo host escape scrutiny."""

    def test_userinfo_host_rejected_by_allow_list(self):
        allowed = _allowed("https://cml.example.com")
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://cml.example.com@attacker.tld", allowed, None)

    def test_userinfo_host_rejected_by_pattern(self):
        pattern = r"^https://cml\.example\.com$"
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://trusted.example.com@attacker.tld", [], pattern)


class TestValidateUrlAllowList:
    """Allow-list comparisons should be resilient to cosmetic differences."""

    def test_trailing_slash_is_ignored(self):
        allowed = _allowed("https://cml.example.com")
        # Should not raise even though the incoming URL has a trailing slash.
        CustomHttpRequestMiddleware._validate_url("https://cml.example.com/", allowed, None)

    def test_host_comparison_is_case_insensitive(self):
        allowed = _allowed("https://cml.example.com")
        # Should not raise even though the incoming URL's host differs in case.
        CustomHttpRequestMiddleware._validate_url("https://CML.EXAMPLE.COM", allowed, None)

    def test_scheme_case_is_not_used_to_bypass_allow_list(self):
        allowed = _allowed("https://cml.example.com")
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("http://cml.example.com", allowed, None)

    def test_unlisted_host_rejected(self):
        allowed = _allowed("https://cml.example.com")
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://attacker.tld", allowed, None)

    def test_missing_allow_list_and_pattern_rejected(self):
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("https://cml.example.com", [], None)

    def test_malformed_url_rejected(self):
        allowed = _allowed("https://cml.example.com")
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_url("not-a-url", allowed, None)
