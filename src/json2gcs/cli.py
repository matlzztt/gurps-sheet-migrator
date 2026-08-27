"""Command line entry point.

Only ``inspect`` exists so far: it reads a Foundry export (and optionally the
base GCS sheet), reports what it found, and lists anything that would make a
conversion unsafe.  ``convert`` arrives with the reconciler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import foundry, jsonio, tid
from . import __version__


def _find_base(actor: foundry.Actor, near: Path) -> Path | None:
    """Look for the GCS file this actor was imported from.

    ``system.additionalresources.importname`` records the original filename, so
    a sheet sitting next to the export can be found without being named.
    """
    name = actor.import_name
    if not name:
        return None
    for candidate in (near.parent / name, near.parent / Path(name).name):
        if candidate.is_file():
            return candidate
    return None


def _gcs_rows(sheet: dict) -> dict[str, dict]:
    """Index every row in a GCS sheet by TID, recursing into containers."""
    index: dict[str, dict] = {}

    def walk(rows) -> None:
        for row in rows or ():
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                index[row["id"]] = row
                walk(row.get("children"))

    for section in ("traits", "skills", "spells", "equipment", "other_equipment", "notes"):
        walk(sheet.get(section))
    return index


def cmd_inspect(args: argparse.Namespace) -> int:
    export = Path(args.export)
    actor = foundry.load(export)

    print(f"Foundry export : {export}")
    print(f"  character    : {actor.name}")
    print(f"  GGA version  : {actor.system_version or '(unknown)'}")
    print(f"  Foundry core : {actor.core_version or '(unknown)'}")
    if actor.last_import:
        print(f"  last import  : {actor.last_import}")
    if actor.import_name:
        print(f"  imported from: {actor.import_name}")

    rows = list(actor.rows())
    print()
    print("  section              rows  containers  added in Foundry")
    sections = [
        ("traits", actor.traits),
        ("skills", actor.skills),
        ("spells", actor.spells),
        ("equipment (carried)", actor.carried),
        ("equipment (other)", actor.other),
        ("notes", actor.notes),
    ]
    for label, tops in sections:
        flat = [r for top in tops for r in top.walk()]
        containers = sum(1 for r in flat if r.is_container)
        added = sum(1 for r in flat if r.added_in_foundry)
        print(f"  {label:<20} {len(flat):>4}  {containers:>10}  {added:>16}")
    print(f"  {'melee weapons':<20} {len(actor.melee()):>4}")
    print(f"  {'ranged weapons':<20} {len(actor.ranged()):>4}")

    base_path = Path(args.base) if args.base else _find_base(actor, export)
    if base_path:
        sheet = jsonio.loads(jsonio.read_text(base_path))
        base_rows = _gcs_rows(sheet)
        found = "given" if args.base else "auto-detected"
        print()
        print(f"Base GCS sheet : {base_path}  ({found})")
        print(f"  data version : {sheet.get('version')}")
        print(f"  total points : {sheet.get('total_points')}")
        print(f"  modified     : {sheet.get('modified_date')}")

        export_tids = {r.tid for r in rows if r.tid}
        matched = export_tids & base_rows.keys()
        print()
        print(f"  matched by TID    : {len(matched)}")
        print(f"  only in Foundry   : {len(export_tids - base_rows.keys())}")
        print(f"  only in base sheet: {len(base_rows.keys() - export_tids)}")
        for missing in sorted(base_rows.keys() - export_tids):
            row = base_rows[missing]
            label = row.get("name") or row.get("description") or "?"
            print(f"      {missing}  {label}   ({tid.kind_of(missing)})")
        if base_rows.keys() - export_tids:
            print(
                "    ^ ambiguous: either deleted in Foundry or added to GCS after\n"
                "      the export. See docs/06-architecture.md 6.3."
            )
    elif actor.import_name:
        print()
        print(
            f"Base GCS sheet : not found — looked for {actor.import_name!r} beside "
            "the export.\n                 Pass --base to point at it."
        )

    if actor.warnings:
        print()
        print("Warnings:")
        for warning in actor.warnings:
            print(f"  ! {warning}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="json2gcs",
        description="Convert a Foundry VTT GURPS actor export back into a GCS sheet.",
    )
    parser.add_argument("--version", action="version", version=f"json2gcs {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser(
        "inspect",
        help="report what is in an export, and how it lines up with a base sheet",
    )
    inspect.add_argument("export", help="the Foundry actor export (.json)")
    inspect.add_argument(
        "--base",
        help="the original .gcs sheet (auto-detected from the export if omitted)",
    )
    inspect.set_defaults(func=cmd_inspect)
    return parser


def _use_utf8_output() -> None:
    """Make stdout/stderr UTF-8.

    Character names routinely contain non-ASCII (the sample is "Stürm"), and on
    Windows the console defaults to a legacy code page that would mangle them.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # pragma: no cover - exotic streams
                pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as err:
        print(f"json2gcs: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
