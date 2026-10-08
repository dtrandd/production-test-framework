# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

import pytest

from production_test_framework.switch.arista.arista_eos_switch import AristaEosSwitch
from production_test_framework.switch.models import NetworkSwitchConfig


def test_flow_control_not_supported() -> None:
    switch = AristaEosSwitch(NetworkSwitchConfig(host="h", username="u", password="p", verify_tls=False))

    with pytest.raises(NotImplementedError):
        _ = switch.flow_control
