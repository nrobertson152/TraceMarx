# SPDX-License-Identifier: GPL-3.0-or-later
"""Wire list, terminal strip plan, hose list and device list: CSV files and one printable PDF ("wiring book").
Every output names its source (file, title block number and revision, SHA-256), so a printed list can always be
traced to the exact schematic it was made from."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from . import APP_NAME, VERSION
from .harness import Harness
from .pdf import MM, PAGE_SIZES, Document, fit_size, text_width

WIRE_COLUMNS = (("Wire", "number"), ("From", "a"), ("From sheet", "a_sheet"), ("To", "b"), ("To sheet", "b_sheet"),
                ("Colour", "colour"), ("Size", "size"), ("Length", "length"), ("Note", "note"))
HOSE_COLUMNS = (("Hose", "number"), ("Function", "function"), ("Medium", "medium"), ("From", "a"), ("To", "b"),
                ("Sheet", "a_sheet"), ("Size", "size"), ("Length", "length"), ("Note", "note"))
TERMINAL_COLUMNS = (("Strip", "strip"), ("Terminal", "number"), ("Description", "description"),
                    ("Side 1", "side1"), ("Side 2", "side2"), ("Sheet", "sheet"))
DEVICE_COLUMNS = (("Designator", "designator"), ("Description", "description"), ("Part number", "part_number"),
                  ("Manufacturer", "manufacturer"), ("Sheets", "sheets"))


def _run_row(r) -> dict:
    return {"number": r.label, "a": r.a.ref, "a_sheet": r.a.sheet, "b": r.b.ref, "b_sheet": r.b.sheet,
            "colour": r.colour, "size": r.size, "length": r.length, "note": r.note, "function": r.function,
            "medium": r.medium}


def wire_rows(h: Harness) -> list[dict]:
    return [_run_row(r) for r in h.wires]


def hose_rows(h: Harness) -> list[dict]:
    return [_run_row(r) for r in h.hoses]


def terminal_rows(h: Harness) -> list[dict]:
    return [{"strip": t.strip, "number": t.number, "description": t.description, "side1": "; ".join(t.side1),
             "side2": "; ".join(t.side2), "sheet": t.sheet} for ts in h.strips.values() for t in ts]


def device_rows(h: Harness) -> list[dict]:
    return [{"designator": d.designator, "description": d.description, "part_number": d.part_number,
             "manufacturer": d.manufacturer, "sheets": ", ".join(d.sheets)} for d in h.devices]


TABLES = {"wires": ("Wire list", WIRE_COLUMNS, wire_rows), "terminals": ("Terminal strips", TERMINAL_COLUMNS,
                                                                         terminal_rows),
          "hoses": ("Hose and tube list", HOSE_COLUMNS, hose_rows), "devices": ("Devices", DEVICE_COLUMNS, device_rows)}


def fit_text(text: str, width: float, size: float, bold: bool = False, minimum: float = 5.5) -> tuple[str, float]:
    """Shrink to fit down to ``minimum``; past that, shorten with an ellipsis."""
    size = fit_size(text, width, size, bold, minimum)
    while len(text) > 1 and text_width(text, size, bold) > width:
        text = text[:-2].rstrip() + "…"
    return text, size


def _guard(value: str) -> str:
    """Spreadsheets run cells that start with = + - @ as formulas; a leading apostrophe keeps them text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(h: Harness, table: str) -> str:
    title, cols, rows = TABLES[table]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c for c, _ in cols])
    for row in rows(h):
        w.writerow([_guard(str(row.get(k, ""))) for _, k in cols])
    return "﻿" + buf.getvalue()


def source_line(h: Harness, rwx_label: str = "") -> str:
    s = h.source
    ident = " ".join(x for x in (s.drawing_number, f"rev {s.revision}" if s.revision else "") if x)
    parts = [f"Source: {s.name}" + (f" ({ident})" if ident else "")]
    if rwx_label:
        parts.append(f"RevisionWorx: {rwx_label}")
    parts.append(f"SHA-256 {s.sha256[:16]}…")
    return " · ".join(parts)


def write_csvs(h: Harness, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(h.source.name).stem
    out = []
    for table in TABLES:
        p = folder / f"{stem} - {TABLES[table][0]}.csv"
        p.write_text(to_csv(h, table), encoding="utf-8", newline="")
        out.append(p)
    return out


def wiring_book(h: Harness, paper: str = "A4", rwx_label: str = "", tables=("wires", "terminals", "hoses",
                                                                                "devices")) -> bytes:
    """One PDF with the chosen tables, landscape, each table starting on a new page."""
    pw, ph = PAGE_SIZES[paper]
    pw, ph = ph, pw                                       # landscape
    margin = 12 * MM
    doc = Document(f"{h.source.title} - wiring")
    src = source_line(h, rwx_label)
    pages = []

    def new_page(title: str):
        p = doc.add_page(pw, ph)
        pages.append(p)
        p.text(margin, margin + 6, h.source.title, 14, bold=True)
        p.text(pw - margin, margin + 6, title, 12, bold=True, align="right")
        p.text(margin, margin + 18, src, 7.5, gray=0.35)
        p.line(margin, margin + 23, pw - margin, margin + 23, 0.6)
        return p, margin + 36

    any_table = False
    for table in tables:
        title, cols, rows_fn = TABLES[table]
        rows = rows_fn(h)
        if not rows:
            continue
        any_table = True
        avail = pw - 2 * margin
        weights = {"description": 2.2, "side1": 3, "side2": 3, "note": 1.6, "a": 1.3, "b": 1.3, "sheets": 1.6,
                   "function": 1.3, "manufacturer": 1.4, "part_number": 1.3, "a_sheet": 1.2, "b_sheet": 1.2}
        total = sum(weights.get(k, 0.8) for _, k in cols)
        widths = [avail * weights.get(k, 0.8) / total for _, k in cols]
        p, y = new_page(title)

        def header(p, y):
            p.rect(margin, y - 10, avail, 14, width=0, fill=0.92)
            x = margin
            for (name, _), w in zip(cols, widths):
                p.text(x + 3, y, name, 8, bold=True)
                x += w
            return y + 14

        y = header(p, y)
        for i, row in enumerate(rows):
            if y > ph - margin - 14:
                p, y = new_page(title + " (continued)")
                y = header(p, y)
            if i % 2:
                p.rect(margin, y - 10, avail, 13, width=0, fill=0.97)
            x = margin
            for (_, key), w in zip(cols, widths):
                bold = key == "number" and table in ("wires", "hoses")
                text, size = fit_text(str(row.get(key, "")), w - 6, 8, bold)
                p.text(x + 3, y, text, size, bold=bold)
                x += w
            y += 13
    if not any_table:
        p, y = new_page("Nothing to list")
        p.text(margin, y, "The schematic has no connections between devices.", 10)
    for n, p in enumerate(pages, 1):
        # no clock time: the same schematic gives the same file, so republishing records no needless revision
        p.text(margin, ph - margin + 14, f"Made with {APP_NAME} {VERSION} from SHA-256 {h.source.sha256[:16]}", 7,
               gray=0.45)
        p.text(pw - margin, ph - margin + 14, f"Page {n} of {len(pages)}", 7, gray=0.45, align="right")
    return doc.to_bytes()
