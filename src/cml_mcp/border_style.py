# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""Legacy CML annotation border_style wire values and alias conversion."""

from __future__ import annotations

LEGACY_BORDER_STYLES = ("", "2,2", "4,2")

# Optional aliases LLM clients may send; mapped to legacy wire on write.
_CANONICAL_TO_LEGACY: dict[str, str] = {
    "solid": "",
    "dotted": "2,2",
    "dashed": "4,2",
}


def border_style_for_api(value: str) -> str:
    """Serialize border_style for legacy CML REST API requests."""
    if value in _CANONICAL_TO_LEGACY:
        return _CANONICAL_TO_LEGACY[value]
    if value in LEGACY_BORDER_STYLES:
        return value
    raise ValueError(
        f"Invalid border_style {value!r}; expected {LEGACY_BORDER_STYLES} "
        f"or solid/dotted/dashed aliases"
    )


def border_style_from_api(value: str) -> str:
    """Normalize border_style from legacy CML REST API responses."""
    if value in _CANONICAL_TO_LEGACY:
        return _CANONICAL_TO_LEGACY[value]
    return value
