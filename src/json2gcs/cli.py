"""Command line entry point.

``inspect`` summarises an export and how it lines up with a base sheet.
``diff`` reconciles the two and reports what a session changed, writing nothing.
``convert`` does the same and writes the merged sheet.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from . import apply as applymod
from . import foundry, gcs, jsonio, reconcile, report, tid
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


def _resolve_base(args: argparse.Namespace, actor: foundry.Actor, export: Path) -> Path:
    """Find the base sheet, or explain why we cannot."""
    if args.base:
        return Path(args.base)
    found = _find_base(actor, export)
    if found:
        return found
    hint = (
        f" — looked for {actor.import_name!r} beside the export"
        if actor.import_name
        else ""
    )
    raise ValueError(f"no base .gcs sheet given and none found{hint}; pass --base")


def cmd_diff(args: argparse.Namespace) -> int:
    export = Path(args.export)
    actor = foundry.load(export)
    base = _resolve_base(args, actor, export)
    sheet = gcs.load(base)

    print(f"Foundry export : {export}   ({actor.name}, GGA {actor.system_version})")
    print(f"Base GCS sheet : {base}")
    print()
    result = reconcile.reconcile(actor, sheet)
    print(report.render(result, verbose=args.verbose))
    return 1 if result.warnings else 0


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


def _verify_with_gcs(path: Path) -> tuple[bool, str]:
    """Load the output with GCS itself, if it is installed.

    ``gcs --convert`` reads a file, rewrites it in the current data format and
    exits, which makes the real application a headless validator
    (docs/06-architecture.md 6.5).
    """
    binary = shutil.which("gcs")
    if not binary:
        return False, "gcs not on PATH; skipped"
    try:
        done = subprocess.run(
            [binary, "--convert", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return False, f"could not run gcs: {err}"
    if done.returncode != 0:
        return False, (done.stderr or done.stdout or "non-zero exit").strip()
    return True, "GCS loaded and rewrote the file without complaint"


def cmd_convert(args: argparse.Namespace) -> int:
    export = Path(args.export)
    actor = foundry.load(export)
    base = _resolve_base(args, actor, export)
    sheet = gcs.load(base)

    if args.output:
        out = Path(args.output)
    else:
        out = base.with_suffix(".merged.gcs")
    if out.resolve() == base.resolve() and not args.force:
        raise ValueError(
            f"refusing to overwrite the base sheet {base}; "
            "choose a different -o, or pass --force"
        )

    result = reconcile.reconcile(actor, sheet)
    print(report.render(result))
    print()

    if args.dry_run:
        outcome = applymod.plan(
            result, deletions=args.deletions, include_lossy=args.include_lossy
        )
        print(
            f"Dry run: would write {len(outcome.applied)} field change(s), "
            f"{len(outcome.added)} new row(s), drop {len(outcome.dropped)}, "
            f"keep {len(outcome.kept)}."
        )
        print(f"Nothing written. Output would be {out}")
        return 0

    outcome = applymod.apply(
        result, sheet, deletions=args.deletions, include_lossy=args.include_lossy
    )
    if not outcome.total:
        print("Nothing to carry back; the sheet already matches the export.")
        return 0

    jsonio.dump(out, sheet.data)
    print(
        f"Wrote {out}  "
        f"({len(outcome.applied)} field change(s), {len(outcome.added)} new row(s), "
        f"{len(outcome.dropped)} dropped, {len(outcome.kept)} kept)"
    )
    for note in outcome.notes:
        print(f"  · {note}")
    if outcome.skipped:
        print(f"  · {len(outcome.skipped)} change(s) left for review — see above")

    if args.verify:
        ok, detail = _verify_with_gcs(out)
        print(f"  · verify: {detail}")
        if not ok and shutil.which("gcs"):
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

    diff = sub.add_parser(
        "diff",
        help="reconcile an export against a base sheet and report what changed",
    )
    diff.add_argument("export", help="the Foundry actor export (.json)")
    diff.add_argument(
        "--base",
        help="the original .gcs sheet (auto-detected from the export if omitted)",
    )
    diff.add_argument(
        "-v", "--verbose", action="store_true", help="also list unchanged rows"
    )
    diff.set_defaults(func=cmd_diff)

    convert = sub.add_parser(
        "convert",
        help="merge an export into a base sheet and write the result",
    )
    convert.add_argument("export", help="the Foundry actor export (.json)")
    convert.add_argument(
        "--base",
        help="the original .gcs sheet (auto-detected from the export if omitted)",
    )
    convert.add_argument(
        "-o",
        "--output",
        help="where to write (default: alongside the base, as <name>.merged.gcs)",
    )
    convert.add_argument(
        "--deletions",
        choices=applymod.DeletionPolicy.ALL,
        default=applymod.DeletionPolicy.KEEP,
        help=(
            "rows in the sheet but not the export: 'keep' (default) leaves them, "
            "'drop' removes them. They are ambiguous — either deleted in Foundry "
            "or added to the sheet after the export"
        ),
    )
    convert.add_argument(
        "--include-lossy",
        action="store_true",
        help="also write changes flagged lossy (notes, values GCS derives)",
    )
    convert.add_argument(
        "--dry-run", action="store_true", help="report only; write nothing"
    )
    convert.add_argument(
        "--force", action="store_true", help="allow overwriting the base sheet"
    )
    convert.add_argument(
        "--verify",
        action="store_true",
        help="after writing, load the result with 'gcs --convert' if available",
    )
    convert.set_defaults(func=cmd_convert)
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
