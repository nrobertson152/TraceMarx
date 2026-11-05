# SPDX-License-Identifier: GPL-3.0-or-later
"""TraceMarx window.

Built from OpenSchematix's own themes, icons and toolbar widgets, so it sits naturally beside OpenSchematix and
OpenSchematix Viewer. TraceMarx never changes a schematic: it reads it, and writes only its own outputs (reports,
labels) and the person's notes about wires and hoses (overrides.py). RevisionWorx is optional."""
from __future__ import annotations

import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from openschematix.gui import theme as th
from openschematix.gui.widgets import IconButton, ToastHost, Tooltip, ToolbarSeparator, make_menu, set_current_theme

from .. import APP_NAME, COPYRIGHT, DONATE_URL, LICENSE_NAME, LICENSE_URL, REPO_URL, VERSION, labels, reports
from ..harness import HarnessError, build
from ..overrides import Overrides
from .settings import Settings

FILE_TYPES = (("OpenSchematix projects", "*.schematix"), ("OpenSchematix drawings (legacy)", "*.osch"),
              ("All files", "*.*"))
SHORTCUTS = (("Ctrl+O", "Open a schematic"), ("Ctrl+Shift+O", "Open a controlled schematic from RevisionWorx"),
             ("Ctrl+P", "Export the wiring book (PDF)"), ("Ctrl+L", "Export labels (PDF)"),
             ("Ctrl+Shift+P", "Publish the wiring book and labels to RevisionWorx"),
             ("Enter / double-click", "Edit a wire's or hose's colour, size, length and note"),
             ("Ctrl+F", "Filter the current list"), ("F5", "Reload the schematic"), ("Ctrl+W", "Close"),
             ("F1", "This list"))


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, "tracemarx", "resources", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", name)


class TraceMarxApp:
    TABS = ("wires", "terminals", "hoses", "devices", "labels", "problems")

    def __init__(self, root: tk.Tk, path: str | None = None, settings: Settings | None = None, rwx=None,
                 data_dir: Path | None = None):
        self.root = root
        self.settings = settings or Settings()
        self.data_dir = data_dir
        self.theme = th.Theme(self.settings["theme"])
        set_current_theme(self.theme)
        th.apply(root, self.theme)
        self.path: str | None = None
        self.h = None
        self.overrides: Overrides | None = None
        self.rwx = rwx
        self.rwx_status: dict | None = None
        self.fetched: dict | None = None
        self._jobs: "queue.Queue" = queue.Queue()
        self._mtime = None
        self._load_icon()
        root.title(APP_NAME)
        root.minsize(980, 600)
        root.geometry(self.settings["geometry"] or "1320x820")
        self._build()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self._poll)
        root.after(2000, self._watch_file)
        if self.rwx is None:
            self._background(self._probe_rwx, self._rwx_ready)
        else:
            root.after(0, lambda: self._rwx_ready(self.rwx or None, None))
        if path:
            root.after(50, lambda: self.open_path(path))
        else:
            self.render()

    # ================================================================ construction
    def _load_icon(self):
        self.icon_image = None
        for path in (resource_path("tracemarx-64.png"),):
            try:
                self.icon_image = tk.PhotoImage(file=path)
                self.root.iconphoto(True, self.icon_image)
                break
            except tk.TclError:
                continue

    def _build(self):
        self._build_menu()
        tb = ttk.Frame(self.root, style="Toolbar.TFrame", padding=(8, 4))
        tb.pack(fill="x")
        self.toolbar = tb

        def btn(icon, cmd, tip, side="left"):
            b = IconButton(tb, icon, cmd, tip)
            b.pack(side=side, padx=1)
            return b
        btn("open", self.open_dialog, "Open a schematic (Ctrl+O)")
        self.rwx_open_btn = btn("revision", self.open_from_rwx, "Open a controlled schematic from RevisionWorx "
                                                                "(Ctrl+Shift+O)")
        ToolbarSeparator(tb).pack(side="left", padx=7, pady=3)
        btn("pdf", self.export_book, "Export the wiring book: wire list, terminal strips, hoses, devices (Ctrl+P)")
        btn("csv", self.export_csvs, "Export every list as CSV")
        btn("print", self.export_labels, "Export labels as PDF (Ctrl+L)")
        self.publish_btn = btn("export", self.publish, "Publish the wiring book and labels to RevisionWorx "
                                                       "(Ctrl+Shift+P)")
        ToolbarSeparator(tb).pack(side="left", padx=7, pady=3)
        self.viewer_btn = btn("eye", self.open_in_viewer, "Open the schematic in OpenSchematix Viewer")
        btn("theme", self.cycle_theme, "Next colour theme")
        btn("help", self.show_shortcuts, "Keyboard shortcuts (F1)")
        self.rwx_chip = tk.Label(tb, text="", padx=10, pady=3, bd=0, font=self.theme.fonts["small"])
        Tooltip(self.rwx_chip, "Revision status of the schematic in RevisionWorx")
        ttk.Separator(self.root).pack(fill="x")

        self.status_bar = ttk.Frame(self.root, style="Status.TFrame", padding=(10, 3))
        self.status_bar.pack(fill="x", side="bottom")
        self.status_left = ttk.Label(self.status_bar, text="", style="Status.TLabel")
        self.status_left.pack(side="left")
        self.status_right = ttk.Label(self.status_bar, text="", style="Status.TLabel")
        self.status_right.pack(side="right")
        ttk.Separator(self.root).pack(fill="x", side="bottom")

        body = ttk.Frame(self.root, style="Surface.TFrame", padding=(16, 12, 16, 8))
        body.pack(fill="both", expand=True)
        self.body = body
        head = ttk.Frame(body, style="Surface.TFrame")
        head.pack(fill="x")
        self.title_label = ttk.Label(head, text="", style="Title.TLabel")
        self.title_label.pack(anchor="w")
        self.sub_label = ttk.Label(head, text="", style="Muted.TLabel")
        self.sub_label.pack(anchor="w")
        self.summary_label = ttk.Label(head, text="", style="Surface.TLabel")
        self.summary_label.pack(anchor="w", pady=(4, 8))
        filt = ttk.Frame(body, style="Surface.TFrame")
        filt.pack(fill="x", pady=(0, 6))
        ttk.Label(filt, text="Filter", style="Muted.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filt, textvariable=self.filter_var, style="Search.TEntry", width=40)
        self.filter_entry.pack(side="left", padx=8)
        self.filter_var.trace_add("write", lambda *a: self.render_lists())
        self.nb = ttk.Notebook(body)
        self.nb.pack(fill="both", expand=True)
        self.trees = {}
        self.trees["wires"] = self._tree(("Wire", "From", "From sheet", "To", "To sheet", "Colour", "Size", "Length",
                                          "Note"), (60, 110, 120, 110, 120, 80, 70, 70, 200), "Wires")
        self.trees["terminals"] = self._tree(("Strip", "Terminal", "Description", "Side 1", "Side 2", "Sheet"),
                                             (60, 80, 160, 260, 260, 120), "Terminal strips")
        self.trees["hoses"] = self._tree(("Hose", "Function", "Medium", "From", "To", "Size", "Length", "Note"),
                                         (60, 110, 90, 110, 110, 80, 80, 200), "Hoses and tubes")
        self.trees["devices"] = self._tree(("Designator", "Description", "Part number", "Manufacturer", "Sheets"),
                                           (90, 240, 140, 140, 220), "Devices")
        self._build_labels_tab()
        self.trees["problems"] = self._tree(("Severity", "Problem", "Where"), (90, 640, 200), "Problems")
        for kind in ("wires", "hoses"):
            self.trees[kind].bind("<Double-1>", lambda e, k=kind: self.edit_run(k))
            self.trees[kind].bind("<Return>", lambda e, k=kind: self.edit_run(k))
        self.welcome = ttk.Frame(body, style="Surface.TFrame")
        self.toasts = ToastHost(body)

    def _tree(self, titles, widths, tab_title):
        frame = ttk.Frame(self.nb, style="Surface.TFrame")
        cols = [f"c{i}" for i in range(len(titles))]
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for c, t, w in zip(cols, titles, widths):
            tree.heading(c, text=t, anchor="w")
            tree.column(c, width=w, anchor="w", stretch=t in ("Note", "Side 1", "Side 2", "Description", "Problem"))
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        self.nb.add(frame, text=tab_title)
        return tree

    def _build_labels_tab(self):
        frame = ttk.Frame(self.nb, style="Surface.TFrame", padding=10)
        self.nb.add(frame, text="Labels")
        left = ttk.Frame(frame, style="Surface.TFrame")
        left.pack(side="left", fill="y")
        self.lab_vars = {k: tk.BooleanVar(value=v) for k, v in (("wires", True), ("wire_destination", True),
                                                                 ("terminals", True), ("devices", True),
                                                                 ("hoses", True), ("outlines", False))}
        ttk.Label(left, text="Print", style="Heading.TLabel").pack(anchor="w")
        for key, text in (("wires", "Wire markers (one for each end)"),
                          ("wire_destination", "   with where the other end goes"),
                          ("terminals", "Terminal and strip markers"), ("devices", "Device labels"),
                          ("hoses", "Hose and tube tags (both ends)")):
            ttk.Checkbutton(left, text=text, variable=self.lab_vars[key], command=self.render_labels).pack(anchor="w")
        ttk.Label(left, text="Label sheet", style="Heading.TLabel").pack(anchor="w", pady=(12, 0))
        self.layout_var = tk.StringVar(value=labels.LAYOUTS[self.settings["layout"]].name
                                       if self.settings["layout"] in labels.LAYOUTS else
                                       labels.LAYOUTS[labels.DEFAULT_LAYOUT].name)
        box = ttk.Combobox(left, textvariable=self.layout_var, state="readonly", width=48,
                           values=[lay.name for lay in labels.LAYOUTS.values()])
        box.pack(anchor="w")
        box.bind("<<ComboboxSelected>>", lambda e: self.render_labels())
        row = ttk.Frame(left, style="Surface.TFrame")
        row.pack(anchor="w", pady=6)
        ttk.Label(row, text="Copies", style="Surface.TLabel").pack(side="left")
        self.copies = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=20, textvariable=self.copies, width=4, command=self.render_labels).pack(
            side="left", padx=6)
        ttk.Label(row, text="Start at label", style="Surface.TLabel").pack(side="left", padx=(12, 0))
        self.start_at = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=80, textvariable=self.start_at, width=4, command=self.render_labels).pack(
            side="left", padx=6)
        ttk.Checkbutton(left, text="Draw outlines (test print on plain paper)", variable=self.lab_vars["outlines"],
                        command=self.render_labels).pack(anchor="w")
        ttk.Button(left, text="Export labels PDF…", style="Accent.TButton", command=self.export_labels).pack(
            anchor="w", pady=(14, 4))
        ttk.Button(left, text="Export for a label printer (CSV)…", command=self.export_labels_csv).pack(anchor="w")
        self.lab_count = ttk.Label(left, text="", style="Muted.TLabel", wraplength=320, justify="left")
        self.lab_count.pack(anchor="w", pady=(10, 0))
        self.preview = tk.Canvas(frame, highlightthickness=0, bd=0)
        self.preview.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.preview.bind("<Configure>", lambda e: self.render_labels())

    def _build_menu(self):
        mb = make_menu(self.root)
        self.root.configure(menu=mb)
        f = make_menu(mb)
        f.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_dialog)
        f.add_command(label="Open from RevisionWorx…", accelerator="Ctrl+Shift+O", command=self.open_from_rwx)
        self.recent_menu = make_menu(f)
        f.add_cascade(label="Recent", menu=self.recent_menu)
        f.add_separator()
        f.add_command(label="Export wiring book (PDF)…", accelerator="Ctrl+P", command=self.export_book)
        f.add_command(label="Export lists (CSV)…", command=self.export_csvs)
        f.add_command(label="Export labels (PDF)…", accelerator="Ctrl+L", command=self.export_labels)
        f.add_command(label="Export labels for a label printer (CSV)…", command=self.export_labels_csv)
        f.add_separator()
        f.add_command(label="Reload", accelerator="F5", command=self.reload)
        f.add_command(label="Close", accelerator="Ctrl+W", command=self.close_file)
        f.add_command(label="Quit", accelerator="Ctrl+Q", command=self.quit)
        mb.add_cascade(label="File", menu=f)
        v = make_menu(mb)
        self.theme_var = tk.StringVar(value=self.theme.name)
        for key, spec in th.THEMES.items():
            v.add_radiobutton(label=spec["label"], value=key, variable=self.theme_var,
                              command=lambda k=key: self.set_theme(k))
        mb.add_cascade(label="Theme", menu=v)
        r = make_menu(mb)
        r.add_command(label="Publish wiring book and labels", accelerator="Ctrl+Shift+P", command=self.publish)
        r.add_command(label="Open from RevisionWorx…", command=self.open_from_rwx)
        r.add_command(label="Show the schematic in RevisionWorx", command=self.show_in_rwx)
        mb.add_cascade(label="RevisionWorx", menu=r)
        self.rwx_menu = r
        h = make_menu(mb)
        h.add_command(label="Keyboard shortcuts", accelerator="F1", command=self.show_shortcuts)
        h.add_command(label="Project page", command=lambda: webbrowser.open(REPO_URL))
        h.add_separator()
        h.add_command(label=f"About {APP_NAME}", command=self.show_about)
        mb.add_cascade(label="Help", menu=h)
        self._fill_recent()

    def _bind_keys(self):
        for seq, fn in (("<Control-o>", self.open_dialog), ("<Control-O>", self.open_from_rwx),
                        ("<Control-Shift-o>", self.open_from_rwx), ("<Control-p>", self.export_book),
                        ("<Control-l>", self.export_labels), ("<Control-P>", self.publish),
                        ("<Control-Shift-p>", self.publish), ("<Control-f>", self.focus_filter),
                        ("<Control-w>", self.close_file), ("<Control-q>", self.quit), ("<F5>", self.reload),
                        ("<F1>", self.show_shortcuts)):
            self.root.bind_all(seq, lambda e, f=fn: (f(), "break")[1])

    # ================================================================ files
    def open_dialog(self):
        path = filedialog.askopenfilename(parent=self.root, title="Open a schematic", filetypes=FILE_TYPES,
                                          initialdir=os.path.dirname(self.path) if self.path else str(Path.home()))
        if path:
            self.open_path(path)

    def open_path(self, path: str, fetched: dict | None = None) -> bool:
        try:
            h = build(path)
        except HarnessError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {path}.\n\n{exc}", parent=self.root)
            return False
        except Exception as exc:  # noqa: BLE001 - a damaged file must give a message, never a crash
            messagebox.showerror(APP_NAME, f"Could not open {path}.\n\n{type(exc).__name__}: {exc}", parent=self.root)
            return False
        self.path, self.h, self.fetched = str(Path(path).resolve()), h, fetched
        try:
            self._mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            self._mtime = None
        doc_id = fetched["document_id"] if fetched else None
        self.overrides = Overrides(Overrides.identity_for(self.path, doc_id), self.data_dir)
        self.overrides.apply(h)
        if not fetched:
            self.settings.remember(self.path)
            self._fill_recent()
        self.root.title(f"{Path(self.path).name} — {APP_NAME}")
        self.render()
        self.refresh_rwx()
        return True

    def reload(self):
        if self.path:
            self.open_path(self.path, self.fetched)
            self.toasts.show("Reloaded.", "info", 1500)

    def close_file(self):
        self.path = self.h = self.overrides = self.fetched = self.rwx_status = None
        self.root.title(APP_NAME)
        self.render()
        self._render_rwx()

    def _watch_file(self):
        try:
            if self.path and self._mtime is not None:
                now = os.stat(self.path).st_mtime_ns
                if now != self._mtime:
                    self._mtime = now
                    self.reload()
        except OSError:
            pass
        self.root.after(2000, self._watch_file)

    def _fill_recent(self):
        self.recent_menu.delete(0, "end")
        recent = [p for p in self.settings["recent"] if isinstance(p, str)]
        for p in recent:
            self.recent_menu.add_command(label=p, command=lambda q=p: self.open_path(q))
        if not recent:
            self.recent_menu.add_command(label="(none)", state="disabled")

    # ================================================================ rendering
    def render(self):
        if self.h is None:
            self.nb.pack_forget()
            self.title_label.configure(text=APP_NAME)
            self.sub_label.configure(text="Wire lists, terminal strip plans, hose lists and labels from OpenSchematix "
                                          "schematics. Open a schematic to start.")
            self.summary_label.configure(text="")
            self._render_welcome()
            self.status_left.configure(text="No schematic open")
            self.status_right.configure(text="")
            return
        self.welcome.pack_forget()
        self.nb.pack(fill="both", expand=True)
        s = self.h.source
        self.title_label.configure(text=s.title)
        ident = " · ".join(x for x in (s.drawing_number, f"Rev {s.revision}" if s.revision else "",
                                        f"{len(s.sheets)} sheet(s)") if x)
        self.sub_label.configure(text=f"{s.name} · {ident}")
        c = self.h.counts()
        problems = f"{c['errors']} error(s), {c['warnings']} warning(s)" if (c["errors"] or c["warnings"]) else \
            "no problems found"
        self.summary_label.configure(text=f"{c['wires']} wire runs · {c['hoses']} hoses/tubes · {c['terminals']} "
                                          f"terminals on {c['strips']} strip(s) · {c['devices']} devices · {problems}")
        self.nb.tab(5, text=f"Problems ({c['errors'] + c['warnings']})" if c["errors"] + c["warnings"] else "Problems")
        self.render_lists()
        self.render_labels()
        src = f"Revision {self.fetched['revision']['label']} copy · " if self.fetched else ""
        self.status_left.configure(text=f"{src}{self.path}")
        self.status_right.configure(text="read-only: the schematic is never changed")

    def _render_welcome(self):
        for w in self.welcome.winfo_children():
            w.destroy()
        self.welcome.pack(fill="both", expand=True, pady=20)
        row = ttk.Frame(self.welcome, style="Surface.TFrame")
        row.pack(anchor="w")
        ttk.Button(row, text="Open a schematic…", style="Accent.TButton", command=self.open_dialog).pack(side="left")
        self.welcome_rwx_btn = ttk.Button(row, text="Open from RevisionWorx…", command=self.open_from_rwx)
        if self.rwx:
            self.welcome_rwx_btn.pack(side="left", padx=8)
        recent = [p for p in self.settings["recent"] if isinstance(p, str)]
        if recent:
            ttk.Label(self.welcome, text="Recent", style="Heading.TLabel").pack(anchor="w", pady=(24, 4))
            for p in recent[:8]:
                ttk.Button(self.welcome, text=p, style="Link.TButton", command=lambda q=p: self.open_path(q)).pack(
                    anchor="w")
        steps = ("1  Number the wires in OpenSchematix (Tools › Number wires).",
                 "2  Open the schematic here: wires, terminal strips, hoses and devices are listed for you.",
                 "3  Double-click a wire or hose to add its colour, size, length or a note.",
                 "4  Print the wiring book and the labels, or publish both to RevisionWorx.")
        ttk.Label(self.welcome, text="How it works", style="Heading.TLabel").pack(anchor="w", pady=(24, 4))
        for s in steps:
            ttk.Label(self.welcome, text=s, style="Surface.TLabel").pack(anchor="w", pady=1)

    def _match(self, values) -> bool:
        needle = self.filter_var.get().casefold().split()
        hay = " ".join(str(v) for v in values).casefold()
        return all(w in hay for w in needle)

    def render_lists(self):
        if self.h is None:
            return
        h = self.h
        rows = {
            "wires": [(r.label, r.a.ref, r.a.sheet, r.b.ref, r.b.sheet, r.colour, r.size, r.length, r.note)
                      for r in h.wires],
            "terminals": [(t.strip, t.number, t.description, "; ".join(t.side1), "; ".join(t.side2), t.sheet)
                          for ts in h.strips.values() for t in ts],
            "hoses": [(r.number, r.function, r.medium, r.a.ref, r.b.ref, r.size, r.length, r.note) for r in h.hoses],
            "devices": [(d.designator, d.description, d.part_number, d.manufacturer, ", ".join(d.sheets))
                        for d in h.devices],
            "problems": [(p.severity, p.message, p.where) for p in h.problems],
        }
        self._row_runs = {"wires": {}, "hoses": {}}
        for kind, tree in self.trees.items():
            tree.delete(*tree.get_children())
            source_runs = h.wires if kind == "wires" else h.hoses if kind == "hoses" else None
            for i, values in enumerate(rows[kind]):
                if self._match(values):
                    iid = tree.insert("", "end", values=values)
                    if source_runs is not None:
                        self._row_runs[kind][iid] = source_runs[i]
        if not h.problems:
            self.trees["problems"].insert("", "end", values=("ok", "No problems found.", ""))

    def label_options(self) -> labels.LabelOptions:
        v = self.lab_vars
        return labels.LabelOptions(wires=v["wires"].get(), wire_destination=v["wire_destination"].get(),
                                   terminals=v["terminals"].get(), devices=v["devices"].get(), hoses=v["hoses"].get(),
                                   copies=self._int(self.copies, 1, 20))

    def layout(self) -> labels.Layout:
        for lay in labels.LAYOUTS.values():
            if lay.name == self.layout_var.get():
                return lay
        return labels.LAYOUTS[labels.DEFAULT_LAYOUT]

    @staticmethod
    def _int(var, lo, hi):
        try:
            return max(lo, min(hi, int(var.get())))
        except (tk.TclError, ValueError):
            return lo

    def render_labels(self):
        cv = self.preview
        cv.delete("all")
        u = self.theme.ui
        cv.configure(bg=u["bg"])
        if self.h is None:
            return
        labs = labels.make_labels(self.h, self.label_options())
        lay = self.layout()
        self.settings["layout"] = lay.key
        start = self._int(self.start_at, 1, lay.per_page) - 1
        pages = (start + len(labs) + lay.per_page - 1) // lay.per_page if labs else 0
        self.lab_count.configure(text=f"{len(labs)} label(s) on {pages} sheet(s) of {lay.per_page}."
                                 + ("" if labs else " Nothing selected to print."))
        from ..pdf import PAGE_SIZES
        pw, ph = PAGE_SIZES[lay.paper]
        cw, ch = max(cv.winfo_width(), 200), max(cv.winfo_height(), 200)
        z = min((cw - 20) / pw, (ch - 20) / ph)
        ox, oy = (cw - pw * z) / 2, 10
        cv.create_rectangle(ox + 3, oy + 3, ox + pw * z + 3, oy + ph * z + 3, fill=u["border"], outline="")
        cv.create_rectangle(ox, oy, ox + pw * z, oy + ph * z, fill="#ffffff", outline=u["border_strong"])
        family = self.theme.fonts.get("family", "Helvetica")
        for lab, (page, x, y) in zip(labs, labels.label_rects(lay, len(labs), start)):
            if page > 0:
                break
            x0, y0, w, hgt = ox + x * z, oy + y * z, lay.width * z, lay.height * z
            cv.create_rectangle(x0, y0, x0 + w, y0 + hgt, outline="#c9ced6", dash=(2, 2))
            size1 = max(5, int(min(hgt * (0.45 if lab.line2 else 0.6), w / max(2, len(lab.line1)) * 1.6)))
            if lab.line2:
                cv.create_text(x0 + w / 2, y0 + hgt * 0.4, text=lab.line1, font=(family, -size1, "bold"), fill="#111")
                cv.create_text(x0 + w / 2, y0 + hgt * 0.75, text=lab.line2, width=w - 4,
                               font=(family, -max(5, int(hgt * 0.18))), fill="#333")
            else:
                cv.create_text(x0 + w / 2, y0 + hgt / 2, text=lab.line1, font=(family, -size1, "bold"), fill="#111")

    # ================================================================ editing (never the schematic)
    def edit_run(self, kind: str):
        tree = self.trees[kind]
        sel = tree.selection()
        if not sel or self.overrides is None:
            return
        run = self._row_runs[kind].get(sel[0])
        if run is None:
            return
        win = tk.Toplevel(self.root)
        win.title(f"{'Wire' if kind == 'wires' else 'Hose'} {run.label}")
        win.configure(bg=self.theme.ui["surface"])
        f = ttk.Frame(win, style="Surface.TFrame", padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{run.label}:  {run.a.ref} → {run.b.ref}", style="Title.TLabel").grid(row=0, column=0,
                                                                                          columnspan=2, sticky="w")
        ttk.Label(f, text="Kept by TraceMarx for this project; the schematic is not changed.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        fields = (("colour", "Colour"), ("size", "Size / cross-section" if kind == "wires" else "Size (inside "
                                                    "diameter)"), ("length", "Length"), ("note", "Note"))
        vars_ = {}
        for i, (key, text) in enumerate(fields, 2):
            ttk.Label(f, text=text, style="Surface.TLabel").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 12))
            vars_[key] = tk.StringVar(value=getattr(run, key))
            e = ttk.Entry(f, textvariable=vars_[key], width=36)
            e.grid(row=i, column=1, sticky="we", pady=3)
            if i == 2:
                e.focus_set()

        def save(*_):
            self.overrides.set(run, **{k: v.get() for k, v in vars_.items()})
            if self.overrides.warning:
                messagebox.showwarning(APP_NAME, self.overrides.warning, parent=win)
            win.destroy()
            self.render_lists()
        row = ttk.Frame(f, style="Surface.TFrame")
        row.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(row, text="Save", style="Accent.TButton", command=save).pack(side="right", padx=6)
        win.bind("<Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())
        win.transient(self.root)
        self._dialog = win
        return win, vars_, save

    def focus_filter(self):
        self.filter_entry.focus_set()

    # ================================================================ outputs
    def _ask_save(self, title, ext, types, suggested):
        path = filedialog.asksaveasfilename(parent=self.root, title=title, defaultextension=ext, filetypes=types,
                                            initialfile=suggested)
        if path and self.path and os.path.abspath(path) == os.path.abspath(self.path):
            messagebox.showerror(APP_NAME, "That is the schematic itself. TraceMarx never changes it; choose "
                                           "another name.", parent=self.root)
            return None
        return path

    def _rwx_label(self) -> str:
        st = self.rwx_status
        if self.fetched:
            r = self.fetched["revision"]
            return f"{(self.fetched.get('document') or {}).get('number', '')} rev {r['label']} ({r['state_name']})"
        if st and st.get("controlled"):
            latest = st["document"].get("latest") or {}
            return f"{st['document'].get('number')} rev {latest.get('label')} ({latest.get('state_name')})"
        return ""

    def export_book(self):
        if self.h is None:
            return
        path = self._ask_save("Export wiring book", ".pdf", (("PDF", "*.pdf"),), f"{Path(self.path).stem} - wiring.pdf")
        if not path:
            return
        try:
            Path(path).write_bytes(reports.wiring_book(self.h, self.settings["paper"], self._rwx_label()))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save: {exc}", parent=self.root)
            return
        self.toasts.show(f"Saved {Path(path).name}", "success")

    def export_csvs(self):
        if self.h is None:
            return
        folder = filedialog.askdirectory(parent=self.root, title="Folder for the CSV lists")
        if not folder:
            return
        try:
            files = reports.write_csvs(self.h, Path(folder))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save: {exc}", parent=self.root)
            return
        self.toasts.show(f"Saved {len(files)} CSV files in {folder}", "success")

    def export_labels(self):
        if self.h is None:
            return
        labs = labels.make_labels(self.h, self.label_options())
        if not labs:
            messagebox.showinfo(APP_NAME, "Nothing is selected to print (see the Labels tab).", parent=self.root)
            return
        path = self._ask_save("Export labels", ".pdf", (("PDF", "*.pdf"),), f"{Path(self.path).stem} - labels.pdf")
        if not path:
            return
        lay = self.layout()
        try:
            Path(path).write_bytes(labels.to_pdf(labs, lay, outlines=self.lab_vars["outlines"].get(),
                                                 start=self._int(self.start_at, 1, lay.per_page) - 1,
                                                 title=f"{self.h.source.title} - labels"))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save: {exc}", parent=self.root)
            return
        self.settings.save()
        self.toasts.show(f"Saved {len(labs)} labels to {Path(path).name}", "success")

    def export_labels_csv(self):
        if self.h is None:
            return
        labs = labels.make_labels(self.h, self.label_options())
        path = self._ask_save("Export labels for a label printer", ".csv", (("CSV", "*.csv"),),
                              f"{Path(self.path).stem} - labels.csv")
        if not path:
            return
        try:
            Path(path).write_text(labels.to_csv(labs), encoding="utf-8", newline="")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save: {exc}", parent=self.root)
            return
        self.toasts.show(f"Saved {Path(path).name}", "success")

    def open_in_viewer(self):
        if not self.path:
            return
        exe = shutil.which("openschematix-viewer")
        if not exe:
            messagebox.showinfo(APP_NAME, "OpenSchematix Viewer is not installed (or not on the PATH).",
                                parent=self.root)
            return
        subprocess.Popen([exe, self.path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)

    # ================================================================ RevisionWorx
    def _background(self, work, done):
        def run():
            try:
                self._jobs.put((done, work(), None))
            except Exception as exc:  # noqa: BLE001
                self._jobs.put((done, None, exc))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, value, error = self._jobs.get_nowait()
                try:
                    done(value, error)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    @staticmethod
    def _probe_rwx():
        from ..rwx_client import RevisionWorx
        rwx = RevisionWorx()
        return rwx if rwx.available() else None

    def _rwx_ready(self, rwx, _error):
        self.rwx = rwx or False
        state = "normal" if self.rwx else "disabled"
        for i in range(self.rwx_menu.index("end") + 1):
            self.rwx_menu.entryconfigure(i, state=state)
        if not self.rwx:
            self.rwx_open_btn.pack_forget()
            self.publish_btn.pack_forget()
        if self.h is None:
            self.render()
        self.refresh_rwx()

    def refresh_rwx(self):
        if not self.rwx or not self.path or self.fetched:
            self._render_rwx()
            return
        path = self.path
        self._background(lambda: self.rwx.status(path), self._rwx_status_done)

    def _rwx_status_done(self, status, error):
        self.rwx_status = status if error is None else {"error": str(error)}
        self._render_rwx()

    def rwx_summary(self) -> tuple[str, str]:
        if self.fetched:
            r = self.fetched["revision"]
            doc = self.fetched.get("document") or {}
            latest = (doc.get("latest") or {}).get("label")
            text = f"{doc.get('number') or 'Schematic'} rev {r['label']} · {r['state_name']} · copy"
            if latest and latest != r["label"]:
                return text + f" (latest is {latest})", "warning"
            return text, "success" if r["state"] == "released" else "info"
        st = self.rwx_status
        if not st or not self.path:
            return "", "muted"
        if st.get("error"):
            return "RevisionWorx: status unavailable", "warning"
        if not st.get("controlled"):
            return "Schematic not under revision control", "muted"
        from ..rwx_client import describe
        latest = st["document"].get("latest") or {}
        if st["file"]["state"] != "unchanged":
            return describe(st), "warning"
        return describe(st), "success" if latest.get("state") == "released" else "info"

    def _render_rwx(self):
        text, tone = self.rwx_summary()
        u = self.theme.ui
        if not text:
            self.rwx_chip.pack_forget()
            return
        color = {"success": u["success"], "warning": u["warning"], "info": u["info"]}.get(tone, u["muted"])
        self.rwx_chip.configure(text=text, fg=color, bg=u["toolbar"])
        self.rwx_chip.pack(side="right", padx=6)

    def show_in_rwx(self):
        if not self.rwx or not self.path:
            return
        try:
            if self.fetched:
                self.rwx.open(document=self.fetched["document_id"])
            else:
                self.rwx.open(file=self.path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)

    def open_from_rwx(self):
        if not self.rwx:
            messagebox.showinfo(APP_NAME, "RevisionWorx is not installed (or was not found).", parent=self.root)
            return
        from .rwx_dialog import OpenFromRevisionWorx
        OpenFromRevisionWorx(self)

    def open_revision(self, doc: dict, revision: str | None = None, released: bool = False) -> bool:
        cache = Path(tempfile.gettempdir()) / "tracemarx" / doc["id"]
        try:
            got = self.rwx.fetch(doc["id"], str(cache), revision=revision, released=released)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return False
        got["document"] = doc
        return self.open_path(got["path"], fetched=got)

    def publish(self, confirm: bool = True):
        """Wiring book + labels into RevisionWorx, next to the schematic."""
        if not self.rwx or self.h is None:
            return None
        from ..publish import PublishRefused, plan, publish
        source = self.path
        project = None
        if self.fetched:
            source = self.fetched["document"]["path"] or self.path
        try:
            p = plan(self.rwx, self.h, source, project)
        except PublishRefused as exc:
            messagebox.showinfo("Publish to RevisionWorx", str(exc), parent=self.root)
            return None
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return None
        allow = False
        if p["file_state"] not in (None, "unchanged") and not self.fetched:
            if confirm and not messagebox.askyesno(
                    "Publish to RevisionWorx", f"The schematic differs from {p['source']}: the outputs would not match "
                    "a recorded revision. Publish anyway?", parent=self.root):
                return None
            allow = True
        numbers = ", ".join(d["number"] for d in p["documents"])
        if confirm and not messagebox.askyesno(
                "Publish to RevisionWorx", f"Publish the wiring book and labels made from {p['source']} as {numbers}?\n\n"
                "Existing documents get a new revision; unchanged outputs record nothing.", parent=self.root):
            return None
        try:
            results = publish(self.rwx, self.h, source, project, self.settings["paper"], self.layout().key,
                              allow_unrecorded=allow or bool(self.fetched), label_options=self.label_options())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            return None
        summary = ", ".join(f"{r['document']['number']} rev {r['document']['latest']['label']} ({r['result']})"
                            for r in results)
        self.toasts.show(f"Published: {summary}", "success", 6000)
        return results

    # ================================================================ themes and dialogs
    def set_theme(self, name):
        self.theme = th.Theme(name)
        set_current_theme(self.theme)
        th.apply(self.root, self.theme)
        th.recolor_tree(self.root, self.theme)
        self.theme_var.set(name)
        self.settings["theme"] = name
        self.settings.save()
        self.rwx_chip.configure(font=self.theme.fonts["small"])
        self._render_rwx()
        self.render_labels()

    def cycle_theme(self):
        names = list(th.THEMES)
        self.set_theme(names[(names.index(self.theme.name) + 1) % len(names)] if self.theme.name in names else names[0])
        self.toasts.show(f"Theme: {self.theme.label}", "info", 1500)

    def show_shortcuts(self):
        win = tk.Toplevel(self.root)
        win.title("Keyboard shortcuts")
        win.configure(bg=self.theme.ui["surface"])
        f = ttk.Frame(win, style="Surface.TFrame", padding=18)
        f.pack(fill="both", expand=True)
        for i, (keys, what) in enumerate(SHORTCUTS):
            ttk.Label(f, text=keys, style="Bold.TLabel").grid(row=i, column=0, sticky="w", padx=(0, 18), pady=2)
            ttk.Label(f, text=what, style="Surface.TLabel").grid(row=i, column=1, sticky="w", pady=2)
        ttk.Button(f, text="Close", command=win.destroy).grid(row=len(SHORTCUTS), column=1, sticky="e", pady=(12, 0))
        win.transient(self.root)
        return win

    def show_about(self):
        t = self.theme
        win = tk.Toplevel(self.root)
        win.title(f"About {APP_NAME}")
        win.configure(bg=t.ui["surface"])
        f = ttk.Frame(win, style="Surface.TFrame", padding=20)
        f.pack(fill="both", expand=True)
        top = ttk.Frame(f, style="Surface.TFrame")
        top.pack(fill="x")
        if self.icon_image is not None:
            tk.Label(top, image=self.icon_image, bg=t.ui["surface"]).pack(side="left", padx=(0, 16))
        col = ttk.Frame(top, style="Surface.TFrame")
        col.pack(side="left", fill="x")
        ttk.Label(col, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(col, text=f"Version {VERSION}", style="Surface.TLabel").pack(anchor="w")
        ttk.Label(col, text="Wire lists, terminal strips, hose lists and labels", style="Muted.TLabel").pack(anchor="w")
        body = (f"{COPYRIGHT}\n\n"
                f"Free and open-source software, licensed under the {LICENSE_NAME}. You may use, study, share and "
                "modify it under the terms of that licence. It comes with ABSOLUTELY NO WARRANTY.\n\n"
                "TraceMarx reads OpenSchematix schematics and never changes them. The run order of each wire list is a "
                "suggestion; the schematic decides what is connected. Check wiring against the schematic and have "
                "safety-related work verified by a qualified person.\n\n"
                "No ads, no telemetry, no accounts and no feature limits. If it is useful to you, you can support "
                "its development voluntarily.")
        ttk.Label(f, text=body, style="Surface.TLabel", wraplength=520, justify="left").pack(anchor="w", pady=(16, 8))
        links = ttk.Frame(f, style="Surface.TFrame")
        links.pack(anchor="w")
        for text, url in (("Support development (Ko-fi)", DONATE_URL), ("Project page", REPO_URL),
                          ("Licence text", LICENSE_URL)):
            ttk.Button(links, text=text, style="Link.TButton", command=lambda u=url: webbrowser.open(u)).pack(
                side="left", padx=(0, 8))
        ttk.Label(f, text=f"Python {platform.python_version()} · Tk {self.root.tk.call('info', 'patchlevel')} · "
                          f"{platform.system()} {platform.release()}", style="Faint.TLabel").pack(anchor="w", pady=(10, 0))
        ttk.Button(f, text="Close", command=win.destroy).pack(anchor="e", pady=(12, 0))
        win.transient(self.root)
        return win

    def quit(self):
        try:
            self.settings["geometry"] = self.root.geometry()
            self.settings.save()
        except tk.TclError:
            pass
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("--version", "-V"):
        print(f"{APP_NAME} {VERSION}")
        return 0
    if len(argv) == 2 and argv[0] == "--self-test":
        from .selftest import run
        return run(Path(argv[1]))
    if argv and argv[0] in ("check", "report", "labels", "publish", "-h", "--help"):
        from ..cli import main as cli_main
        return cli_main(argv)
    root = tk.Tk()
    TraceMarxApp(root, argv[0] if argv else None)
    root.mainloop()
    return 0
