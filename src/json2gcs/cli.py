"""Command line entry point.

``inspect`` summarises an export and how it lines up with a base sheet.
``diff`` reconciles the two and reports what a session changed, writing nothing.
``convert`` does the same and writes the merged sheet.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
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


#: Where to look for the GCS application, in order. Override with --gcs or
#: the JSON2GCS_GCS environment variable; installs are often not on PATH.
_GCS_CANDIDATES = (
    r"C:\Program Files\GCS\gcs.exe",
    r"C:\Program Files (x86)\GCS\gcs.exe",
    "/Applications/GCS.app/Contents/MacOS/gcs",
    "/usr/local/bin/gcs",
    "/usr/bin/gcs",
)


def find_gcs(explicit: str | None = None) -> Path | None:
    """Locate the GCS application, or return None."""
    for candidate in (explicit, os.environ.get("JSON2GCS_GCS")):
        if candidate:
            path = Path(candidate)
            return path if path.is_file() else None
    found = shutil.which("gcs")
    if found:
        return Path(found)
    for candidate in _GCS_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def run_gcs_convert(binary: Path, target: Path) -> tuple[bool, str]:
    """Run ``gcs --convert`` on a file. **Rewrites it in place.**

    This is GCS loading the file for real and writing it back in the current
    data format, which makes the application itself a headless validator
    (docs/06-architecture.md 6.5).
    """
    try:
        done = subprocess.run(
            [str(binary), "--convert", str(target)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return False, f"could not run {binary}: {err}"
    if done.returncode != 0:
        return False, (done.stderr or done.stdout or "non-zero exit").strip()
    return True, (done.stdout or "").strip()


def _strip_calc(value):
    """Drop every ``calc`` block, which GCS recomputes on load."""
    if isinstance(value, dict):
        return {k: _strip_calc(v) for k, v in value.items() if k != "calc"}
    if isinstance(value, list):
        return [_strip_calc(v) for v in value]
    return value


def _verify_with_gcs(path: Path, binary: Path) -> tuple[bool, list[str]]:
    """Have GCS load our output, then check it did not have to change anything.

    GCS rewrites ``calc`` from the sheet's real values, so those blocks are
    expected to differ — that is the point of them being write-only.  Anything
    *else* that differs is a defect in our writer.
    """
    with tempfile.TemporaryDirectory() as workdir:
        copy = Path(workdir) / path.name
        shutil.copy(path, copy)
        ok, detail = run_gcs_convert(binary, copy)
        if not ok:
            return False, [f"GCS rejected the file: {detail}"]

        ours = jsonio.loads(jsonio.read_text(path))
        theirs = jsonio.loads(jsonio.read_text(copy))

    if jsonio.dumps(ours) == jsonio.dumps(theirs):
        return True, ["GCS loaded the file and rewrote it identically"]
    if jsonio.dumps(_strip_calc(ours)) == jsonio.dumps(_strip_calc(theirs)):
        return True, [
            "GCS loaded the file and changed nothing but the calc blocks,",
            "which it recomputes on open. Pass --refresh-calc to keep its values.",
        ]
    return False, [
        "GCS rewrote the file differently outside of calc — that is a writer bug.",
        f"Compare {path} against a 'gcs --convert' of a copy.",
    ]


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
        if args.refresh_calc and note.startswith("calc blocks"):
            continue  # about to be superseded by GCS's own values
        print(f"  · {note}")
    if outcome.skipped:
        print(f"  · {len(outcome.skipped)} change(s) left for review — see above")

    if args.verify or args.refresh_calc:
        binary = find_gcs(args.gcs)
        if binary is None:
            print(
                "  · GCS not found — pass --gcs PATH or set JSON2GCS_GCS to enable "
                "verification"
            )
            return 0
        if args.refresh_calc:
            ok, detail = run_gcs_convert(binary, out)
            if not ok:
                print(f"  · refresh-calc failed: {detail}")
                return 1
            print("  · calc refreshed by GCS itself; the file is now exactly what "
                  "GCS would save")
        if args.verify:
            ok, lines = _verify_with_gcs(out, binary)
            for i, line in enumerate(lines):
                print(f"  · verify: {line}" if i == 0 else f"             {line}")
            if not ok:
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
        help="have GCS itself load the result and confirm it rewrites it unchanged",
    )
    convert.add_argument(
        "--refresh-calc",
        action="store_true",
        help=(
            "run the output through GCS so its derived 'calc' values are correct. "
            "GCS ignores calc on load, but GGA needs it to re-import"
        ),
    )
    convert.add_argument(
        "--gcs",
        metavar="PATH",
        help="path to the GCS executable (or set JSON2GCS_GCS)",
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
