#
# This file is part of VIRL 2
# Copyright (c) 2019-2026, Cisco Systems, Inc.
# All rights reserved.
#
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Body
from pydantic import BaseModel, Field, model_validator

from simple_webserver.schemas.common import UUID4Type
from simple_webserver.schemas.pcap import PCAPConfigBase


class WirelessPcapConfig(PCAPConfigBase, extra="forbid"):
    """Request body for starting a wireless PCAP capture."""


    link_capture_key: UUID4Type = Field(
        ..., description="Unique identifier for this wireless PCAP capture (node_id)."
    )
    mac: str | None = Field(
        default=None,
        description="Optional MAC address or interface identifier for capture.",
        min_length=1,
        max_length=128,
        examples=["00:11:22:33:44:55"],
    )
    node_id: UUID4Type = Field(
        ...,
        description="Node ID (same as capture key) as capture is node-specific.",
    )


class WirelessPcapStart(WirelessPcapConfig):
    @model_validator(mode="after")
    def check_at_least_one(self):
        if self.maxpackets is None and self.maxtime is None:
            raise ValueError("Either 'maxpackets' or 'maxtime' must be specified")
        return self

    @model_validator(mode="after")
    def check_capture_key_is_node_id(self):
        if self.link_capture_key != self.node_id:
            raise ValueError("Packet capture key and node_id must match")
        return self


WirelessPcapStartBody = Annotated[
    WirelessPcapStart,
    Body(description="Send parameters in JSON format to start Wireless PCAP."),
]


class WirelessPcapStatusResponse(BaseModel, extra="forbid"):
    config: WirelessPcapConfig | None = Field(
        None,
        description="The configuration of the PCAP. Empty when PCAP is not running",
    )
    starttime: datetime | None = Field(
        None, description="The start time of the PCAP. None when PCAP is not running"
    )
    packetscaptured: int | None = Field(
        None,
        description="The number of packets captured. None when PCAP is not running",
        ge=0,
    )


WirelessPcapActionResponse = Annotated[
    str,
    Field(description="Result message from the wireless PCAP operation."),
]
