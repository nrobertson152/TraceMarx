# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
pytestmark = pytest.mark.gui

from tracemarx.gui import app as appmod  # noqa: E402
from tracemarx.gui.settings import Settings  # noqa: E402


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def app(root, tmp_path):
    a = appmod.TraceMarxApp(root, None, settings=Settings(tmp_path / "cfg"), rwx=False, data_dir=tmp_path / "data")
    root.update()
    return a


def rows(app, kind):
    t = app.trees[kind]
    return [t.item(i, "values") for i in t.get_children()]


def test_welcome_then_open(app, demo, root):
    assert app.h is None and not app.nb.winfo_ismapped()
    assert app.open_path(str(demo))
    root.update()
    assert app.title_label.cget("text") == "Conveyor station 3"
    assert len(rows(app, "wires")) == 13 and len(rows(app, "hoses")) == 9
    assert rows(app, "problems")[0][1] == "No problems found."
    assert "demo_project.schematix" in root.title()
    assert app.settings["recent"][0] == str(demo.resolve())


def test_damaged_file_shows_a_message_not_a_crash(app, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(appmod.messagebox, "showerror", lambda *a, **k: shown.append(a))
    bad = tmp_path / "bad.schematix"
    bad.write_bytes(b"x")
    assert not app.open_path(str(bad))
    assert shown and app.h is None


def test_filter_narrows_every_list(app, demo, root):
    app.open_path(str(demo))
    app.filter_var.set("K1:A2")
    root.update()
    assert rows(app, "wires") and all("K1:A2" in " ".join(r) for r in rows(app, "wires"))
    app.filter_var.set("")
    assert len(rows(app, "wires")) == 13


def test_edit_a_wire_is_kept_for_next_time_and_never_touches_the_schematic(app, demo, root, tmp_path):
    before = demo.read_bytes()
    app.open_path(str(demo))
    tree = app.trees["wires"]
    tree.selection_set(tree.get_children()[0])
    win, vars_, save = app.edit_run("wires")
    vars_["colour"].set("BN")
    vars_["size"].set("0.75")
    save()
    assert rows(app, "wires")[0][5:7] == ("BN", "0.75")
    assert demo.read_bytes() == before
    again = appmod.TraceMarxApp(tk.Toplevel(root), None, settings=Settings(tmp_path / "cfg2"), rwx=False,
                                data_dir=tmp_path / "data")
    again.open_path(str(demo))
    assert again.h.wires[0].colour == "BN"


def test_exports_refuse_the_schematic_path(app, demo, monkeypatch):
    app.open_path(str(demo))
    errors = []
    monkeypatch.setattr(appmod.filedialog, "asksaveasfilename", lambda **k: str(demo))
    monkeypatch.setattr(appmod.messagebox, "showerror", lambda *a, **k: errors.append(a))
    before = demo.read_bytes()
    app.export_book()
    app.export_labels()
    app.export_labels_csv()
    assert len(errors) == 3 and demo.read_bytes() == before


def test_exports_write_files(app, demo, tmp_path, monkeypatch):
    app.open_path(str(demo))
    targets = iter([tmp_path / "book.pdf", tmp_path / "labels.pdf", tmp_path / "labels.csv"])
    monkeypatch.setattr(appmod.filedialog, "asksaveasfilename", lambda **k: str(next(targets)))
    monkeypatch.setattr(appmod.filedialog, "askdirectory", lambda **k: str(tmp_path / "csv"))
    app.export_book()
    app.export_labels()
    app.export_labels_csv()
    app.export_csvs()
    assert (tmp_path / "book.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "labels.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "labels.csv").exists() and len(list((tmp_path / "csv").glob("*.csv"))) == 4


def test_label_options_and_layout(app, demo, root):
    app.open_path(str(demo))
    total = len(appmod.labels.make_labels(app.h, app.label_options()))
    assert app.lab_count.cget("text").startswith(f"{total} label(s)")
    for key in ("wires", "terminals", "hoses"):
        app.lab_vars[key].set(False)
    app.layout_var.set(appmod.labels.LAYOUTS["a4-24"].name)
    app.render_labels()
    assert app.layout().key == "a4-24" and app.settings["layout"] == "a4-24"
    assert app.lab_count.cget("text").startswith("16 label(s) on 1 sheet(s) of 24")


def test_revisionworx_commands_hidden_without_it(app, root):
    root.update()
    assert not app.rwx_open_btn.winfo_ismapped() and not app.publish_btn.winfo_ismapped()
    assert app.rwx_menu.entrycget(0, "state") == "disabled"
    assert app.publish() is None


def test_themes_about_and_shortcuts(app, demo, root):
    app.open_path(str(demo))
    first = app.theme.name
    app.cycle_theme()
    assert app.theme.name != first and app.settings["theme"] == app.theme.name
    about = app.show_about()
    shortcuts = app.show_shortcuts()
    root.update()
    assert about.winfo_exists() and shortcuts.winfo_exists()
    about.destroy()
    shortcuts.destroy()


def test_close_returns_to_welcome(app, demo, root):
    app.open_path(str(demo))
    app.close_file()
    root.update()
    assert app.h is None and app.welcome.winfo_ismapped()


def test_self_test(tmp_path):
    from tracemarx.gui.selftest import run
    report = tmp_path / "r.txt"
    try:
        code = run(report)
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    assert code == 0, report.read_text()
    assert "self-test PASSED" in report.read_text()
