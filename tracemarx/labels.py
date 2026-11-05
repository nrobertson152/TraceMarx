# SPDX-License-Identifier: GPL-3.0-or-later
"""Labels: wire markers (one for each end), terminal markers, device labels and hose tags, printed on label
sheets as PDF, or exported as CSV for label-printer software (Brady Workstation, Brother P-touch Editor and
similar programs import a CSV database).

Sheet layouts are given by their dimensions. Print a test page on plain paper with outlines first, hold it
against a label sheet, and adjust the offsets if your printer shifts the page."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .harness import Harness
from .pdf import MM, PAGE_SIZES, Document, fit_size, text_width


@dataclass(frozen=True)
class Layout:
    key: str
    name: str
    paper: str
    cols: int
    rows: int
    width: float          # label size, points
    height: float
    left: float           # page margins, points
    top: float
    pitch_x: float        # distance from one label's left edge to the next one's
    pitch_y: float

    @property
    def per_page(self) -> int:
        return self.cols * self.rows


IN = 72.0
LAYOUTS = {
    "a4-65": Layout("a4-65", "A4 · 65 labels 38.1 × 21.2 mm (Avery L7651 layout)", "A4", 5, 13, 38.1 * MM, 21.2 * MM,
                    4.75 * MM, 10.7 * MM, 40.6 * MM, 21.2 * MM),
    "a4-24": Layout("a4-24", "A4 · 24 labels 63.5 × 33.9 mm (Avery L7159 layout)", "A4", 3, 8, 63.5 * MM, 33.9 * MM,
                    6.5 * MM, 13.1 * MM, 66.0 * MM, 33.9 * MM),
    "letter-30": Layout("letter-30", "Letter · 30 labels 2⅝ × 1 in (Avery 5160 layout)", "Letter", 3, 10, 2.625 * IN,
                        1.0 * IN, 0.1875 * IN, 0.5 * IN, 2.75 * IN, 1.0 * IN),
    "letter-80": Layout("letter-80", "Letter · 80 labels 1¾ × ½ in (Avery 5167 layout)", "Letter", 4, 20, 1.75 * IN,
                        0.5 * IN, 0.3 * IN, 0.5 * IN, 2.05 * IN, 0.5 * IN),
}
DEFAULT_LAYOUT = "a4-65"


def custom_layout(paper: str, cols: int, rows: int, width_mm: float, height_mm: float, left_mm: float, top_mm: float,
                  gap_x_mm: float = 0.0, gap_y_mm: float = 0.0) -> Layout:
    if paper not in PAGE_SIZES or cols < 1 or rows < 1 or width_mm <= 0 or height_mm <= 0:
        raise ValueError("a label layout needs a paper size, at least one row and column, and a label size")
    pw, ph = PAGE_SIZES[paper]
    lay = Layout("custom", f"{paper} · {cols * rows} labels {width_mm:g} × {height_mm:g} mm (custom)", paper, cols,
                 rows, width_mm * MM, height_mm * MM, left_mm * MM, top_mm * MM, (width_mm + gap_x_mm) * MM,
                 (height_mm + gap_y_mm) * MM)
    if lay.left + (cols - 1) * lay.pitch_x + lay.width > pw + 0.5 or lay.top + (rows - 1) * lay.pitch_y + lay.height > ph + 0.5:
        raise ValueError("those labels do not fit on the paper")
    return lay


@dataclass(frozen=True)
class Label:
    kind: str             # wire | terminal | strip | device | hose
    line1: str
    line2: str = ""


@dataclass
class LabelOptions:
    wires: bool = True
    wire_destination: bool = True      # second line on a wire marker: where the other end goes
    terminals: bool = True
    devices: bool = True
    hoses: bool = True
    copies: int = 1
    skip_unnumbered: bool = True


def make_labels(h: Harness, opt: LabelOptions) -> list[Label]:
    out: list[Label] = []
    if opt.wires:
        for r in h.wires:
            if not r.number and opt.skip_unnumbered:
                continue
            if opt.wire_destination:
                out.append(Label("wire", r.label, f"{r.a.ref} → {r.b.ref}"))
                out.append(Label("wire", r.label, f"{r.b.ref} → {r.a.ref}"))
            else:
                out += [Label("wire", r.label), Label("wire", r.label)]
    if opt.terminals:
        for strip, terminals in h.strips.items():
            out.append(Label("strip", strip, f"{len(terminals)} terminals"))
            for t in terminals:
                out.append(Label("terminal", f"{strip}:{t.number}" if t.number else strip, t.description))
    if opt.devices:
        for d in h.devices:
            out.append(Label("device", d.designator, d.description))
    if opt.hoses:
        for r in h.hoses:
            line2 = f"{r.a.ref} ↔ {r.b.ref} · {r.function}"
            out += [Label("hose", r.number, line2), Label("hose", r.number, line2)]
    copies = max(1, min(int(opt.copies or 1), 20))
    return [lab for lab in out for _ in range(copies)]


def label_rects(layout: Layout, count: int, start: int = 0):
    """(page, x, y) of each label position, from position ``start`` on the first sheet (to use a partly used
    sheet)."""
    positions = []
    i = start
    for _ in range(count):
        page, slot = divmod(i, layout.per_page)
        row, col = divmod(slot, layout.cols)
        positions.append((page, layout.left + col * layout.pitch_x, layout.top + row * layout.pitch_y))
        i += 1
    return positions


def to_pdf(labels: list[Label], layout: Layout, outlines: bool = False, start: int = 0,
           offset_x_mm: float = 0.0, offset_y_mm: float = 0.0, title: str = "Labels") -> bytes:
    pw, ph = PAGE_SIZES[layout.paper]
    doc = Document(title)
    pages = {}
    dx, dy = offset_x_mm * MM, offset_y_mm * MM
    for lab, (page_i, x, y) in zip(labels, label_rects(layout, len(labels), start)):
        page = pages.get(page_i)
        if page is None:
            while len(doc.pages) <= page_i:
                doc.add_page(pw, ph)
            page = pages[page_i] = doc.pages[page_i]
        x, y = x + dx, y + dy
        if outlines:
            page.rect(x, y, layout.width, layout.height, width=0.3, gray=0.6, dash=True, radius=2)
        _draw_label(page, lab, x, y, layout.width, layout.height)
    if not doc.pages:
        doc.add_page(pw, ph)
    return doc.to_bytes()


def _draw_label(page, lab: Label, x: float, y: float, w: float, h: float) -> None:
    pad = min(w, h) * 0.08
    inner = w - 2 * pad
    big = min(h * (0.5 if lab.line2 else 0.62), 26)
    size1 = fit_size(lab.line1, inner, big, bold=True, minimum=4)
    if lab.line2:
        size2 = fit_size(lab.line2, inner, min(h * 0.22, 9), minimum=3.5)
        line2 = lab.line2
        while len(line2) > 1 and text_width(line2, size2) > inner:
            line2 = line2[:-2] + "…"
        total = size1 + size2 * 1.25
        top = y + (h - total) / 2
        page.text(x + w / 2, top + size1 * 0.85, lab.line1, size1, bold=True, align="center")
        page.text(x + w / 2, top + size1 + size2 * 1.05, line2, size2, align="center", gray=0.2)
    else:
        page.text(x + w / 2, y + h / 2 + size1 * 0.35, lab.line1, size1, bold=True, align="center")


def to_csv(labels: list[Label]) -> str:
    """One row per distinct label with its count, for label-printer software."""
    counts: dict[Label, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["Kind", "Line1", "Line2", "Copies"])
    for lab, n in counts.items():
        w.writerow([lab.kind, _guard(lab.line1), _guard(lab.line2), n])
    return "﻿" + buf.getvalue()


def _guard(value: str) -> str:
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
