# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

from unittest.mock import MagicMock

import pytest

from production_test_framework.switch.models import NetworkSwitchConfig, PortBridge
from production_test_framework.switch.nvidia.nvidia_cumulus_switch import NvidiaCumulusSwitch

# NVUE interface <id> bridge domain br_default, applied revision.
_TRUNK = {"learning": "on", "untagged": 3001, "vlan": {"1": {}, "3001": {}}}


def _switch(body: dict) -> NvidiaCumulusSwitch:
    switch = NvidiaCumulusSwitch(NetworkSwitchConfig(host="h", username="u", password="p", verify_tls=False))
    switch._run_api_call = MagicMock(return_value=body)
    return switch


def test_port_bridge_calls_applied_bridge_domain_path() -> None:
    switch = _switch(_TRUNK)

    assert switch.port_bridge("swp7s0") == PortBridge(interface="swp7s0", mode="trunk", native_vlan=3001, learning=True)
    switch._run_api_call.assert_called_once_with(
        "/interface/swp7s0/bridge/domain/br_default", params={"rev": "applied"}
    )


@pytest.mark.parametrize(
    ("learning", "expected"),
    [("on", True), ("enabled", True), ("off", False), ("disabled", False), ("bogus", None)],
)
def test_parse_port_bridge_learning_both_dialects(learning: str, expected: bool | None) -> None:
    bridge = NvidiaCumulusSwitch._parse_port_bridge("swp1", {"learning": learning})

    assert bridge.learning is expected


def test_parse_port_bridge_access_port() -> None:
    bridge = NvidiaCumulusSwitch._parse_port_bridge("swp1", {"access": 10, "untagged": "none"})

    assert bridge == PortBridge(interface="swp1", mode="access", native_vlan=None, learning=None)


def test_parse_port_bridge_string_pvid() -> None:
    assert NvidiaCumulusSwitch._parse_port_bridge("swp1", {"untagged": "1"}).native_vlan == 1


def test_parse_port_bridge_not_a_member() -> None:
    assert NvidiaCumulusSwitch._parse_port_bridge("swp1", {}) == PortBridge(interface="swp1")
