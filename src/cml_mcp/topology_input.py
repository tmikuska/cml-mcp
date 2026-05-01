# Copyright (c) 2025-2026  Cisco Systems, Inc.
# All rights reserved.

"""MCP topology import models with Gemini-safe border_style tool schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, create_model
from pydantic_strict_partial import create_partial_model

from cml_mcp.border_style import BorderStyleLiteral
from cml_mcp.cml.simple_webserver.schemas.annotations import (
    EllipseAnnotation,
    LineAnnotation,
    RectangleAnnotation,
    TextAnnotation,
)
from cml_mcp.cml.simple_webserver.schemas.smart_annotations import SmartAnnotationBase
from cml_mcp.cml.simple_webserver.schemas.topologies import (
    LabTopology,
    LinkTopology,
    NodeTopology,
)

_BORDER_STYLE = (
    BorderStyleLiteral,
    Field(..., description="Border style: solid, dotted, or dashed."),
)


def _annotation_with_canonical_border(model_cls: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"Mcp{model_cls.__name__}",
        __base__=model_cls,
        border_style=_BORDER_STYLE,
    )


McpTextAnnotation = _annotation_with_canonical_border(TextAnnotation)
McpRectangleAnnotation = _annotation_with_canonical_border(RectangleAnnotation)
McpEllipseAnnotation = _annotation_with_canonical_border(EllipseAnnotation)
McpLineAnnotation = _annotation_with_canonical_border(LineAnnotation)

McpTextAnnotationUpdate = create_partial_model(model=McpTextAnnotation, required_fields=["type"])
McpRectangleAnnotationUpdate = create_partial_model(
    model=McpRectangleAnnotation, required_fields=["type"]
)
McpEllipseAnnotationUpdate = create_partial_model(model=McpEllipseAnnotation, required_fields=["type"])
McpLineAnnotationUpdate = create_partial_model(model=McpLineAnnotation, required_fields=["type"])

McpAnnotationUpdate = Annotated[
    McpTextAnnotationUpdate
    | McpRectangleAnnotationUpdate
    | McpEllipseAnnotationUpdate
    | McpLineAnnotationUpdate,
    Field(discriminator="type"),
]


class SmartAnnotationInput(SmartAnnotationBase):
    border_style: BorderStyleLiteral = Field(
        default="solid",
        description="Border style: solid, dotted, or dashed.",
    )


class TopologyInput(BaseModel, extra="forbid"):
    """Topology import payload for MCP tools (canonical border_style values)."""

    nodes: list[NodeTopology] = Field(...)
    links: list[LinkTopology] = Field(...)
    lab: LabTopology = Field(...)
    annotations: list[McpAnnotationUpdate] = Field(default_factory=list)
    smart_annotations: list[SmartAnnotationInput] = Field(default_factory=list)
