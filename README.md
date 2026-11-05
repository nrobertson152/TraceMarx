# TraceMarx

Wire lists, terminal strip plans, hose lists and labels from [OpenSchematix](https://github.com/nrobertson152/OpenSchematix)
schematics, with optional [RevisionWorx](https://github.com/nrobertson152/RevisionWorx) revision control.

TraceMarx reads a schematic and **never changes it**. Wire numbers, designators, terminal numbers and port types
come from the schematic; TraceMarx works out what to build from them:

- **Wire list:** every physical wire run (end to end, in drawing order), with both ends and sheets.
- **Terminal strip plan:** each strip (`X1`, `X2`, …) terminal by terminal, with what lands on each side.
- **Hose and tube list:** hydraulic (`HS1`, …) and pneumatic (`TB1`, …) runs with their function (pressure, tank,
  work, pilot, drain).
- **Device list:** designator, description, part number, manufacturer and sheets.
- **Problems:** duplicate wire numbers, unnumbered wires, connections with only one end, duplicate terminals.
- **Labels:** wire markers for both ends (optionally showing where the other end goes), terminal and strip markers,
  device labels and hose tags, on standard label sheets (A4 65/24, Letter 30/80) or exported as CSV for label-printer
  software. You can start on a partly used sheet and print outlines on plain paper to check alignment.
- **Wiring book:** one PDF with all the lists, stamped with the schematic's SHA-256 and RevisionWorx revision.

Double-click a wire or hose to add its colour, size, length or a note. These are kept per user, outside the schematic,
and follow the runs even if wires are renumbered (Linux `~/.local/share/tracemarx`, Windows
`%APPDATA%\TraceMarx`).

## RevisionWorx

When RevisionWorx is installed, TraceMarx shows the schematic's revision and state and can:

- open any revision of a controlled schematic (latest, released or earlier) as a read-only copy;
- **publish** the wiring book and labels as documents `<number>-WL` and `<number>-LBL` beside the schematic. The
  revision message records the source revision and file hash. Republishing identical outputs records nothing. A
  schematic that differs from its recorded revision is refused unless you confirm.

TraceMarx talks to RevisionWorx only through `revisionworx link` (`tracemarx/rwx_client.py`, a copy of
RevisionWorx's `contrib/rwx_client.py`). Without RevisionWorx those commands are hidden.

## Install and run

```sh
python -m pip install .
tracemarx-gui [SCHEMATIC]                 # the window
tracemarx check   PROJECT.schematix       # problems; exit 1 if there are errors
tracemarx report  PROJECT.schematix --out DIR [--paper A4|Letter]
tracemarx labels  PROJECT.schematix --out labels.pdf [--layout a4-65] [--outlines] [--csv]
tracemarx publish PROJECT.schematix       # into RevisionWorx
```

Standalone Windows and Linux builds are produced by the Package workflow.

Requirements: Python 3.10–3.13 with Tkinter, and OpenSchematix (installed automatically, pinned to a tested commit).
Fully offline: no network access, accounts, telemetry or ads.

## Development

```sh
python -m pip install -e ".[dev]"         # Linux also: sudo apt-get install -y xvfb python3-tk
xvfb-run -a python -m pytest -q -ra
xvfb-run -a tracemarx-gui --self-test report.txt
```

The run order in a wire list is a suggestion; the schematic decides what is connected. Check wiring against the
schematic and have safety-related work verified by a qualified person.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
