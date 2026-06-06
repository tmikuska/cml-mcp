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
Tests for cml_mcp.settings -- in particular the comma-separated env-var parsing for
list-typed settings, which the documentation promises but pydantic-settings does not support
out of the box (it normally requires JSON-array syntax for list env vars).
"""

from cml_mcp.settings import Settings


class TestCommaSeparatedListSettings:
    def test_cml_allowed_urls_accepts_comma_separated_string(self, monkeypatch):
        monkeypatch.setenv("CML_MCP_TRANSPORT", "http")
        monkeypatch.setenv("CML_ALLOWED_URLS", "https://cml1.example.com,https://cml2.example.com")
        s = Settings()
        assert [str(u) for u in s.cml_allowed_urls] == ["https://cml1.example.com/", "https://cml2.example.com/"]

    def test_cml_mcp_allowed_hosts_accepts_comma_separated_string(self, monkeypatch):
        monkeypatch.setenv("CML_MCP_TRANSPORT", "http")
        monkeypatch.setenv("CML_MCP_ALLOWED_HOSTS", "cml.example.com, localhost:8443")
        s = Settings()
        assert s.cml_mcp_allowed_hosts == ["cml.example.com", "localhost:8443"]

    def test_empty_list_settings_default_to_empty(self, monkeypatch):
        monkeypatch.setenv("CML_MCP_TRANSPORT", "http")
        monkeypatch.delenv("CML_ALLOWED_URLS", raising=False)
        monkeypatch.delenv("CML_MCP_ALLOWED_HOSTS", raising=False)
        s = Settings()
        assert s.cml_allowed_urls == []
        assert s.cml_mcp_allowed_hosts == []

    def test_list_value_passed_programmatically_still_works(self):
        s = Settings(cml_mcp_transport="http", cml_mcp_allowed_hosts=["cml.example.com"])
        assert s.cml_mcp_allowed_hosts == ["cml.example.com"]
