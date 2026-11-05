# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys

import pytest

from tracemarx.harness import build
from tracemarx.overrides import Overrides


def test_edits_round_trip_and_follow_the_run_not_the_number(demo, edit, tmp_path):
    h = build(str(demo))
    ov = Overrides(Overrides.identity_for(str(demo)), tmp_path / "data")
    ov.set(h.wires[0], colour="BU", size="1.5 mm²", note=" spare ")
    assert (h.wires[0].colour, h.wires[0].note) == ("BU", "spare")

    def renumber(sheet):
        for w in sheet["wires"]:
            if w.get("number"):
                w["number"] = "W" + w["number"]
    again = build(str(edit(renumber)))
    Overrides(Overrides.identity_for(str(demo)), tmp_path / "data").apply(again)
    assert (again.wires[0].number, again.wires[0].colour, again.wires[0].size) == ("W1", "BU", "1.5 mm²")


def test_clearing_every_field_removes_the_entry(demo, tmp_path):
    h = build(str(demo))
    ov = Overrides("file:x", tmp_path)
    ov.set(h.hoses[0], length="1200")
    ov.set(h.hoses[0], length="")
    assert ov.runs == {}
    assert Overrides("file:x", tmp_path).runs == {}


def test_identity_prefers_the_revisionworx_document(demo):
    assert Overrides.identity_for(str(demo), "doc-1") == "rwx:doc-1"
    assert Overrides.identity_for(str(demo)).startswith("file:")


def test_damaged_file_reads_as_empty(demo, tmp_path):
    ov = Overrides("file:y", tmp_path)
    ov.path.parent.mkdir(parents=True)
    ov.path.write_text("{not json", encoding="utf-8")
    assert Overrides("file:y", tmp_path).runs == {}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_save_failure_is_reported_not_raised(demo, tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        ov = Overrides("file:z", folder)
        ov.set(build(str(demo)).wires[0], colour="RD")
        assert ov.warning and "could not be saved" in ov.warning
    finally:
        folder.chmod(0o700)
