# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: the same outputs as the window, for scripts and batch work.

    tracemarx check   PROJECT.schematix                     # problems that affect wiring and labels
    tracemarx report  PROJECT.schematix [--out DIR] [--paper A4|Letter]   # wiring book PDF + CSV tables
    tracemarx labels  PROJECT.schematix --out labels.pdf [--layout a4-65] [--outlines] [--csv]
    tracemarx publish PROJECT.schematix [--project P] [--out DIR]          # report + labels into RevisionWorx

Exit status: 0 ok, 1 problems reported (check) or nothing to do, 2 error."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, VERSION
from .harness import HarnessError, build
from .overrides import Overrides


def _load(path: str, rwx=None):
    h = build(path)
    doc_id = None
    if rwx is not None:
        try:
            st = rwx.status(path)
            doc_id = st["document"]["id"] if st.get("controlled") else None
        except Exception:  # noqa: BLE001
            doc_id = None
    Overrides(Overrides.identity_for(path, doc_id)).apply(h)
    return h


def main(argv: list[str] | None = None) -> int:
    from . import labels, reports
    p = argparse.ArgumentParser(prog="tracemarx", description=f"{APP_NAME} {VERSION}: wire lists, terminal plans, "
                                "hose lists and labels from OpenSchematix projects")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("project")
    s = sub.add_parser("report")
    s.add_argument("project")
    s.add_argument("--out", default=".")
    s.add_argument("--paper", choices=["A4", "Letter"], default="A4")
    s = sub.add_parser("labels")
    s.add_argument("project")
    s.add_argument("--out", required=True)
    s.add_argument("--layout", choices=sorted(labels.LAYOUTS), default=labels.DEFAULT_LAYOUT)
    s.add_argument("--outlines", action="store_true", help="draw label outlines (test print on plain paper)")
    s.add_argument("--csv", action="store_true", help="write a CSV for label-printer software instead of a PDF")
    s.add_argument("--no-destination", action="store_true", help="wire markers show only the wire number")
    s.add_argument("--copies", type=int, default=1)
    s = sub.add_parser("publish")
    s.add_argument("project")
    s.add_argument("--project-id", dest="rwx_project", help="RevisionWorx project (default: the schematic's)")
    s.add_argument("--paper", choices=["A4", "Letter"], default="A4")
    s.add_argument("--layout", choices=sorted(labels.LAYOUTS), default=labels.DEFAULT_LAYOUT)
    s.add_argument("--out", default=None, help="also keep the generated files here")
    a = p.parse_args(argv)
    try:
        if a.cmd == "check":
            h = _load(a.project)
            for prob in h.problems:
                print(f"{prob.severity.upper():8} {prob.message}" + (f"  ({prob.where})" if prob.where else ""))
            c = h.counts()
            print(f"{c['wires']} wire run(s), {c['hoses']} hose(s), {c['terminals']} terminal(s) on {c['strips']} "
                  f"strip(s), {c['devices']} device(s); {c['errors']} error(s), {c['warnings']} warning(s)")
            return 1 if c["errors"] else 0
        if a.cmd == "report":
            h = _load(a.project)
            out = Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            pdf = out / f"{Path(a.project).stem} - wiring.pdf"
            pdf.write_bytes(reports.wiring_book(h, a.paper))
            for f in [pdf, *reports.write_csvs(h, out)]:
                print(f)
            return 0
        if a.cmd == "labels":
            h = _load(a.project)
            labs = labels.make_labels(h, labels.LabelOptions(wire_destination=not a.no_destination, copies=a.copies))
            if not labs:
                print("nothing to label", file=sys.stderr)
                return 1
            out = Path(a.out)
            if a.csv:
                out.write_text(labels.to_csv(labs), encoding="utf-8", newline="")
            else:
                out.write_bytes(labels.to_pdf(labs, labels.LAYOUTS[a.layout], outlines=a.outlines,
                                              title=f"{h.source.title} - labels"))
            print(f"{out}  ({len(labs)} labels)")
            return 0
        if a.cmd == "publish":
            from .publish import publish
            from .rwx_client import RevisionWorx
            rwx = RevisionWorx()
            if not rwx.available():
                print("error: RevisionWorx is not installed (or not found)", file=sys.stderr)
                return 2
            h = _load(a.project, rwx)
            results = publish(rwx, h, a.project, project=a.rwx_project, paper=a.paper, layout=a.layout,
                              keep_in=Path(a.out) if a.out else None)
            for r in results:
                d = r["document"]
                print(f"{d['number']}  rev {d['latest']['label']}  {r['result']}")
            return 0
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        from .publish import PublishRefused
        from .rwx_client import RwxError
        if isinstance(exc, (RwxError, PublishRefused, OSError, ValueError)):
            print(f"error: {exc}", file=sys.stderr)
            return 2
        raise
    return 2


if __name__ == "__main__":
    sys.exit(main())
