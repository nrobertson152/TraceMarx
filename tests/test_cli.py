# SPDX-License-Identifier: GPL-3.0-or-later
from tracemarx.cli import main


def test_check(demo, capsys):
    assert main(["check", str(demo)]) == 0
    assert "13 wire run(s)" in capsys.readouterr().out


def test_check_reports_errors_with_exit_1(edit, capsys):
    def dup(sheet):
        for w in sheet["wires"]:
            if w.get("number") == "1":
                w["number"] = "2"
    assert main(["check", str(edit(dup))]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_report_and_labels(demo, tmp_path):
    out = tmp_path / "out"
    assert main(["report", str(demo), "--out", str(out), "--paper", "Letter"]) == 0
    assert (out / "demo_project - wiring.pdf").read_bytes().startswith(b"%PDF")
    assert len(list(out.glob("*.csv"))) == 4
    assert main(["labels", str(demo), "--out", str(tmp_path / "l.pdf"), "--layout", "letter-30", "--outlines"]) == 0
    assert (tmp_path / "l.pdf").read_bytes().startswith(b"%PDF")
    assert main(["labels", str(demo), "--out", str(tmp_path / "l.csv"), "--csv", "--no-destination"]) == 0
    assert "Kind,Line1,Line2,Copies" in (tmp_path / "l.csv").read_text(encoding="utf-8-sig")


def test_unreadable_schematic_exits_2(tmp_path, capsys):
    bad = tmp_path / "bad.schematix"
    bad.write_bytes(b"x")
    assert main(["check", str(bad)]) == 2
    assert capsys.readouterr().err.startswith("error:")
