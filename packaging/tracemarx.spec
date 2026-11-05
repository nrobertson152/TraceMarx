# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller spec: one folder with tracemarx(.exe). Build: pyinstaller packaging/tracemarx.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

here = os.path.abspath(SPECPATH)
datas = collect_data_files("openschematix") + collect_data_files("tracemarx")
hidden = collect_submodules("openschematix") + collect_submodules("tracemarx")
a = Analysis([os.path.join(here, "entry", "tracemarx_gui.py")], pathex=[os.path.dirname(here)], datas=datas,
             hiddenimports=hidden, excludes=["PySide6", "PyQt5", "PyQt6"])
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="tracemarx", console=False, upx=False,
          icon=os.path.join(os.path.dirname(here), "tracemarx", "resources", "tracemarx.ico"))
COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="TraceMarx")
