# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.

from production_test_framework.switch.models import Port
from production_test_framework.switch.port_sort import port_id_sort_key, sort_ports


def test_port_id_sort_key_numeric_suffix():
    assert port_id_sort_key("swp6") < port_id_sort_key("swp59")
    assert port_id_sort_key("swp1") < port_id_sort_key("swp10")


def test_port_id_sort_key_breakout_subports():
    # Subports order within their cage, and the cage number still wins: a plain
    # string sort puts swp10s0 ahead of swp1s0.
    assert port_id_sort_key("swp1s0") < port_id_sort_key("swp1s1")
    assert port_id_sort_key("swp1s1") < port_id_sort_key("swp2s0")
    assert port_id_sort_key("swp1s0") < port_id_sort_key("swp10s0")
    assert port_id_sort_key("Ethernet49/1") < port_id_sort_key("Ethernet49/2")
    # A cage with no suffix sorts ahead of any subport of the same cage.
    assert port_id_sort_key("swp1") < port_id_sort_key("swp1s0")


def test_sort_ports_breakout_order():
    ports = [
        Port(id="swp10s0"),
        Port(id="swp2s1"),
        Port(id="swp1s1"),
        Port(id="swp2s0"),
        Port(id="swp1s0"),
    ]
    assert [port.id for port in sort_ports(ports)] == ["swp1s0", "swp1s1", "swp2s0", "swp2s1", "swp10s0"]


def test_sort_ports_natural_order():
    ports = [
        Port(id="swp59"),
        Port(id="swp6"),
        Port(id="swp1"),
        Port(id="eth0"),
        Port(id="lo"),
    ]
    assert [port.id for port in sort_ports(ports)] == ["eth0", "lo", "swp1", "swp6", "swp59"]


def test_format_ports_table_row_order():
    from production_test_framework.switch.switch_status import format_ports

    ports = [Port(id="swp59"), Port(id="swp6"), Port(id="swp1")]
    text = format_ports(ports, use_color=False)
    swp1 = text.index("swp1")
    swp6 = text.index("swp6")
    swp59 = text.index("swp59")
    assert swp1 < swp6 < swp59
