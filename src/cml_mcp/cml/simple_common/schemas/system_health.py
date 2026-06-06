#
# This file is part of VIRL 2
# Copyright (c) 2019-2026, Cisco Systems, Inc.
# All rights reserved.
#
"""System health models shared by REST ``/system_health`` and websocket push."""

from __future__ import annotations

from pydantic import BaseModel, Field

from simple_common.schemas.enums import ComputeState
from simple_common.schemas.types import UUID4Type


class ComputeHealth(BaseModel, extra="forbid"):
    kvm_vmx_enabled: bool | None = Field(
        ..., description="System supports running KVM virtual machines"
    )
    enough_cpus: bool | None = Field(..., description="Host has at least 4 CPUs")
    lld_connected: bool = Field(..., description="Node handling service is connected")
    lld_synced: bool | None = Field(..., description="Node and link states are in sync")
    libvirt: bool | None = Field(..., description="Libvirt VM service is ready")
    fabric: bool | None = Field(..., description="Link fabric service is ready")
    device_mux: bool | None = Field(..., description="Console service is ready")
    docker_shim: bool | None = Field(..., description="Container service is ready")
    cpu_overload: bool = Field(..., description="CPU overload prevents node starts")
    memory_overload: bool = Field(..., description="RAM overload prevents node starts")
    disk_overload: bool = Field(..., description="Disk overload prevents node starts")
    refplat_images_available: bool | None = Field(
        ..., description="Images stored on controller are accessible on this compute"
    )
    valid: bool | None = Field(..., description="The compute host is in a valid state")
    is_controller: bool = Field(..., description="This host is the controller")
    admission_state: ComputeState = Field(...)
    hostname: str = Field(..., description="A Linux hostname (not FQDN).")


class ControllerHealth(BaseModel, extra="forbid"):
    core_connected: bool | None = Field(..., description="Core service is connected")
    airhandler: bool | None = Field(..., description="Wireless link service is ready")
    dispatcher: bool | None = Field(..., description="Console/PCAP service is ready")
    ipsnooper: bool | None = Field(..., description="Layer3 address service is ready")
    pcapdemux: bool | None = Field(..., description="PCAP analyzer service is ready")
    nodes_loaded: bool = Field(..., description="Node definitions have been loaded")
    images_loaded: bool = Field(..., description="Image definitions have been loaded")
    valid: bool = Field(..., description="The controller is in a valid state.")


class SystemHealth(BaseModel, extra="forbid"):
    valid: bool | None = Field(..., description="Indicates if the system is healthy.")
    computes: dict[UUID4Type, ComputeHealth] = Field(
        ..., description="Compute hosts health statistics."
    )
    is_licensed: bool | None = Field(..., description="Indicates if the system is licensed.")
    is_enterprise: bool = Field(..., description="Indicates if the system is enterprise.")
    controller: ControllerHealth = Field(..., description="Controller health statistics.")
