# SPDX-License-Identifier: GPL-3.0-or-later
"""``tracemarx --self-test REPORT``: open the bundled demo schematic in the real window, check the lists, make the
wiring book and labels into a temporary folder, close, and write PASSED or FAILED. Proves an installed or packaged
copy works (a windowed Windows executable has no console)."""
from __future__ import annotations

import shutil
import tempfile
import tkinter as tk
import traceback
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent / "demo" / "demo_project.schematix"


def run(report: Path) -> int:
    from .. import VERSION, labels, reports
    from .app import TraceMarxApp
    from .settings import Settings
    lines, ok = [f"version {VERSION}"], False
    tmp = Path(tempfile.mkdtemp(prefix="tracemarx-selftest-"))
    try:
        root = tk.Tk()
        app = TraceMarxApp(root, None, settings=Settings(tmp / "cfg"), rwx=False, data_dir=tmp / "data")
        root.update()
        assert app.open_path(str(DEMO)), "the demo schematic did not open"
        root.update()
        wires = len(app.trees["wires"].get_children())
        assert wires > 0, "no wires listed"
        book = reports.wiring_book(app.h)
        assert book.startswith(b"%PDF"), "the wiring book is not a PDF"
        labs = labels.make_labels(app.h, app.label_options())
        sheet = labels.to_pdf(labs, app.layout())
        assert sheet.startswith(b"%PDF"), "the label sheet is not a PDF"
        lines += [f"wires: {wires}", f"labels: {len(labs)}", f"book bytes: {len(book)}", f"label bytes: {len(sheet)}"]
        app.quit()
        ok = True
    except BaseException as exc:  # noqa: BLE001
        lines.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    lines.append("self-test " + ("PASSED" if ok else "FAILED"))
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1
