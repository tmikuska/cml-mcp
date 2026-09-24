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


class _FakeClient:
    def __init__(self, username: str) -> None:
        self.username = username


class TestAclFailClosed:
    """A configured-but-invalid ACL file must deny every tool, not fall back to 'no ACLs'."""

    def setup_method(self):
        import cml_mcp.tools.middleware as mw

        self._orig_acl_data = dict(mw.acl_data)
        self._orig_failed = mw.acl_load_failed

    def teardown_method(self):
        import cml_mcp.tools.middleware as mw

        mw.acl_data.clear()
        mw.acl_data.update(self._orig_acl_data)
        mw.acl_load_failed = self._orig_failed

    async def test_load_failure_denies_all_tools(self):
        import cml_mcp.tools.middleware as mw

        mw.acl_data.clear()
        mw.acl_load_failed = True
        assert await CustomHttpRequestMiddleware.check_tool_enabled("get_cml_labs", _FakeClient("alice")) is False

    async def test_no_acl_configured_allows_all_tools(self):
        import cml_mcp.tools.middleware as mw

        mw.acl_data.clear()
        mw.acl_load_failed = False
        assert await CustomHttpRequestMiddleware.check_tool_enabled("get_cml_labs", _FakeClient("alice")) is True


class TestAclFilePermissions:
    """ACL files must be owned by the running user and not group/other-writable."""

    def test_group_writable_file_rejected(self, tmp_path):
        import os
        import stat as stat_module

        from cml_mcp.tools.middleware import _check_acl_file_permissions

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\n")
        os.chmod(aclf, stat_module.S_IRUSR | stat_module.S_IWUSR | stat_module.S_IWGRP)
        assert _check_acl_file_permissions(aclf) is not None

    def test_owned_private_file_accepted(self, tmp_path):
        import stat as stat_module

        from cml_mcp.tools.middleware import _check_acl_file_permissions

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\n")
        aclf.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)
        assert _check_acl_file_permissions(aclf) is None

    def test_root_owned_read_only_file_accepted(self, tmp_path):
        """A root-owned, non-writable file (the common Docker bind-mount/ConfigMap/Secret
        pattern) must be accepted even when the running process is a non-root uid -- it
        couldn't have been tampered with by that process either way."""
        import stat as stat_module

        from cml_mcp.tools.middleware import _check_acl_file_permissions

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\n")
        aclf.chmod(stat_module.S_IRUSR)

        class _FakePath:
            """Duck-typed stand-in for Path -- avoids monkeypatching the real pathlib.Path
            class globally, which would break pytest's own internal Path.stat() calls (e.g.
            tmp_path cleanup) for the duration of the test."""

            def __init__(self, real_path, st_uid, st_mode):
                self._real_path = real_path
                self._st_uid = st_uid
                self._st_mode = st_mode

            def stat(self):
                import types

                return types.SimpleNamespace(st_uid=self._st_uid, st_mode=self._st_mode)

            def __str__(self):
                return str(self._real_path)

        real_mode = aclf.stat().st_mode
        fake_aclf = _FakePath(aclf, st_uid=0, st_mode=real_mode)
        assert _check_acl_file_permissions(fake_aclf) is None

    def test_other_uid_owned_file_rejected(self, tmp_path):
        """A file owned by neither the running user nor root must still be rejected."""
        from cml_mcp.tools.middleware import _check_acl_file_permissions

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\n")

        class _FakePath:
            def __init__(self, real_path, st_uid, st_mode):
                self._real_path = real_path
                self._st_uid = st_uid
                self._st_mode = st_mode

            def stat(self):
                import types

                return types.SimpleNamespace(st_uid=self._st_uid, st_mode=self._st_mode)

            def __str__(self):
                return str(self._real_path)

        real_mode = aclf.stat().st_mode
        fake_aclf = _FakePath(aclf, st_uid=65534, st_mode=real_mode)
        assert _check_acl_file_permissions(fake_aclf) is not None


class TestLoadAclDataFailClosed:
    """load_acl_data() must fail closed on missing/empty/invalid ACL files."""

    def setup_method(self):
        import cml_mcp.tools.middleware as mw

        self._orig_acl_data = dict(mw.acl_data)
        self._orig_failed = mw.acl_load_failed

    def teardown_method(self):
        import cml_mcp.tools.middleware as mw

        mw.acl_data.clear()
        mw.acl_data.update(self._orig_acl_data)
        mw.acl_load_failed = self._orig_failed

    def test_empty_file_fails_closed(self, tmp_path, monkeypatch):
        import stat as stat_module

        import cml_mcp.tools.middleware as mw
        from cml_mcp.settings import settings

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("")
        aclf.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)
        monkeypatch.setattr(settings, "cml_mcp_transport", "http")
        monkeypatch.setattr(settings, "cml_mcp_acl_file", str(aclf))
        mw.acl_data.clear()
        mw.load_acl_data()
        assert mw.acl_load_failed is True

    def test_missing_file_fails_closed(self, tmp_path, monkeypatch):
        import cml_mcp.tools.middleware as mw
        from cml_mcp.settings import settings

        monkeypatch.setattr(settings, "cml_mcp_transport", "http")
        monkeypatch.setattr(settings, "cml_mcp_acl_file", str(tmp_path / "does-not-exist.yaml"))
        mw.acl_data.clear()
        mw.load_acl_data()
        assert mw.acl_load_failed is True

    def test_valid_file_loads_successfully(self, tmp_path, monkeypatch):
        import stat as stat_module

        import cml_mcp.tools.middleware as mw
        from cml_mcp.settings import settings

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\nusers: {}\n")
        aclf.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)
        monkeypatch.setattr(settings, "cml_mcp_transport", "http")
        monkeypatch.setattr(settings, "cml_mcp_acl_file", str(aclf))
        mw.acl_data.clear()
        mw.load_acl_data()
        assert mw.acl_load_failed is False
        assert mw.acl_data["default_enabled"] is True

    def test_malformed_default_enabled_fails_closed_end_to_end(self, tmp_path, monkeypatch):
        """A malformed top-level field must invalidate the whole file via load_acl_data(),
        not just the lower-level _validate_acl_data() helper -- guards against a future
        regression where load_acl_data() stops propagating _validate_acl_data()'s None."""
        import stat as stat_module

        import cml_mcp.tools.middleware as mw
        from cml_mcp.settings import settings

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: not-a-bool\nusers: {}\n")
        aclf.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)
        monkeypatch.setattr(settings, "cml_mcp_transport", "http")
        monkeypatch.setattr(settings, "cml_mcp_acl_file", str(aclf))
        mw.acl_data.clear()
        mw.load_acl_data()
        assert mw.acl_load_failed is True
        assert mw.acl_data == {}

    def test_malformed_users_fails_closed_end_to_end(self, tmp_path, monkeypatch):
        import stat as stat_module

        import cml_mcp.tools.middleware as mw
        from cml_mcp.settings import settings

        aclf = tmp_path / "acl.yaml"
        aclf.write_text("default_enabled: true\nusers: [not, a, mapping]\n")
        aclf.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)
        monkeypatch.setattr(settings, "cml_mcp_transport", "http")
        monkeypatch.setattr(settings, "cml_mcp_acl_file", str(aclf))
        mw.acl_data.clear()
        mw.load_acl_data()
        assert mw.acl_load_failed is True
        assert mw.acl_data == {}


class TestRateLimiter:
    def test_allows_up_to_max_attempts_then_blocks(self):
        from cml_mcp.tools.middleware import _SlidingWindowRateLimiter

        limiter = _SlidingWindowRateLimiter(max_attempts=3, window_seconds=60)
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is False

    def test_separate_keys_do_not_share_budget(self):
        from cml_mcp.tools.middleware import _SlidingWindowRateLimiter

        limiter = _SlidingWindowRateLimiter(max_attempts=1, window_seconds=60)
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("5.6.7.8") is True

    def test_window_expiry_resets_budget(self):
        import time

        from cml_mcp.tools.middleware import _SlidingWindowRateLimiter

        limiter = _SlidingWindowRateLimiter(max_attempts=1, window_seconds=0)
        assert limiter.allow("1.2.3.4") is True
        time.sleep(0.01)
        # A window of 0 seconds means any elapsed time immediately expires the prior hit.
        assert limiter.allow("1.2.3.4") is True

    def test_expired_buckets_are_swept_to_bound_memory(self):
        from cml_mcp.tools.middleware import _SlidingWindowRateLimiter

        # window_seconds=0 makes every prior hit expire immediately, so a stream of distinct
        # one-shot keys must not grow _hits without bound once the sweep threshold is crossed.
        limiter = _SlidingWindowRateLimiter(max_attempts=1, window_seconds=0)
        limiter._sweep_threshold = 10
        for i in range(200):
            assert limiter.allow(f"key-{i}") is True
        assert len(limiter._hits) <= limiter._sweep_threshold

    def test_active_keys_survive_sweep(self):
        from cml_mcp.tools.middleware import _SlidingWindowRateLimiter

        # A large window keeps hits live; the sweep must not drop keys that are still within window.
        limiter = _SlidingWindowRateLimiter(max_attempts=5, window_seconds=3600)
        limiter._sweep_threshold = 10
        for i in range(50):
            limiter.allow(f"key-{i}")
        assert len(limiter._hits) == 50


class _FakeToolMessage:
    def __init__(self, name: str, arguments: dict | None = None):
        self.name = name
        self.arguments = arguments


class _FakeToolContext:
    def __init__(self, name: str, arguments: dict | None = None):
        self.message = _FakeToolMessage(name, arguments)


class TestExtractResourceId:
    """_extract_resource_id() is best-effort and never raises on missing/odd input."""

    def test_child_resource_id_wins_over_ambient_lab_id(self):
        # For a tool taking both (e.g. wipe_cml_node), the specific child id is the resource,
        # not the surrounding lab_id.
        assert CustomHttpRequestMiddleware._extract_resource_id({"lab_id": "lab-1", "node_id": "node-1"}) == "node-1"

    def test_lab_only_tool_falls_back_to_lab_id(self):
        assert CustomHttpRequestMiddleware._extract_resource_id({"lab_id": "lab-1"}) == "lab-1"

    def test_falls_back_to_any_trailing_id_key(self):
        assert CustomHttpRequestMiddleware._extract_resource_id({"weird_id": "x"}) == "x"

    def test_missing_or_empty_arguments_returns_placeholder(self):
        assert CustomHttpRequestMiddleware._extract_resource_id({}) == "-"
        assert CustomHttpRequestMiddleware._extract_resource_id(None) == "-"

    def test_falsy_values_are_skipped(self):
        assert CustomHttpRequestMiddleware._extract_resource_id({"node_id": "", "link_id": "l1"}) == "l1"


class TestRequestIdContext:
    """Request-scoped correlation id used to tie audit/log lines for one request together."""

    def test_new_request_id_is_stored_and_retrievable(self):
        from cml_mcp.tools.dependencies import get_request_id, new_request_id

        rid = new_request_id()
        assert rid == get_request_id()
        assert len(rid) == 12  # uuid4().hex[:12] -> 12 hex characters
        # Generating again yields a different id (new request).
        rid2 = new_request_id()
        assert rid2 != rid

    def test_default_outside_request_context(self):
        from cml_mcp.tools.dependencies import _request_id, get_request_id

        token = _request_id.set("-")
        try:
            assert get_request_id() == "-"
        finally:
            _request_id.reset(token)

    def test_log_filter_injects_request_id(self):
        import logging

        from cml_mcp.tools.dependencies import RequestIdLogFilter, new_request_id

        rid = new_request_id()
        record = logging.LogRecord("cml-mcp.test", logging.INFO, __file__, 1, "hello", None, None)
        assert RequestIdLogFilter().filter(record) is True
        assert record.request_id == rid

    def test_user_hash_context_roundtrip(self):
        from cml_mcp.tools.dependencies import get_request_user_hash, set_request_user_hash

        set_request_user_hash("abc123")
        assert get_request_user_hash() == "abc123"
        set_request_user_hash("-")
        assert get_request_user_hash() == "-"


class TestAuditLogOnCallTool:
    """on_call_tool() emits one structured AUDIT log line per invocation, with redaction."""

    async def _allow(self, tool_name, client):
        return True

    async def _deny(self, tool_name, client):
        return False

    async def test_success_is_logged(self, monkeypatch, caplog):
        import logging

        import cml_mcp.tools.dependencies as deps

        monkeypatch.setattr(deps, "get_cml_client_dep", lambda: _FakeClient("someone"))
        monkeypatch.setattr(CustomHttpRequestMiddleware, "check_tool_enabled", self._allow)

        middleware = CustomHttpRequestMiddleware()
        context = _FakeToolContext("delete_cml_lab", {"lab_id": "lab-123", "confirm": True})

        async def call_next(_ctx):
            return "ok"

        with caplog.at_level(logging.INFO, logger="cml-mcp.middleware"):
            result = await middleware.on_call_tool(context, call_next)

        assert result == "ok"
        audit_lines = [r.message for r in caplog.records if r.message.startswith("AUDIT")]
        assert len(audit_lines) == 1
        assert "tool=delete_cml_lab" in audit_lines[0]
        assert "resource=lab-123" in audit_lines[0]
        assert "outcome=success" in audit_lines[0]
        # Never leak arguments/credentials into the audit line.
        assert "confirm" not in audit_lines[0]

    async def test_acl_denial_is_logged(self, monkeypatch, caplog):
        import logging

        from fastmcp.exceptions import ToolError

        import cml_mcp.tools.dependencies as deps

        monkeypatch.setattr(deps, "get_cml_client_dep", lambda: _FakeClient("someone"))
        monkeypatch.setattr(CustomHttpRequestMiddleware, "check_tool_enabled", self._deny)

        middleware = CustomHttpRequestMiddleware()
        context = _FakeToolContext("wipe_cml_lab", {"lab_id": "lab-456"})

        async def call_next(_ctx):
            raise AssertionError("call_next should not be reached when the tool is denied")

        with caplog.at_level(logging.INFO, logger="cml-mcp.middleware"):
            with pytest.raises(ToolError):
                await middleware.on_call_tool(context, call_next)

        audit_lines = [r.message for r in caplog.records if r.message.startswith("AUDIT")]
        assert len(audit_lines) == 1
        assert "outcome=denied" in audit_lines[0]
        assert "reason=acl" in audit_lines[0]

    async def test_unauthenticated_denial_is_logged(self, monkeypatch, caplog):
        import logging

        from fastmcp.exceptions import ToolError

        import cml_mcp.tools.dependencies as deps

        def _raise_runtime_error():
            raise RuntimeError("no client")

        monkeypatch.setattr(deps, "get_cml_client_dep", _raise_runtime_error)

        middleware = CustomHttpRequestMiddleware()
        context = _FakeToolContext("get_cml_labs", {})

        async def call_next(_ctx):
            raise AssertionError("call_next should not be reached when unauthenticated")

        with caplog.at_level(logging.INFO, logger="cml-mcp.middleware"):
            with pytest.raises(ToolError):
                await middleware.on_call_tool(context, call_next)

        audit_lines = [r.message for r in caplog.records if r.message.startswith("AUDIT")]
        assert len(audit_lines) == 1
        assert "outcome=denied" in audit_lines[0]
        assert "reason=unauthenticated" in audit_lines[0]

    async def test_failure_is_logged(self, monkeypatch, caplog):
        import logging

        import cml_mcp.tools.dependencies as deps

        monkeypatch.setattr(deps, "get_cml_client_dep", lambda: _FakeClient("someone"))
        monkeypatch.setattr(CustomHttpRequestMiddleware, "check_tool_enabled", self._allow)

        middleware = CustomHttpRequestMiddleware()
        context = _FakeToolContext("delete_cml_node", {"node_id": "node-9"})

        async def call_next(_ctx):
            raise ValueError("boom")

        with caplog.at_level(logging.INFO, logger="cml-mcp.middleware"):
            with pytest.raises(ValueError):
                await middleware.on_call_tool(context, call_next)

        audit_lines = [r.message for r in caplog.records if r.message.startswith("AUDIT")]
        assert len(audit_lines) == 1
        assert "outcome=failure" in audit_lines[0]
        assert "resource=node-9" in audit_lines[0]


class TestValidateAclDataFailsClosed:
    """Malformed top-level ACL fields must invalidate the whole file, not silently degrade to a permissive default."""

    def test_non_bool_default_enabled_is_invalid(self):
        from cml_mcp.tools.middleware import _validate_acl_data

        assert _validate_acl_data({"default_enabled": "false", "users": {}}) is None

    def test_non_dict_users_is_invalid(self):
        from cml_mcp.tools.middleware import _validate_acl_data

        assert _validate_acl_data({"default_enabled": True, "users": ["admin"]}) is None

    def test_well_formed_data_is_accepted(self):
        from cml_mcp.tools.middleware import _validate_acl_data

        result = _validate_acl_data({"default_enabled": False, "users": {"admin": {}}})
        assert result == {"default_enabled": False, "users": {"admin": {"enabled_tools": None, "disabled_tools": None}}}

    def test_malformed_single_user_is_skipped_not_fatal(self):
        from cml_mcp.tools.middleware import _validate_acl_data

        result = _validate_acl_data({"default_enabled": True, "users": {"admin": {}, "bad": ["not", "a", "dict"]}})
        assert result is not None
        assert "admin" in result["users"]
        assert "bad" not in result["users"]


class TestValidateRequestHostCaseInsensitive:
    """Host/Origin comparison against CML_MCP_ALLOWED_HOSTS must be case-insensitive."""

    class _FakeHeaders:
        def __init__(self, headers: dict[str, str]):
            self._headers = headers

        def get(self, key, default=None):
            return self._headers.get(key, default)

    class _FakeRequest:
        def __init__(self, headers: dict[str, str]):
            self.headers = TestValidateRequestHostCaseInsensitive._FakeHeaders(headers)

    def test_differently_cased_host_header_is_accepted(self, monkeypatch):
        import cml_mcp.tools.middleware as middleware_mod

        monkeypatch.setattr(middleware_mod.settings, "cml_mcp_allowed_hosts", ["cml.example.com"])
        monkeypatch.setattr(middleware_mod, "get_http_request", lambda: self._FakeRequest({"host": "CML.Example.COM"}))
        # Should not raise.
        CustomHttpRequestMiddleware._validate_request_host(context=None)

    def test_differently_cased_allowed_list_entry_is_accepted(self, monkeypatch):
        import cml_mcp.tools.middleware as middleware_mod

        monkeypatch.setattr(middleware_mod.settings, "cml_mcp_allowed_hosts", ["CML.EXAMPLE.COM"])
        monkeypatch.setattr(middleware_mod, "get_http_request", lambda: self._FakeRequest({"host": "cml.example.com"}))
        CustomHttpRequestMiddleware._validate_request_host(context=None)

    def test_unlisted_host_is_still_rejected(self, monkeypatch):
        import cml_mcp.tools.middleware as middleware_mod

        monkeypatch.setattr(middleware_mod.settings, "cml_mcp_allowed_hosts", ["cml.example.com"])
        monkeypatch.setattr(middleware_mod, "get_http_request", lambda: self._FakeRequest({"host": "evil.example.com"}))
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_request_host(context=None)

    def test_differently_cased_origin_header_is_accepted(self, monkeypatch):
        import cml_mcp.tools.middleware as middleware_mod

        monkeypatch.setattr(middleware_mod.settings, "cml_mcp_allowed_hosts", ["cml.example.com"])
        monkeypatch.setattr(
            middleware_mod,
            "get_http_request",
            lambda: self._FakeRequest({"host": "cml.example.com", "origin": "https://CML.Example.COM"}),
        )
        # Should not raise.
        CustomHttpRequestMiddleware._validate_request_host(context=None)

    def test_unlisted_origin_is_still_rejected(self, monkeypatch):
        import cml_mcp.tools.middleware as middleware_mod

        monkeypatch.setattr(middleware_mod.settings, "cml_mcp_allowed_hosts", ["cml.example.com"])
        monkeypatch.setattr(
            middleware_mod,
            "get_http_request",
            lambda: self._FakeRequest({"host": "cml.example.com", "origin": "https://evil.example.com"}),
        )
        with pytest.raises(McpError):
            CustomHttpRequestMiddleware._validate_request_host(context=None)


class TestAnonDiscoveryReachableWithDefaultCmlUrl:
    """The MCP protocol handshake (initialize/ping/notifications) never touches CML, so it
    must stay reachable unauthenticated even when a default CML_URL is configured server-side
    -- that configuration is orthogonal to whether the handshake can proceed."""

    class _FakeContext:
        def __init__(self, method: str):
            self.method = method

    async def test_initialize_allowed_with_no_creds_and_default_cml_url(self, monkeypatch):
        import cml_mcp.tools.middleware as mw

        monkeypatch.setattr(mw.settings, "cml_url", AnyHttpUrl("https://cml.example.com"))
        monkeypatch.setattr(mw.settings, "cml_mcp_allowed_hosts", [])
        monkeypatch.setattr(mw, "get_http_headers", lambda include=None: {})
        monkeypatch.setattr(mw, "get_http_request", lambda: (_ for _ in ()).throw(RuntimeError()))

        sentinel = object()

        async def fake_call_next(context):
            return sentinel

        middleware = CustomHttpRequestMiddleware()
        result = await middleware.on_request(self._FakeContext("initialize"), fake_call_next)
        assert result is sentinel

    async def test_tools_list_still_requires_auth_with_default_cml_url(self, monkeypatch):
        """Unlike initialize/ping, tools/list must still require auth (or explicit
        CML_MCP_ALLOW_ANON_DISCOVERY=true) even with a default CML_URL configured."""
        import cml_mcp.tools.middleware as mw

        monkeypatch.setattr(mw.settings, "cml_url", AnyHttpUrl("https://cml.example.com"))
        monkeypatch.setattr(mw.settings, "cml_mcp_allow_anon_discovery", False)
        monkeypatch.setattr(mw.settings, "cml_mcp_allowed_hosts", [])
        # Isolate from any ambient CML_MCP_ALLOW_UNAUTHENTICATED/CML_USERNAME/CML_PASSWORD in the
        # process environment: otherwise this test could accidentally succeed via the
        # unauthenticated-fallback-credentials path instead of the "missing auth" path it means
        # to exercise.
        monkeypatch.setattr(mw.settings, "cml_mcp_allow_unauthenticated", False)
        monkeypatch.setattr(mw.settings, "cml_username", None)
        monkeypatch.setattr(mw.settings, "cml_password", None)
        monkeypatch.setattr(mw, "get_http_headers", lambda include=None: {})
        monkeypatch.setattr(mw, "get_http_request", lambda: (_ for _ in ()).throw(RuntimeError()))

        async def fake_call_next(context):
            raise AssertionError("should not reach call_next without credentials")

        middleware = CustomHttpRequestMiddleware()
        with pytest.raises(McpError):
            await middleware.on_request(self._FakeContext("tools/list"), fake_call_next)


class TestOnListToolsRespectsAclLoadFailed:
    """The anonymous-discovery branch of on_list_tools() (no request client available) must
    also fail closed when the ACL file failed to load, not just the authenticated,
    per-tool check_tool_enabled() path."""

    def setup_method(self):
        import cml_mcp.tools.middleware as mw

        self._orig_acl_data = dict(mw.acl_data)
        self._orig_failed = mw.acl_load_failed

    def teardown_method(self):
        import cml_mcp.tools.middleware as mw

        mw.acl_data.clear()
        mw.acl_data.update(self._orig_acl_data)
        mw.acl_load_failed = self._orig_failed

    async def test_anonymous_list_tools_empty_when_acl_load_failed(self, monkeypatch):
        import cml_mcp.tools.middleware as mw
        from cml_mcp.tools.dependencies import request_client

        mw.acl_data.clear()
        mw.acl_load_failed = True
        monkeypatch.setattr(mw.settings, "cml_mcp_transport", "http")
        token = request_client.set(None)
        try:
            fake_tools = [object(), object()]

            async def fake_call_next(context):
                return fake_tools

            middleware = CustomHttpRequestMiddleware()
            result = await middleware.on_list_tools(context=None, call_next=fake_call_next)
            assert result == []
        finally:
            request_client.reset(token)

    async def test_anonymous_list_tools_unfiltered_when_acl_ok(self, monkeypatch):
        import cml_mcp.tools.middleware as mw
        from cml_mcp.tools.dependencies import request_client

        mw.acl_data.clear()
        mw.acl_load_failed = False
        monkeypatch.setattr(mw.settings, "cml_mcp_transport", "http")
        token = request_client.set(None)
        try:
            fake_tools = [object(), object()]

            async def fake_call_next(context):
                return fake_tools

            middleware = CustomHttpRequestMiddleware()
            result = await middleware.on_list_tools(context=None, call_next=fake_call_next)
            assert result == fake_tools
        finally:
            request_client.reset(token)
