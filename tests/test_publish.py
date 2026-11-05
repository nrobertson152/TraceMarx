# SPDX-License-Identifier: GPL-3.0-or-later
"""End to end with the real RevisionWorx (installed by the [dev] extra): the wiring book and labels are published
next to a controlled schematic, republishing unchanged outputs records nothing, and a schematic that differs from
its recorded revision is refused."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("revisionworx.integration")

from tracemarx.cli import main  # noqa: E402
from tracemarx.harness import build  # noqa: E402
from tracemarx.publish import PublishRefused, plan, publish  # noqa: E402
from tracemarx.rwx_client import RevisionWorx  # noqa: E402

DEMO = Path(__file__).with_name("demo_project.schematix")


def rwx_cli(*args):
    out = subprocess.run([sys.executable, "-m", "revisionworx", *args], capture_output=True, text=True,
                         env=dict(os.environ), timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    return out.stdout


@pytest.fixture
def rwx(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    monkeypatch.setenv("REVISIONWORX_WORKSPACE", str(ws))
    monkeypatch.setenv("REVISIONWORX_CLI", "")
    rwx_cli("init", str(ws), "--name", "TraceMarx test")
    (ws / "Projects").mkdir()
    rwx_cli("root", "add", "Projects", str(ws / "Projects"))
    rwx_cli("project", "new", "--name", "Conveyor", "--number", "CS3")
    client = RevisionWorx()
    client.command = [sys.executable, "-m", "revisionworx"]
    assert client.available()
    return client


@pytest.fixture
def controlled(tmp_path, rwx):
    src = tmp_path / "conveyor.schematix"
    shutil.copy(DEMO, src)
    made = rwx.publish(str(src), project="CS3", number="DWG-100", title="Conveyor schematic", message="Initial")
    return Path(made["document"]["path"])


def test_publish_creates_then_records_nothing_when_unchanged(rwx, controlled):
    h = build(str(controlled))
    p = plan(rwx, h, str(controlled))
    assert [d["number"] for d in p["documents"]] == ["DWG-100-WL", "DWG-100-LBL"]
    assert p["file_state"] == "unchanged" and p["source"].startswith("DWG-100 rev A")
    first = publish(rwx, h, str(controlled))
    assert [r["document"]["number"] for r in first] == ["DWG-100-WL", "DWG-100-LBL"]
    assert all(r["document"]["latest"]["label"] == "A" for r in first)
    again = publish(rwx, build(str(controlled)), str(controlled))
    assert [r["result"] for r in again] == ["unchanged", "unchanged"]
    docs = {d["number"] for d in rwx.documents()}
    assert {"DWG-100", "DWG-100-WL", "DWG-100-LBL"} <= docs
    message = rwx.document(first[0]["document"]["id"])["history"][-1]
    assert "DWG-100 rev A" in json.dumps(message)


def test_changed_schematic_is_refused_unless_allowed(rwx, controlled, tmp_path):
    data = controlled.read_bytes()
    controlled.chmod(0o644)
    controlled.write_bytes(data + b"\0")  # differs from the recorded revision (and is no longer a valid zip tail)
    h = build(str(tmp_path / "conveyor.schematix"))
    with pytest.raises(PublishRefused):
        publish(rwx, h, str(controlled))
    results = publish(rwx, h, str(controlled), allow_unrecorded=True)
    assert len(results) == 2


def test_uncontrolled_schematic_needs_a_project(rwx, demo, capsys):
    h = build(str(demo))
    with pytest.raises(PublishRefused):
        plan(rwx, h, str(demo))
    assert plan(rwx, h, str(demo), project="CS3")["documents"][0]["number"] == "CS3-E-WL"
    assert main(["publish", str(demo)]) == 2
    assert "not in RevisionWorx" in capsys.readouterr().err


@pytest.mark.gui
def test_window_shows_status_and_publishes(rwx, controlled, tmp_path):
    tk = pytest.importorskip("tkinter")
    from tracemarx.gui.app import TraceMarxApp
    from tracemarx.gui.settings import Settings
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        app = TraceMarxApp(root, None, settings=Settings(tmp_path / "cfg"), rwx=rwx, data_dir=tmp_path / "data")
        assert app.open_path(str(controlled))
        for _ in range(500):
            root.update()
            app._poll()
            if app.rwx_status:
                break
            root.after(20)
        assert app.rwx_summary() == ("DWG-100 rev A · Work in Progress", "info")
        results = app.publish(confirm=False)
        assert [r["document"]["number"] for r in results] == ["DWG-100-WL", "DWG-100-LBL"]
    finally:
        root.destroy()
