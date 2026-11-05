# SPDX-License-Identifier: GPL-3.0-or-later
"""What the person adds that a schematic does not hold: wire colour and size, hose size and length, notes.

Stored per user, never in the schematic (TraceMarx never writes it): one JSON file per project in TraceMarx's data
folder, keyed by the project's RevisionWorx document id when it has one (so it follows the document across
revisions and copies) and otherwise by the file's path. Each run is keyed by its two ends, so renumbering wires
keeps the entries."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

FIELDS = ("colour", "size", "length", "note")


def data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "TraceMarx"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "tracemarx"


class Overrides:
    def __init__(self, identity: str, folder: Path | None = None):
        self.identity = identity
        name = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        self.path = (Path(folder) if folder else data_dir()) / "projects" / f"{name}.json"
        self.runs: dict[str, dict[str, str]] = {}
        self.warning: str | None = None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("runs"), dict):
                self.runs = {k: {f: str(v.get(f, "")) for f in FIELDS if v.get(f)}
                             for k, v in data["runs"].items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    @staticmethod
    def identity_for(path: str, rwx_document_id: str | None = None) -> str:
        return f"rwx:{rwx_document_id}" if rwx_document_id else f"file:{os.path.normcase(os.path.abspath(path))}"

    def apply(self, harness) -> None:
        for run in harness.runs:
            for f, v in self.runs.get(run.key, {}).items():
                setattr(run, f, v)

    def set(self, run, **values) -> None:
        entry = {f: str(values.get(f, getattr(run, f)) or "").strip() for f in FIELDS}
        for f, v in entry.items():
            setattr(run, f, v)
        entry = {f: v for f, v in entry.items() if v}
        if entry:
            self.runs[run.key] = entry
        else:
            self.runs.pop(run.key, None)
        self.save()

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"identity": self.identity, "runs": self.runs}, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            self.warning = None
            return True
        except OSError as exc:
            self.warning = f"Your edits could not be saved in {self.path.parent}: {exc.strerror or exc}"
            return False
