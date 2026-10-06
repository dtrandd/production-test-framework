# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

from unittest.mock import MagicMock, patch

import pytest

from production_test_framework.switch.exceptions import SwitchAPIError
from production_test_framework.switch.models import NetworkSwitchConfig, PortBridge
from production_test_framework.switch.nvidia.nvidia_cumulus_switch import NvidiaCumulusSwitch

# NVUE interface <id> bridge domain br_default, applied revision
_TRUNK = {
    "learning": "on",
    "stp": {
        "admin-edge": "off",
        "auto-edge": "on",
        "bpdu-filter": "off",
        "bpdu-guard": "off",
        "network": "off",
        "restrrole": "off",
        "vlan": {},
    },
    "untagged": 3001,
    "vlan": {"1": {}, "3001": {}},
}


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


@pytest.mark.parametrize("access", [10, "10"])
def test_parse_port_bridge_access_port_uses_access_vlan(access: int | str) -> None:
    bridge = NvidiaCumulusSwitch._parse_port_bridge("swp1", {"access": access, "untagged": "none"})

    assert bridge == PortBridge(interface="swp1", mode="access", native_vlan=10, learning=None)


def test_parse_port_bridge_access_wins_over_untagged() -> None:
    bridge = NvidiaCumulusSwitch._parse_port_bridge("swp1", {"access": 10, "untagged": 3001})

    assert bridge.mode == "access"
    assert bridge.native_vlan == 10


@pytest.mark.parametrize("access", ["none", None, "auto"])
def test_parse_port_bridge_access_placeholder_is_trunk(access: object) -> None:
    bridge = NvidiaCumulusSwitch._parse_port_bridge("swp1", {"access": access, "untagged": 3001})

    assert bridge == PortBridge(interface="swp1", mode="trunk", native_vlan=3001, learning=None)


def test_parse_port_bridge_string_pvid() -> None:
    assert NvidiaCumulusSwitch._parse_port_bridge("swp1", {"untagged": "1"}).native_vlan == 1


def test_parse_port_bridge_not_a_member() -> None:
    assert NvidiaCumulusSwitch._parse_port_bridge("swp1", {}) == PortBridge(interface="swp1")


def _not_found(path: str) -> SwitchAPIError:
    return SwitchAPIError(f"API call failed: 404 {path}", 404)


def test_port_bridge_not_a_member_returns_empty() -> None:
    # NVUE answers 404 for an interface outside the bridge domain.
    switch = _switch(_TRUNK)
    switch._run_api_call.side_effect = [_not_found("bridge"), {"type": "swp"}]

    assert switch.port_bridge("swp17") == PortBridge(interface="swp17")
    switch._run_api_call.assert_called_with("/interface/swp17")


def test_port_bridge_unknown_interface_raises() -> None:
    switch = _switch(_TRUNK)
    switch._run_api_call.side_effect = [_not_found("bridge"), _not_found("interface")]

    with pytest.raises(SwitchAPIError) as error:
        switch.port_bridge("swp99")
    assert error.value.status_code == 404


@patch("production_test_framework.switch.nvidia.nvidia_cumulus_switch.requests.Session.get")
def test_port_bridge_other_error_raises_without_fallback(mock_get: MagicMock) -> None:
    mock_get.return_value = MagicMock(status_code=500, text="Internal Server Error")
    switch = NvidiaCumulusSwitch(NetworkSwitchConfig(host="h", username="u", password="p", verify_tls=False))

    with pytest.raises(SwitchAPIError) as error:
        switch.port_bridge("swp1")
    assert error.value.status_code == 500
    mock_get.assert_called_once()
