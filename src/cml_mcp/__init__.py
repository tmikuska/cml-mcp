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

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

_cml_path = Path(__file__).parent / "cml"

# Detect whether real CML packages (simple_common, simple_webserver) are installed
# in the environment. This check runs BEFORE we potentially add bundled schemas to
# sys.path, so find_spec only finds the real packages when they are truly installed.
_HAS_CML_PACKAGES = importlib.util.find_spec("simple_common") is not None and importlib.util.find_spec("simple_webserver") is not None


class _SchemaAliasLoader(importlib.abc.Loader):
    """Loads a module by importing its real CML counterpart and copying its namespace."""

    def __init__(self, real_name: str) -> None:
        self._real_name = real_name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        real_mod = importlib.import_module(self._real_name)
        module.__dict__.update(real_mod.__dict__)
        if hasattr(real_mod, "__path__"):
            module.__path__ = list(real_mod.__path__)


class _CMLSchemaFinder(importlib.abc.MetaPathFinder):
    """Redirect cml_mcp.cml.{simple_common,simple_webserver} to real installed packages.

    When cml-mcp is installed inside a CML environment that already has the real
    simple_common and simple_webserver packages, this finder intercepts imports of
    the bundled copies (under cml_mcp.cml.*) and transparently redirects them to
    the real packages. This eliminates the need for build-time import rewriting.
    """

    _REDIRECTS = {
        "cml_mcp.cml.simple_common": "simple_common",
        "cml_mcp.cml.simple_webserver": "simple_webserver",
    }

    def find_spec(self, fullname, path, target=None):
        for prefix, real_prefix in self._REDIRECTS.items():
            if fullname == prefix or fullname.startswith(prefix + "."):
                real_name = real_prefix + fullname[len(prefix):]
                return importlib.machinery.ModuleSpec(fullname, _SchemaAliasLoader(real_name))
        return None


if _HAS_CML_PACKAGES:
    sys.meta_path.insert(0, _CMLSchemaFinder())
else:
    # Standalone mode: add the bundled schemas directory to sys.path so that
    # internal cross-references (e.g. "from simple_webserver.schemas.X import Y"
    # inside the bundled schema files) resolve correctly.
    if str(_cml_path) not in sys.path:
        sys.path.insert(0, str(_cml_path))


from cml_mcp.settings import settings  # noqa: E402

__all__ = ["settings"]
