# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

import pytest

from production_test_framework.grafana import Panel, panel_queries


def _loki_panel(target: dict) -> Panel:
    return Panel(
        title="p", type="stat", datasource={"type": "loki", "uid": "loki"}, targets=(target,), row="", collapsed=False
    )


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ({"expr": "x"}, "range"),
        ({"expr": "x", "instant": True}, "instant"),
        ({"expr": "x", "instant": False}, "range"),
        ({"expr": "x", "instant": True, "queryType": "range"}, "range"),
    ],
)
def test_loki_query_type(target: dict, expected: str) -> None:
    assert panel_queries(_loki_panel(target))[0]["queryType"] == expected
