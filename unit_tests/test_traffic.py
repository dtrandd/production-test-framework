# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

"""Unit tests for sending and capturing traffic on hosts."""

import base64
import io
import sys

import dpkt
import pytest

from production_test_framework.ssh import CommandResult
from production_test_framework.traffic import (
    BROADCAST_MAC,
    Capture,
    CaptureError,
    _Tcpdump,
    arp_frame,
    arping,
    ethernet_frame,
    parse_pcap,
    send_frames,
    send_udp,
)

SRC = "2c:9d:90:82:da:42"

# Stands in for `ssh host sudo tcpdump -w -`: says it is listening, streams the pcap, then waits for stdin to close.
_FAKE_TCPDUMP = (
    "import sys\n"
    "sys.stderr.write('tcpdump: listening on eth1, link-type EN10MB\\n'); sys.stderr.flush()\n"
    "sys.stdout.buffer.write(bytes.fromhex(sys.argv[1])); sys.stdout.flush()\n"
    "sys.stdin.read()\n"
)
_FAILING_TCPDUMP = "import sys\nsys.stderr.write('tcpdump: eth9: No such device exists\\n')\nsys.exit(1)\n"


def _pcap(*frames: bytes, linktype: int = dpkt.pcap.DLT_EN10MB) -> bytes:
    out = io.BytesIO()
    writer = dpkt.pcap.Writer(out, linktype=linktype)
    for frame in frames:
        writer.writepkt(frame, ts=1)
    return out.getvalue()


class FakeHost:
    """Answers run() from a script of results, and runs captures as a local stand-in process."""

    def __init__(self, *results: CommandResult, default: CommandResult | None = None, capture: str = ""):
        self.results = list(results)
        self.default = default if default is not None else CommandResult(0, "", "")
        self.capture = capture or _FAKE_TCPDUMP
        self.pcap_hex = ""
        self.calls: list[tuple[list[str], str | None]] = []
        self.captures: list[tuple[list[str], bool]] = []

    def run(self, command, timeout=60, stdin_data=None):
        self.calls.append((list(command), stdin_data))
        return self.results.pop(0) if self.results else self.default

    def argv(self, command, *, shared_connection=True):
        self.captures.append((list(command), shared_connection))
        return [sys.executable, "-c", self.capture, self.pcap_hex]

    def __str__(self):
        return "fake-host"


class TestParsePcap:
    def test_ethernet_fields(self):
        frame = parse_pcap(_pcap(ethernet_frame(BROADCAST_MAC, SRC, b"marker-1")), "eth1")[0]
        assert (frame.interface, frame.src_mac, frame.dst_mac, frame.ethertype, frame.vlans) == (
            "eth1",
            SRC,
            BROADCAST_MAC,
            0x88B5,
            (),
        )
        assert frame.carries(b"marker-1") and not frame.carries(b"marker-2")

    def test_double_tag_reports_both_vlans_and_the_inner_ethertype(self):
        frame = parse_pcap(_pcap(ethernet_frame(BROADCAST_MAC, SRC, b"x", vlans=[1, 3002])), "eth1")[0]
        assert (frame.vlans, frame.ethertype) == ((1, 3002), 0x88B5)

    def test_packets_without_an_ethernet_header_are_kept(self):
        ip_header = bytes.fromhex("4500005400004000400126970a0000020a000014")
        frame = parse_pcap(_pcap(ip_header + b"marker", linktype=dpkt.pcap.DLT_RAW), "tailscale0")[0]
        assert (frame.src_mac, frame.ethertype, frame.vlans) == ("", None, ())
        assert frame.carries(b"marker")

    @pytest.mark.parametrize("pcap", [b"", b"not a pcap"])
    def test_no_pcap_has_no_frames(self, pcap):
        assert parse_pcap(pcap, "eth1") == []


class TestFrames:
    def test_untagged_frame_layout(self):
        frame = ethernet_frame("02:00:00:00:00:01", SRC, b"x", ethertype=0x88B5)
        assert frame[:14] == bytes.fromhex("0200000000012c9d9082da4288b5")
        assert frame[14:15] == b"x" and len(frame) == 60

    def test_double_tag_is_two_8021q_tags_outermost_first(self):
        frame = ethernet_frame(BROADCAST_MAC, SRC, b"x", vlans=[100, 200])
        assert frame[12:22] == bytes.fromhex("81000064810000c888b5")

    def test_arp_request_body(self):
        arp = dpkt.arp.ARP(arp_frame(SRC, "10.0.0.2", "10.0.0.20")[14:])
        assert (arp.op, arp.sha.hex(), arp.spa, arp.tpa) == (
            dpkt.arp.ARP_OP_REQUEST,
            SRC.replace(":", ""),
            bytes([10, 0, 0, 2]),
            bytes([10, 0, 0, 20]),
        )

    def test_arp_trailer_follows_the_arp_body(self):
        frame = arp_frame(SRC, "10.0.0.2", "10.0.0.2", trailer=b"marker")
        assert frame[42:48] == b"marker" and len(frame) == 60

    def test_rejects_a_bad_mac(self):
        with pytest.raises(ValueError):
            ethernet_frame("not-a-mac", SRC, b"x")


class TestCapture:
    def test_streams_each_interface_over_its_own_connection(self):
        host = FakeHost()
        host.pcap_hex = _pcap(ethernet_frame(BROADCAST_MAC, SRC, b"marker-1")).hex()
        with Capture(host, ["eth1", "eth2"], "ether proto 0x88b5", ready_timeout=10) as capture:
            pass
        assert [shared for _, shared in host.captures] == [False, False]
        command = host.captures[0][0]
        assert command[:4] == ["sudo", "-n", "sh", "-c"]
        assert command[4].startswith("timeout 600 tcpdump -i eth1 -nn -U -w - 'ether proto 0x88b5' & ")
        assert [f.interface for f in capture.carrying(b"marker-1")] == ["eth1", "eth2"]
        assert set(capture.pcaps) == {"eth1", "eth2"}

    def test_tcpdump_that_never_listens_raises(self):
        host = FakeHost(capture=_FAILING_TCPDUMP)
        with pytest.raises(CaptureError, match="No such device"):
            Capture(host, ["eth9"], ready_timeout=10).start()

    def test_no_interfaces_is_an_error(self):
        with pytest.raises(CaptureError):
            Capture(FakeHost(), []).start()

    def test_stop_closes_stdin_so_tcpdump_ends_cleanly(self):
        host = FakeHost()
        tcpdump = _Tcpdump(host, "eth1", "", 600)
        tcpdump.start()
        assert tcpdump.ready.wait(10)
        tcpdump.stop()
        assert tcpdump._proc.returncode == 0

    def test_stop_before_start_returns_nothing(self):
        host = FakeHost()
        assert Capture(host, ["eth1"]).stop() == []
        assert host.captures == []


class TestSending:
    def test_send_frames_writes_base64_lines_to_a_raw_socket(self):
        host = FakeHost()
        frames = [ethernet_frame(BROADCAST_MAC, SRC, b"a"), ethernet_frame(BROADCAST_MAC, SRC, b"b")]
        send_frames(host, "eth1", frames)
        command, stdin = host.calls[0]
        assert command[:4] == ["sudo", "-n", "python3", "-c"] and command[-1] == "eth1"
        assert [base64.b64decode(line) for line in stdin.split()] == frames

    def test_send_frames_failure_raises(self):
        with pytest.raises(CaptureError, match="eth1"):
            send_frames(FakeHost(CommandResult(1, "", "Operation not permitted")), "eth1", [b"x" * 60])

    def test_send_udp_out_of_an_interface_needs_root(self):
        host = FakeHost()
        assert send_udp(host, "10.0.0.255", 47000, "nonce", interface="eth1")
        command, stdin = host.calls[0]
        assert command[:4] == ["sudo", "-n", "python3", "-c"]
        assert command[-3:] == ["10.0.0.255", "47000", "eth1"] and stdin == "nonce"

    def test_send_udp_without_an_interface_runs_unprivileged(self):
        host = FakeHost()
        send_udp(host, "10.0.0.20", 47000, "nonce")
        assert host.calls[0][0][0] == "python3" and host.calls[0][0][-1] == ""

    def test_arping_uses_iputils_flags(self):
        host = FakeHost(CommandResult(1, "", ""))
        assert not arping(host, "eth1", "10.0.0.20")
        assert host.calls[0][0] == ["sudo", "-n", "arping", "-c", "2", "-w", "3", "-I", "eth1", "10.0.0.20"]
