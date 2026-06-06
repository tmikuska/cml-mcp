#
# This file is part of VIRL 2
# Copyright (c) 2019-2026, Cisco Systems, Inc.
# All rights reserved.
#
from __future__ import annotations

# disk image formats supported for VMs
QCOW2 = "qcow2"
QCOW = "qcow"
TAR = "tar"
TARGZ = "tar.gz"
SUPPORTED_IMAGE_FORMATS = [QCOW2, QCOW, TAR, TARGZ]

DEFAULT_LOGIN_TIMEOUT = 10  # seconds

# valid parameters for LinkCondition API schema
LINK_CONDITION_PARAMETERS = (
    "bandwidth",
    "latency",
    "delay_corr",
    "limit",
    "loss",
    "loss_corr",
    "gap",
    "duplicate",
    "duplicate_corr",
    "jitter",
    "reorder_prob",
    "reorder_corr",
    "corrupt_prob",
    "corrupt_corr",
)


def empty_link_condition() -> dict[str, None]:
    return dict.fromkeys(LINK_CONDITION_PARAMETERS)
