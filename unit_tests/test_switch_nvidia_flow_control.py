# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

from unittest.mock import MagicMock

import pytest

from production_test_framework.switch.exceptions import SwitchAPIError
from production_test_framework.switch.models import FlowControl, NetworkSwitchConfig
from production_test_framework.switch.nvidia.nvidia_cumulus_switch import NvidiaCumulusSwitch

_ROCE_PATH = "/qos/roce"
_PFC_PATH = "/qos/pfc/default-global"

_ROCE_ON = {"enable": "on", "mode": "lossless"}
_PFC = {"switch-priority": {"3": {}}, "cable-length": 10}

# Captured from Cumulus 5.12 with no flow control configured: RoCE answers, the PFC profile 404s.
_ROCE_UNSET = {"enable": "off"}


def _not_found(path: str) -> SwitchAPIError:
    return SwitchAPIError(f"API call failed: 404 {path}", 404)


def _switch(responses: dict[str, dict | SwitchAPIError]) -> NvidiaCumulusSwitch:
    """A switch whose GETs answer from responses by path; a SwitchAPIError value is raised."""
    switch = NvidiaCumulusSwitch(NetworkSwitchConfig(host="h", username="u", password="p", verify_tls=False))

    def get(path: str, params: dict | None = None) -> dict:
        response = responses[path]
        if isinstance(response, SwitchAPIError):
            raise response
        return response

    switch._run_api_call = MagicMock(side_effect=get)
    return switch


def test_flow_control_reads_applied_revision() -> None:
    switch = _switch({_ROCE_PATH: _ROCE_ON, _PFC_PATH: _PFC})

    assert switch.flow_control == FlowControl(roce_lossless=True, pfc_profile=True)
    switch._run_api_call.assert_any_call(_ROCE_PATH, params={"rev": "applied"})
    switch._run_api_call.assert_any_call(_PFC_PATH, params={"rev": "applied"})


def test_flow_control_nothing_configured() -> None:
    switch = _switch({_ROCE_PATH: _ROCE_UNSET, _PFC_PATH: _not_found(_PFC_PATH)})

    assert switch.flow_control == FlowControl(roce_lossless=False, pfc_profile=False)


def test_flow_control_roce_not_found_and_empty_profile() -> None:
    switch = _switch({_ROCE_PATH: _not_found(_ROCE_PATH), _PFC_PATH: {}})

    assert switch.flow_control == FlowControl(roce_lossless=False, pfc_profile=False)


def test_flow_control_roce_without_profile() -> None:
    switch = _switch({_ROCE_PATH: _ROCE_ON, _PFC_PATH: _not_found(_PFC_PATH)})

    assert switch.flow_control == FlowControl(roce_lossless=True, pfc_profile=False)


def test_flow_control_other_error_raises() -> None:
    error = SwitchAPIError("API call failed: 500", 500)
    switch = _switch({_ROCE_PATH: error, _PFC_PATH: _PFC})

    with pytest.raises(SwitchAPIError) as raised:
        _ = switch.flow_control
    assert raised.value.status_code == 500


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"enable": "on", "mode": "lossless"}, True),
        ({"state": "enabled", "mode": "lossless"}, True),  # 5.15+ dialect
        ({"enable": "on"}, True),  # NVUE defaults the mode to lossless
        ({"enable": "on", "mode": "lossy"}, False),  # RoCE without PFC
        ({"enable": "off", "mode": "lossless"}, False),
        ({"state": "disabled"}, False),
        ({"mode": None}, False),
        ({}, False),
    ],
)
def test_roce_lossless(body: dict, expected: bool) -> None:
    assert NvidiaCumulusSwitch._roce_lossless(body) is expected
