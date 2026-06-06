#
# This file is part of VIRL 2
# Copyright (c) 2019-2026, Cisco Systems, Inc.
# All rights reserved.
#
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

from simple_common.schemas.constants import DEFAULT_LOGIN_TIMEOUT
from simple_common.schemas.enums import AuthMethod, ComputeState, OptInStatus
from simple_common.utils import get_product_version, is_production


def _reject_newlines(v: str) -> str:
    if "\n" in v or "\r" in v:
        raise ValueError("String must not contain newline characters")
    return v


OneLineStr = Annotated[str, AfterValidator(_reject_newlines)]
MultiLineStr = Annotated[str, Field()]

UUID4_REG = r"^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$"
UUID4Type = Annotated[
    OneLineStr,
    Field(
        description="A UUID4",
        examples=["90f84e38-a71c-4d57-8d90-00fa8a197385"],
        pattern=UUID4_REG,
    ),
]

Timeout = Annotated[
    int,
    Field(
        default=DEFAULT_LOGIN_TIMEOUT,
        description="Timeout in seconds for authentication requests.",
        ge=1,
        le=60,
        examples=[30],
    ),
]

OAuth2Timeout = Annotated[
    int,
    Field(
        default=30,
        description="HTTP timeout in seconds for provider calls.",
        ge=1,
        le=60,
        examples=[30],
    ),
]

RESOURCE_POOL_DESCRIPTION = "Resource pool or template ID for new user accounts."

DEFAULT_AUTH_METHOD = AuthMethod.LOCAL
DEFAULT_COMPUTE_STATE = ComputeState.READY
DEFAULT_OPT_IN = OptInStatus.UNSET if is_production() else OptInStatus.ACCEPTED
DEFAULT_OPT_IN_VERSION = "" if is_production() else get_product_version()
DEFAULT_TOKEN_LIFETIME = 86400
