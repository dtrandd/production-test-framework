# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.

"""Maps switch interface names to the integer port IDs that identify them.

A switch port is identified by a single integer, while the switch names the same
port with a string, and that string depends on whether the physical cage is
broken out: a whole cage is one interface, while a broken-out cage is several,
each named with the cage plus a subport index.

A port ID is an interface's position in the switch's data-port interface list,
numbered from 1, so the subports of a broken-out cage take consecutive IDs and
every later cage shifts up. It is not the cage number, and no single name settles
it - only the whole list does.

Formatting a port ID with a fixed pattern therefore addresses a non-existent
interface on a broken-out cage, and truncating the suffix when parsing collapses
every subport of a cage onto the same port ID. Broken-out and whole cages can be
present on the same switch at once.

A subport suffix does not by itself mean the cage is split: a vendor may index the
first subport of a whole cage. Only a cage holding more than one interface is a
real breakout - see PortNameResolver.has_breakout versus
InterfaceName.has_subport_suffix.

This numbering is the convention the services under test use to identify switch
ports, so it is fixed by them rather than chosen here: reordering the names or
changing the base would silently disagree with every port ID they report.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

# The subport value of an interface name that carries no subport index.
NO_SUBPORT = -1

# An interface name is an alphabetic prefix, a cage number, and an optional
# subport index. This is the one place that records the per-vendor spellings, so
# the rest of the module stays notation-agnostic:
#
#   - Cumulus separates with "s" and numbers subports from 0 ("swp1", "swp1s0").
#     A whole cage carries no suffix.
#   - Arista separates with "/" and numbers subports from 1 ("Ethernet49/1"). A
#     whole QSFP is still written "/1", so a suffix here does not imply a split -
#     only the presence of "/2" and beyond does.
#
# Because of that second case, no conclusion about breakout may be drawn from a
# single name; see PortNameResolver.has_breakout.
_NAME_PATTERN = re.compile(r"^([a-zA-Z]+)(\d+)(?:([s/])(\d+))?$")


@dataclass(frozen=True)
class InterfaceName:
    """A parsed switch interface name."""

    raw: str  # The name as the switch reported it, trimmed.
    prefix: str  # The leading alphabetic portion, e.g. "swp" or "Ethernet".
    cage: int  # The physical port (cage) number.
    subport: int  # The breakout subport index, or NO_SUBPORT.

    @property
    def has_subport_suffix(self) -> bool:
        """Whether the name carries a subport index.

        This is a property of the name alone and does NOT mean the cage is broken
        out: some vendors index the first subport even when the cage is whole, so a
        name can carry a suffix while being the cage's only interface. Whether a
        cage is really split is a property of the set of names - see
        PortNameResolver.has_breakout.
        """
        return self.subport != NO_SUBPORT

    @property
    def sort_key(self) -> tuple[str, int, int]:
        """Orders names the way the switch lays its ports out: by prefix, then cage,
        then subport within a cage. A cage without a subport suffix sorts ahead of
        any subport, though in practice a cage has either form and never both.
        """
        return (self.prefix, self.cage, self.subport)


def parse(interface_name: str) -> InterfaceName | None:
    """Split an interface name into its prefix, cage and optional subport.

    Returns None for names that are not in either supported notation. Management
    and logical names like "eth0" or "vlan101" do parse, since they are
    structurally identical to a data port name; dropping those is the caller's
    job, as only the vendor driver knows which prefix its data ports use.
    """
    raw = interface_name.strip()
    match = _NAME_PATTERN.match(raw)
    if match is None:
        return None
    prefix, cage, separator, subport = match.groups()
    return InterfaceName(
        raw=raw,
        prefix=prefix,
        cage=int(cage),
        subport=int(subport) if separator else NO_SUBPORT,
    )


class PortNameResolver:
    """A bidirectional port ID <-> interface name mapping.

    It is given the interface names the switch actually reports (see learn) and
    assigns port IDs 1..N in switch-port order, so the subports of a broken-out
    cage take consecutive IDs and every later cage shifts up accordingly. Callers
    ask for the name of a port ID or the port ID of a name and never construct
    either themselves.

    Until learn succeeds the resolver is empty and every lookup reports None.
    """

    def __init__(self) -> None:
        self._id_to_name: dict[int, str] = {}
        self._name_to_id: dict[str, int] = {}
        self._has_breakout = False

    def learn(self, interface_names: Iterable[str]) -> int:
        """Replace the mapping with one derived from the names the switch reports.

        Names are sorted into switch-port order and assigned port IDs 1..N, so the
        subports of a broken-out cage take consecutive IDs. Names that are not in a
        recognized notation are ignored, as are duplicates; callers pass only
        data-port interfaces.

        Returns how many ports were mapped. A call that would map nothing leaves
        the existing mapping untouched, so a switch that answers with an empty or
        unusable interface list does not discard a good mapping.
        """
        parsed: dict[str, InterfaceName] = {}
        for raw in interface_names:
            name = parse(raw)
            if name is not None:
                parsed.setdefault(name.raw, name)
        if not parsed:
            return 0

        ordered = sorted(parsed.values(), key=lambda name: name.sort_key)
        self._id_to_name = {port_id: name.raw for port_id, name in enumerate(ordered, start=1)}
        self._name_to_id = {name: port_id for port_id, name in self._id_to_name.items()}

        per_cage: dict[tuple[str, int], int] = {}
        for name in ordered:
            cage = (name.prefix, name.cage)
            per_cage[cage] = per_cage.get(cage, 0) + 1
        self._has_breakout = any(count > 1 for count in per_cage.values())

        return len(ordered)

    def port_id(self, interface_name: str) -> int | None:
        """The port ID for an interface name, or None when it is not mapped."""
        return self._name_to_id.get(interface_name.strip())

    def name(self, port_id: int) -> str | None:
        """The interface name for a port ID, or None when it is not mapped."""
        return self._id_to_name.get(port_id)

    @property
    def discovered(self) -> bool:
        """Whether the resolver holds a mapping."""
        return bool(self._id_to_name)

    @property
    def has_breakout(self) -> bool:
        """Whether any cage is actually broken out, meaning more than one interface
        shares it. It tells an operator, via the driver's log, whether the mapping
        shifted port IDs away from plain cage numbering.

        A subport suffix on its own does not qualify: a vendor may index the first
        subport of a whole cage, so a suffix appears with no split behind it and
        every port would look like a breakout. Only a cage holding more than one
        interface adds port IDs, and so only that shifts the cages after it.
        """
        return self._has_breakout

    def mapping(self) -> str:
        """The port ID -> interface name mapping in port ID order, for logging."""
        return " ".join(f"{port_id}={self._id_to_name[port_id]}" for port_id in sorted(self._id_to_name))

    def __len__(self) -> int:
        return len(self._id_to_name)
