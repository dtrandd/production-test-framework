# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

from unittest.mock import MagicMock

import pytest

from production_test_framework.switch.arista.arista_eos_switch import AristaEosSwitch
from production_test_framework.switch.exceptions import SwitchAPIError
from production_test_framework.switch.models import NetworkSwitchConfig, PortBridge

_SWITCHPORT = {
    "switchports": {
        "Ethernet35": {
            "enabled": True,
            "switchportInfo": {
                "mode": "trunk",
                "accessVlanId": 1,
                "trunkingNativeVlanId": 3001,
                "trunkAllowedVlans": "ALL",
                "macLearning": True,
            },
        }
    }
}


def _switch() -> AristaEosSwitch:
    switch = AristaEosSwitch(NetworkSwitchConfig(host="h", username="u", password="p", verify_tls=False))
    switch._node = MagicMock()
    return switch


def test_port_bridge_calls_show_interfaces_switchport() -> None:
    switch = _switch()
    switch._node.run_commands.return_value = [_SWITCHPORT]

    assert switch.port_bridge("Ethernet35") == PortBridge(
        interface="Ethernet35", mode="trunk", native_vlan=3001, learning=True
    )
    switch._node.run_commands.assert_called_once_with(["show interfaces Ethernet35 switchport"])


def test_port_bridge_unknown_port_raises() -> None:
    switch = _switch()
    switch._node.run_commands.return_value = [{"switchports": {}}]

    with pytest.raises(SwitchAPIError):
        switch.port_bridge("Ethernet99")


def test_parse_port_bridge_learning_disabled() -> None:
    body = {"enabled": True, "switchportInfo": {"mode": "access", "trunkingNativeVlanId": 1, "macLearning": False}}

    assert AristaEosSwitch._parse_port_bridge("Ethernet1", body) == PortBridge(
        interface="Ethernet1", mode="access", native_vlan=1, learning=False
    )


def test_parse_port_bridge_routed_port() -> None:
    body = {"enabled": False, "switchportInfo": {"mode": "access", "macLearning": True}}

    assert AristaEosSwitch._parse_port_bridge("Ethernet1", body) == PortBridge(interface="Ethernet1")
