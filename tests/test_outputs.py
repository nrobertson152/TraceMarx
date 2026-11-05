# SPDX-License-Identifier: GPL-3.0-or-later
import re
import zlib

import pytest

from tracemarx import labels, pdf, reports
from tracemarx.harness import build


def page_text(data: bytes) -> str:
    out = []
    for m in re.finditer(rb"stream\n(.*?)\nendstream", data, re.S):
        out.append(zlib.decompress(m.group(1)).decode("latin-1"))
    return "\n".join(out)


def check_xref(data: bytes):
    """Every xref offset points at the object it names: a PDF reader can open the file."""
    start = int(re.search(rb"startxref\n(\d+)", data).group(1))
    table = data[start:].split(b"trailer")[0].split(b"\n")
    count = int(table[1].split()[1])
    for i, line in enumerate(table[3:3 + count - 1], 1):
        off = int(line.split()[0])
        assert data[off:].startswith(f"{i} 0 obj".encode()), i


def test_pdf_structure_and_text_escaping():
    doc = pdf.Document("Title (x)")
    page = doc.add_page(*pdf.PAGE_SIZES["A4"])
    page.text(10, 10, "a (b) \\ c → d", 10)
    data = doc.to_bytes()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    check_xref(data)
    text = page_text(data)
    assert r"a \(b\) \\ c" in text and "?" not in text.split("c", 1)[1].split("d")[0]


def test_fit_text_shrinks_then_shortens():
    text, size = reports.fit_text("short", 100, 10)
    assert (text, size) == ("short", 10)
    text, size = reports.fit_text("a very long description " * 5, 60, 10)
    assert text.endswith("…") and pdf.text_width(text, size) <= 60


def test_wiring_book_is_deterministic_and_names_its_source(demo):
    h = build(str(demo))
    a = reports.wiring_book(h, "A4", "DWG-1 rev A (Released)")
    b = reports.wiring_book(build(str(demo)), "A4", "DWG-1 rev A (Released)")
    assert a == b
    check_xref(a)
    text = page_text(a)
    assert "DWG-1 rev A" in text and h.source.sha256[:16] in text
    assert reports.wiring_book(h, "Letter") != a


def test_csv_tables_guard_formulas(demo, tmp_path):
    h = build(str(demo))
    h.wires[0].note = "=HYPERLINK(\"x\")"
    h.wires[1].note = "@SUM(A1)"
    out = reports.to_csv(h, "wires")
    assert out.startswith("﻿")
    assert "'=HYPERLINK" in out and "'@SUM" in out
    files = reports.write_csvs(h, tmp_path / "csv")
    assert len(files) == len(reports.TABLES) and all(f.exists() for f in files)


def test_labels_for_every_kind_and_copies(demo):
    h = build(str(demo))
    labs = labels.make_labels(h, labels.LabelOptions())
    kinds = {lab.kind for lab in labs}
    assert kinds == {"wire", "strip", "terminal", "device", "hose"}
    wires = [lab for lab in labs if lab.kind == "wire"]
    assert len(wires) == 2 * len(h.wires)
    assert wires[0].line2 == "X1:1 → F1:1" and wires[1].line2 == "F1:1 → X1:1"
    assert len(labels.make_labels(h, labels.LabelOptions(copies=3))) == 3 * len(labs)
    only = labels.make_labels(h, labels.LabelOptions(wires=False, terminals=False, hoses=False))
    assert {lab.kind for lab in only} == {"device"}


def test_label_positions_start_on_a_partly_used_sheet():
    lay = labels.LAYOUTS["a4-24"]
    rects = labels.label_rects(lay, 3, start=23)
    assert [r[0] for r in rects] == [0, 1, 1]
    assert rects[1][1:] == (lay.left, lay.top)


def test_label_pdf_pages(demo):
    h = build(str(demo))
    labs = labels.make_labels(h, labels.LabelOptions())
    lay = labels.LAYOUTS["a4-24"]
    data = labels.to_pdf(labs, lay, outlines=True)
    check_xref(data)
    assert data.count(b"/Type /Page ") == -(-len(labs) // lay.per_page)


def test_custom_layout_must_fit_the_paper():
    lay = labels.custom_layout("A4", 2, 5, 90, 50, 10, 10, 5, 2)
    assert lay.per_page == 10
    with pytest.raises(ValueError):
        labels.custom_layout("A4", 3, 5, 90, 50, 10, 10)
    with pytest.raises(ValueError):
        labels.custom_layout("A3", 1, 1, 10, 10, 0, 0)


def test_label_csv_counts_duplicates_and_guards_formulas():
    labs = [labels.Label("wire", "1", "A → B")] * 2 + [labels.Label("device", "=X", "")]
    out = labels.to_csv(labs)
    assert "wire,1,A → B,2" in out and "device,'=X,,1" in out
