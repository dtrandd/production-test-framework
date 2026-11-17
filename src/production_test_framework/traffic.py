# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

"""
Send and capture traffic on hosts, to check which interfaces a frame or packet reaches.

Commands run on a :class:`~production_test_framework.host.Host` with ``sudo -n``; the hosts
need ``tcpdump``, ``python3`` and ``iputils-arping``. tcpdump streams each capture back as a
pcap, which dpkt decodes here. Frames are built here and written to an AF_PACKET socket on
the host, so the hosts need no packet library.
"""

import base64
import io
import ipaddress
import logging
import shlex
import struct
import subprocess
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import dpkt

from production_test_framework.host import Host
from production_test_framework.switch.mac import normalize_mac

__all__ = [
    "BROADCAST_MAC",
    "Capture",
    "CaptureError",
    "Frame",
    "arp_frame",
    "arping",
    "ethernet_frame",
    "parse_pcap",
    "send_frames",
    "send_udp",
]

_log = logging.getLogger(__name__)

BROADCAST_MAC = "ff:ff:ff:ff:ff:ff"
_TPID_8021Q = 0x8100
_ETHERTYPE_ARP = 0x0806
_MIN_FRAME = 60
_STOP_TIMEOUT_S = 10.0

_SEND = (
    "import base64, socket, sys\n"
    "s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)\n"
    "s.bind((sys.argv[1], 0))\n"
    "for line in sys.stdin.read().split():\n"
    "    s.send(base64.b64decode(line))\n"
)

_SEND_UDP = (
    "import socket, sys\n"
    "dest, port, interface = sys.argv[1], int(sys.argv[2]), sys.argv[3]\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)\n"
    "if interface:\n"
    "    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())\n"
    "s.sendto(sys.stdin.read().encode(), (dest, port))\n"
)


class CaptureError(RuntimeError):
    """A capture could not be started, or frames could not be sent."""


@dataclass(frozen=True)
class Frame:
    """One captured packet. MACs, ethertype and VLANs are empty on interfaces without Ethernet."""

    interface: str
    src_mac: str
    dst_mac: str
    ethertype: int | None
    vlans: tuple[int, ...] = ()
    data: bytes = b""

    def carries(self, marker: bytes) -> bool:
        return marker in self.data

    def __str__(self) -> str:
        if self.ethertype is None:
            return f"{self.interface}: {len(self.data)} bytes without an Ethernet header"
        tags = f" vlans {list(self.vlans)}" if self.vlans else ""
        return f"{self.interface}: {self.src_mac} > {self.dst_mac} ethertype {self.ethertype:#06x}{tags}"


class _Tcpdump:
    """tcpdump on one interface, streaming its pcap back over its own ssh connection.

    Closing its stdin stops it: the host then ends tcpdump cleanly, so it neither lingers
    nor drops what it has yet to send.
    """

    def __init__(self, host: Host, interface: str, bpf_filter: str, max_duration_s: int):
        tcpdump = ["timeout", str(max_duration_s), "tcpdump", "-i", interface, "-nn", "-U", "-w", "-"]
        tcpdump += [bpf_filter] if bpf_filter else []
        script = f"{shlex.join(tcpdump)} & pid=$!; cat >/dev/null; kill $pid 2>/dev/null; wait $pid"
        self.interface = interface
        self.argv = host.argv(["sudo", "-n", "sh", "-c", script], shared_connection=False)
        self.ready = threading.Event()
        self.pcap = bytearray()
        self.stderr: list[str] = []
        self._proc: subprocess.Popen | None = None
        self._readers: list[threading.Thread] = []

    def start(self) -> None:
        self._proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._readers = [
            threading.Thread(target=self._read_pcap, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def listening(self) -> bool:
        return any("listening on" in line for line in self.stderr)

    def stop(self) -> bytes:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=_STOP_TIMEOUT_S)
            except OSError, subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=_STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    self._proc.wait()
        for reader in self._readers:
            reader.join(timeout=_STOP_TIMEOUT_S)
        return bytes(self.pcap)

    def _read_pcap(self) -> None:
        for chunk in iter(lambda: self._proc.stdout.read1(65536), b""):
            self.pcap += chunk

    def _read_stderr(self) -> None:
        for raw in self._proc.stderr:
            self.stderr.append(raw.decode(errors="replace").rstrip())
            if self.listening():
                self.ready.set()
        self.ready.set()


@dataclass
class Capture:
    """tcpdump on some interfaces of one host, from :meth:`start` until :meth:`stop`.

    Use it as a context manager or call :meth:`start` and :meth:`stop`. *bpf_filter* is a
    tcpdump filter expression; empty captures everything. tcpdump on the host gives up by
    itself after *max_duration_s*, in case the connection is lost before :meth:`stop`.
    """

    host: Host
    interfaces: Sequence[str]
    bpf_filter: str = ""
    ready_timeout: float = 15.0
    max_duration_s: int = 600
    frames: list[Frame] = field(default_factory=list, init=False)
    pcaps: dict[str, bytes] = field(default_factory=dict, init=False)
    _running: list[_Tcpdump] = field(default_factory=list, init=False)

    def start(self) -> Capture:
        if not self.interfaces:
            raise CaptureError(f"no interfaces to capture on {self.host}")
        self._running = [_Tcpdump(self.host, i, self.bpf_filter, self.max_duration_s) for i in self.interfaces]
        for tcpdump in self._running:
            tcpdump.start()
        for tcpdump in self._running:
            tcpdump.ready.wait(self.ready_timeout)
            if not tcpdump.listening():
                errors = {t.interface: t.stderr for t in self._running}
                self.stop()
                raise CaptureError(f"tcpdump on {self.host} {list(self.interfaces)} not listening: {errors}")
        _log.debug("capturing on %s %s with filter %r", self.host, list(self.interfaces), self.bpf_filter)
        return self

    def stop(self) -> list[Frame]:
        """Stop tcpdump and return what it captured; also kept in :attr:`frames`, the raw pcaps in :attr:`pcaps`."""
        if not self._running:
            return self.frames
        self.pcaps = {t.interface: t.stop() for t in self._running}
        self._running = []
        self.frames = [frame for interface, pcap in self.pcaps.items() for frame in parse_pcap(pcap, interface)]
        return self.frames

    def carrying(self, marker: bytes) -> list[Frame]:
        return [f for f in self.frames if f.carries(marker)]

    def __enter__(self) -> Capture:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def parse_pcap(pcap: bytes, interface: str) -> list[Frame]:
    """The packets in *pcap*, as captured on *interface*; a packet cut short at the end is kept as far as it got."""
    try:
        reader = dpkt.pcap.Reader(io.BytesIO(pcap))
    except ValueError, dpkt.NeedData, dpkt.UnpackError:
        return []
    ethernet = reader.datalink() == dpkt.pcap.DLT_EN10MB
    frames: list[Frame] = []
    try:
        for _timestamp, buf in reader:
            frames.append(_ethernet_frame(interface, buf) if ethernet else Frame(interface, "", "", None, data=buf))
    except ValueError, dpkt.NeedData, dpkt.UnpackError:
        _log.debug("pcap from %s ends in a truncated packet", interface)
    return frames


def _ethernet_frame(interface: str, buf: bytes) -> Frame:
    try:
        eth = dpkt.ethernet.Ethernet(buf)
    except dpkt.NeedData, dpkt.UnpackError:
        return Frame(interface, "", "", None, data=buf)
    tags = getattr(eth, "vlan_tags", None) or []
    return Frame(
        interface=interface,
        src_mac=normalize_mac(eth.src.hex()),
        dst_mac=normalize_mac(eth.dst.hex()),
        ethertype=tags[-1].type if tags else eth.type,
        vlans=tuple(tag.id for tag in tags),
        data=buf,
    )


def ethernet_frame(dst: str, src: str, payload: bytes, *, ethertype: int = 0x88B5, vlans: Iterable[int] = ()) -> bytes:
    """An Ethernet frame with one 802.1Q (0x8100) tag per entry in *vlans*, outermost first.

    Built by hand rather than with dpkt, which writes the outer tag of a pair as 802.1ad.
    """
    frame = _mac_bytes(dst) + _mac_bytes(src)
    for vlan in vlans:
        frame += struct.pack("!HH", _TPID_8021Q, vlan & 0x0FFF)
    frame += struct.pack("!H", ethertype) + payload
    return frame.ljust(_MIN_FRAME, b"\x00")


def arp_frame(
    src_mac: str,
    src_ip: str,
    target_ip: str,
    *,
    dst: str = BROADCAST_MAC,
    reply: bool = False,
    trailer: bytes = b"",
) -> bytes:
    """An ARP request (or reply) from *src_mac*/*src_ip*; *target_ip* == *src_ip* makes it gratuitous.

    *trailer* goes in the padding after the ARP body, e.g. a marker to find the frame in a capture.
    """
    arp = dpkt.arp.ARP(
        op=dpkt.arp.ARP_OP_REPLY if reply else dpkt.arp.ARP_OP_REQUEST,
        sha=_mac_bytes(src_mac),
        spa=ipaddress.IPv4Address(src_ip).packed,
        tha=_mac_bytes(dst) if reply else b"\x00" * 6,
        tpa=ipaddress.IPv4Address(target_ip).packed,
    )
    return ethernet_frame(dst, src_mac, bytes(arp) + trailer, ethertype=_ETHERTYPE_ARP)


def send_frames(host: Host, interface: str, frames: Sequence[bytes]) -> None:
    """Write raw *frames* out of *interface* on *host*."""
    lines = "\n".join(base64.b64encode(f).decode() for f in frames)
    result = host.run(["sudo", "-n", "python3", "-c", _SEND, interface], stdin_data=lines)
    if not result.success:
        raise CaptureError(f"sending {len(frames)} frame(s) on {host} {interface} failed: {result.stderr}")


def send_udp(host: Host, dest_ip: str, port: int, payload: str, *, interface: str | None = None) -> bool:
    """Send one UDP datagram from *host*, out of *interface* when given; True when sent, not that it arrived.

    Broadcast and multicast destinations are allowed.
    """
    command = ["python3", "-c", _SEND_UDP, dest_ip, str(port), interface or ""]
    if interface:
        command = ["sudo", "-n", *command]
    return host.run(command, timeout=15, stdin_data=payload).success


def arping(host: Host, interface: str, ip: str, *, count: int = 2, timeout: int = 3) -> bool:
    """True when *ip* answers ARP on *interface* of *host* (iputils arping)."""
    command = ["sudo", "-n", "arping", "-c", str(count), "-w", str(timeout), "-I", interface, ip]
    return host.run(command, timeout=timeout + 15).success


def _mac_bytes(mac: str) -> bytes:
    normalized = normalize_mac(mac)
    if not normalized:
        raise ValueError(f"not a MAC address: {mac!r}")
    return bytes.fromhex(normalized.replace(":", ""))
