# SPDX-License-Identifier: GPL-3.0-or-later
"""Everything TraceMarx produces, derived from one OpenSchematix project. No GUI toolkit here.

Connectivity comes from OpenSchematix's public scripting API (``Project.nets()``), so TraceMarx sees exactly the
connections OpenSchematix computes. The schematic is opened read-only and never written.

* **Runs** are the physical pieces to make: one per pair of consecutive *physical* endpoints of a net, in drawing
  order (sheet, then top to bottom, left to right). A net with N device terminals needs N-1 runs. Logical symbols
  (sheet connectors, signal ends) join nets but are not devices, so they are skipped. The schematic decides what
  is connected; the run order is a suggestion the person building it may change.
* Electrical nets give **wires**; nets whose terminals are hydraulic or pneumatic ports give **hoses / tubes**.
* **Terminal strips** come from terminal symbols, grouped by designator (X1) and ordered by terminal number.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

LOGICAL_SYMBOLS = frozenset({"gen.offpage", "el.connector.point"})
LINE_FUNCTIONS = (("hyd.pressure", "Pressure"), ("pneu.supply", "Supply"), ("hyd.tank", "Tank / return"),
                  ("pneu.exhaust", "Exhaust"), ("hyd.drain", "Drain"), ("hyd.pilot", "Pilot"),
                  ("pneu.pilot", "Pilot"), ("hyd.work", "Work"), ("pneu.work", "Work"))


class HarnessError(Exception):
    """The project could not be read. The message is meant for the user."""


def natural(s: str):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", s or "")]


@dataclass(frozen=True)
class End:
    designator: str
    terminal: str
    sheet: str
    sheet_index: int
    description: str = ""

    @property
    def ref(self) -> str:
        return f"{self.designator}:{self.terminal}"


@dataclass
class Run:
    """One wire or hose to make, between two device terminals."""
    kind: str                 # wire | hose
    number: str               # wire number from the schematic, or the hose id; "" if the schematic has none
    a: End
    b: End
    medium: str = ""          # electrical | hydraulic | pneumatic
    function: str = ""        # hoses: Pressure, Tank / return, Work, ...
    net_key: str = ""
    index_in_net: int = 0
    colour: str = ""          # user overrides (tracemarx/overrides.py)
    size: str = ""
    length: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        """Stable identity for overrides: the two ends, independent of numbering."""
        return f"{self.kind}|{self.a.sheet}|{self.a.ref}|{self.b.sheet}|{self.b.ref}"

    @property
    def label(self) -> str:
        return self.number or "?"


@dataclass
class StripTerminal:
    strip: str
    number: str
    description: str
    sheet: str
    side1: list[str] = field(default_factory=list)     # "wire 3 → F1:1"
    side2: list[str] = field(default_factory=list)
    wires: list[str] = field(default_factory=list)


@dataclass
class Device:
    designator: str
    description: str
    part_number: str
    manufacturer: str
    sheets: list[str]


@dataclass
class Problem:
    severity: str             # error | warning | info
    message: str
    where: str = ""


@dataclass
class Source:
    path: str
    name: str
    sha256: str
    title: str
    drawing_number: str
    revision: str
    sheets: list[str]


@dataclass
class Harness:
    source: Source
    wires: list[Run]
    hoses: list[Run]
    strips: dict[str, list[StripTerminal]]
    devices: list[Device]
    problems: list[Problem]

    @property
    def runs(self) -> list[Run]:
        return self.wires + self.hoses

    def counts(self) -> dict[str, int]:
        return {"wires": len(self.wires), "hoses": len(self.hoses), "strips": len(self.strips),
                "terminals": sum(len(v) for v in self.strips.values()), "devices": len(self.devices),
                "errors": sum(1 for p in self.problems if p.severity == "error"),
                "warnings": sum(1 for p in self.problems if p.severity == "warning")}


def _medium(port_types: set[str]) -> str:
    if any(p.startswith("hyd.") for p in port_types):
        return "hydraulic"
    if any(p.startswith("pneu.") for p in port_types):
        return "pneumatic"
    return "electrical"


def _function(port_types: set[str]) -> str:
    for pt, name in LINE_FUNCTIONS:
        if pt in port_types:
            return name
    return "Line"


def build(path: str | Path) -> Harness:
    from openschematix import api
    from openschematix.project import is_terminal_symbol
    path = Path(path)
    try:
        data = path.read_bytes()
        project = api.open_project(str(path))
    except api.ApiError as exc:
        raise HarnessError(str(exc)) from exc
    except OSError as exc:
        raise HarnessError(f"could not read {path}: {exc.strerror or exc}") from exc
    internal = project._use()
    meta = internal.meta
    sheets = list(project.sheets)
    sheet_index = {s.uid: i for i, s in enumerate(sheets)}
    source = Source(str(path.resolve()), path.name, hashlib.sha256(data).hexdigest(), project.title or path.stem,
                    meta.get("number", ""), meta.get("revision", ""), [s.name for s in sheets])
    problems: list[Problem] = [Problem("warning", w) for w in project.warnings]

    comps: dict[tuple[str, int], object] = {}
    for s in sheets:
        for c in s.components():
            comps[(s.uid, c.id)] = c

    def physical(c) -> bool:
        return bool(c.designator) and c.symbol_id not in LOGICAL_SYMBOLS

    def end_of(ref) -> End:
        c = comps[(ref.sheet_uid, ref.component_id)]
        props = dict(c.properties)
        # a terminal block is named by its strip terminal number (X1:7), not by which side the wire is on
        label = (props.get("terminal") or c.label or _terminal_label(c, ref.terminal)) if is_terminal_symbol(c.symbol_id) \
            else _terminal_label(c, ref.terminal)
        return End(ref.designator, label, ref.sheet_name, sheet_index[ref.sheet_uid], props.get("description", ""))

    def sort_key(ref):
        c = comps[(ref.sheet_uid, ref.component_id)]
        t = c.terminals.get(ref.terminal)
        x, y = t.position if t else c.position
        return (sheet_index[ref.sheet_uid], y, x, natural(ref.designator), natural(ref.terminal))

    wires: list[Run] = []
    hoses: list[Run] = []
    numbers_seen: dict[str, str] = {}
    unnumbered = 0
    for n in project.nets():
        refs = [r for r in n.terminals if (r.sheet_uid, r.component_id) in comps]
        port_types = set()
        for r in refs:
            t = comps[(r.sheet_uid, r.component_id)].terminals.get(r.terminal)
            if t:
                port_types.add(t.port_type)
        medium = _medium(port_types)
        kind = "wire" if medium == "electrical" else "hose"
        phys = sorted((r for r in refs if physical(comps[(r.sheet_uid, r.component_id)])), key=sort_key)
        net_key = "|".join(sorted(f"{r.sheet_uid}:{r.component_id}:{r.terminal}" for r in refs))
        where = ", ".join(sorted({r.sheet_name for r in refs}))
        if len(phys) < 2:
            if len(phys) == 1:
                problems.append(Problem("warning", f"{'Wire ' + n.number if n.number else 'A connection'} reaches only "
                                        f"one device terminal ({end_of(phys[0]).ref}); nothing to make", where))
            continue
        if kind == "wire":
            if not n.number:
                unnumbered += 1
            elif n.number in numbers_seen and numbers_seen[n.number] != net_key:
                problems.append(Problem("error", f"Wire number {n.number} is used for two different connections",
                                        where))
            numbers_seen.setdefault(n.number, net_key)
        target = wires if kind == "wire" else hoses
        order = sort_key(phys[0])
        for i in range(len(phys) - 1):
            run = Run(kind, n.number if kind == "wire" else "", end_of(phys[i]), end_of(phys[i + 1]),
                      medium, _function(port_types) if kind == "hose" else "", net_key, i)
            run._order = order                       # drawing position of the net's first device (sorting only)
            target.append(run)
    if unnumbered:
        problems.append(Problem("warning", f"{unnumbered} connection(s) have no wire number. Number the wires in "
                                "OpenSchematix (Tools › Number wires) so labels can be printed for them."))

    wires.sort(key=lambda r: (r.number == "", natural(r.number), r._order, r.index_in_net))
    hoses.sort(key=lambda r: (r._order, r.index_in_net))
    designators = {c.designator for c in comps.values() if c.designator}
    counters = {"hydraulic": 0, "pneumatic": 0}
    for h in hoses:
        prefix = "HS" if h.medium == "hydraulic" else "TB"     # hose / tube; never a device designator
        while True:
            counters[h.medium] += 1
            if f"{prefix}{counters[h.medium]}" not in designators:
                break
        h.number = f"{prefix}{counters[h.medium]}"

    # terminal strips
    strips: dict[str, list[StripTerminal]] = {}
    side_nets: dict[tuple[str, int, str], list] = {}
    for n in project.nets():
        for r in n.terminals:
            side_nets.setdefault((r.sheet_uid, r.component_id, r.terminal), []).append(n)
    seen_numbers: dict[tuple[str, str], int] = {}
    for (uid, cid), c in comps.items():
        if not is_terminal_symbol(c.symbol_id):
            continue
        props = dict(c.properties)
        strip = c.designator or "X?"
        number = props.get("terminal") or c.label or ""
        if not c.designator:
            problems.append(Problem("warning", "A terminal has no strip designator (for example X1)",
                                    sheets[sheet_index[uid]].name))
        if not number:
            problems.append(Problem("warning", f"A terminal on strip {strip} has no terminal number",
                                    sheets[sheet_index[uid]].name))
        key = (strip, number)
        seen_numbers[key] = seen_numbers.get(key, 0) + 1
        st = StripTerminal(strip, number, props.get("description", ""), sheets[sheet_index[uid]].name)
        for side_i, side in enumerate(sorted(c.terminals, key=natural)):
            texts = []
            for n in side_nets.get((uid, cid, side), []):
                far = [end_of(r).ref for r in n.terminals
                       if (r.sheet_uid, r.component_id) != (uid, cid)
                       and (r.sheet_uid, r.component_id) in comps and physical(comps[(r.sheet_uid, r.component_id)])]
                label = f"wire {n.number}" if n.number else "unnumbered"
                texts.append(f"{label} → {', '.join(far)}" if far else label)
                if n.number and n.number not in st.wires:
                    st.wires.append(n.number)
            (st.side1 if side_i == 0 else st.side2).extend(texts)
        strips.setdefault(strip, []).append(st)
    for (strip, number), count in seen_numbers.items():
        if number and count > 1:
            problems.append(Problem("error", f"Terminal {strip}:{number} appears {count} times"))
    for k in strips:
        strips[k].sort(key=lambda t: natural(t.number))
    strips = dict(sorted(strips.items(), key=lambda kv: natural(kv[0])))

    # devices (labels and the device list)
    by_des: dict[str, Device] = {}
    for (uid, cid), c in comps.items():
        if not physical(c) or is_terminal_symbol(c.symbol_id):
            continue
        props = dict(c.properties)
        d = by_des.get(c.designator)
        if d is None:
            by_des[c.designator] = Device(c.designator, props.get("description", ""), props.get("part_number", ""),
                                          props.get("manufacturer", ""), [sheets[sheet_index[uid]].name])
        else:
            d.description = d.description or props.get("description", "")
            d.part_number = d.part_number or props.get("part_number", "")
            if sheets[sheet_index[uid]].name not in d.sheets:
                d.sheets.append(sheets[sheet_index[uid]].name)
    devices = sorted(by_des.values(), key=lambda d: natural(d.designator))
    return Harness(source, wires, hoses, strips, devices, problems)


def _terminal_label(c, name: str) -> str:
    t = c.terminals.get(name)
    return (t.label or name) if t else name
