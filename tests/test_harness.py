# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from tracemarx.harness import HarnessError, build


def test_demo_lists(demo):
    h = build(str(demo))
    c = h.counts()
    assert (c["wires"], c["hoses"], c["terminals"], c["strips"], c["devices"]) == (13, 9, 2, 1, 16)
    assert c["errors"] == 0 and c["warnings"] == 0
    first = h.wires[0]
    assert (first.number, first.a.ref, first.b.ref) == ("1", "X1:1", "F1:1")
    assert h.source.title == "Conveyor station 3" and len(h.source.sha256) == 64


def test_terminal_block_ends_are_named_by_terminal_number(demo):
    h = build(str(demo))
    refs = {r.a.ref for r in h.wires} | {r.b.ref for r in h.wires}
    assert {"X1:1", "X1:2"} <= refs
    assert [t.number for t in h.strips["X1"]] == ["1", "2"]
    assert any("F1:1" in s for s in h.strips["X1"][0].side2)


def test_hose_ids_never_collide_with_device_designators(demo):
    h = build(str(demo))
    devices = {d.designator for d in h.devices}
    hoses = [r.number for r in h.hoses]
    assert hoses and all(n.startswith("HS") for n in hoses)
    assert not devices & set(hoses)
    assert len(set(hoses)) == len(hoses)


def test_run_keys_are_unique_and_survive_renumbering(demo, edit):
    keys = [r.key for r in build(str(demo)).runs]
    assert len(keys) == len(set(keys))

    def renumber(sheet):
        for w in sheet["wires"]:
            if w.get("number"):
                w["number"] = "W" + w["number"]
    renumbered = build(str(edit(renumber)))
    assert sorted(r.key for r in renumbered.runs) == sorted(keys)
    assert renumbered.wires[0].number == "W1"


def test_duplicate_wire_number_is_an_error(edit):
    def dup(sheet):
        for w in sheet["wires"]:
            if w.get("number") == "1":
                w["number"] = "2"
    h = build(str(edit(dup)))
    assert any(p.severity == "error" and "2" in p.message for p in h.problems), h.problems


def test_unnumbered_wires_are_reported(edit):
    def strip_numbers(sheet):
        for w in sheet["wires"]:
            w.pop("number", None)
    h = build(str(edit(strip_numbers)))
    assert any("no wire number" in p.message for p in h.problems)
    assert all(r.label == "?" for r in h.wires if not r.number)


def test_missing_and_damaged_files_raise_harness_error(tmp_path):
    with pytest.raises(HarnessError):
        build(str(tmp_path / "nope.schematix"))
    bad = tmp_path / "bad.schematix"
    bad.write_bytes(b"not a schematic")
    with pytest.raises(HarnessError):
        build(str(bad))


def test_reading_never_changes_the_schematic(demo):
    before = demo.read_bytes()
    build(str(demo))
    assert demo.read_bytes() == before
