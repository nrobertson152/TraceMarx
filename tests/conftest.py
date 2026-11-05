# SPDX-License-Identifier: GPL-3.0-or-later
import json
import shutil
import zipfile
from pathlib import Path

import pytest

DEMO = Path(__file__).with_name("demo_project.schematix")


@pytest.fixture(autouse=True)
def private_profile(tmp_path, monkeypatch):
    """Settings and overrides never touch the real user profile."""
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "profile" / var))


@pytest.fixture
def demo(tmp_path) -> Path:
    p = tmp_path / "demo_project.schematix"
    shutil.copy(DEMO, p)
    return p


def edited(src: Path, dest: Path, change) -> Path:
    """A copy of ``src`` with each sheet's JSON passed through ``change(sheet_dict)``."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("sheets/") and info.filename.endswith(".json"):
                sheet = json.loads(data)
                change(sheet)
                data = json.dumps(sheet).encode("utf-8")
            zout.writestr(info, data)
    return dest


@pytest.fixture
def edit(tmp_path):
    return lambda change, name="edited.schematix": edited(DEMO, tmp_path / name, change)
