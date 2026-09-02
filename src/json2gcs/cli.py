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
from dataclasses import dataclass
from pathlib import Path

from . import apply as applymod
from . import foundry, gcs, jsonio, reconcile, report, store, synthesize, tid
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


@dataclass(frozen=True)
class Base:
    """The sheet to merge into, and how we came to choose it."""

    path: Path
    how: str
    snapshot: "store.Snapshot | None" = None

    @property
    def is_remembered_copy(self) -> bool:
        """True when ``path`` is the store's own copy of a sheet that has since
        moved or been deleted — so nothing may be written anywhere near it."""
        return self.snapshot is not None and Path(self.snapshot.source) != self.path


def _holds_the_same_character(path: Path, actor: foundry.Actor) -> bool:
    """True if the sheet at ``path`` shares any row TID with this export.

    A snapshot records where its sheet was read from, but a path is not a
    promise: the file there may since have been replaced by a different
    character.  One shared TID settles it, since a TID is an identity.
    """
    try:
        sheet = gcs.load(path)
    except (OSError, ValueError):
        return False
    return any(row.tid in sheet.by_tid for row in actor.rows() if row.tid)


def _base_from_store(actor: foundry.Actor, where: str | None) -> Base | None:
    """Find the base through the snapshot store (docs/06-architecture.md 6.9).

    The snapshot is the *ancestor*, not the target: merging into it would
    produce a sheet missing everything done in GCS since it was taken.  So the
    live file it was read from is what we want, and the snapshot's job here is
    to say where that is.  Only when the original is gone do we fall back to
    the remembered copy, which is still better than refusing outright.
    """
    try:
        shelf = store.Store(store.find_root(where))
        snapshot = shelf.ancestor_for(actor)
    except OSError:
        return None
    if snapshot is None:
        return None

    live = Path(snapshot.source)
    if live.is_file() and _holds_the_same_character(live, actor):
        return Base(live, f"found in the snapshot store, remembered from {live}", snapshot)
    return Base(
        shelf.blob_path(snapshot.digest),
        f"the remembered copy of {snapshot.name!r} [{snapshot.digest}] — the "
        f"original is no longer at {snapshot.source}, so anything done to it in "
        "GCS since is not here",
        snapshot,
    )


def _resolve_base(args: argparse.Namespace, actor: foundry.Actor, export: Path) -> Base:
    """Find the base sheet, or explain why we cannot."""
    if args.base:
        return Base(Path(args.base), "given with --base")
    found = _find_base(actor, export)
    if found:
        return Base(found, f"found beside the export as {found.name}")
    remembered = _base_from_store(actor, getattr(args, "store", None))
    if remembered is not None:
        return remembered
    hint = (
        f" — looked for {actor.import_name!r} beside the export"
        if actor.import_name
        else ""
    )
    raise ValueError(
        f"no base .gcs sheet given and none found{hint}, and no snapshot of it "
        "is stored; pass --base, or run 'json2gcs remember <sheet.gcs>' so it "
        "can be found on its own next time"
    )


def _ancestor_sheet(
    args: argparse.Namespace, actor: foundry.Actor, base: Base
) -> tuple[gcs.Sheet, store.Snapshot] | None:
    """The sheet as Foundry imported it, if we remembered it.

    This is what makes the merge three-way (docs/06-architecture.md 6.9).
    When the base *is* the remembered copy there is nothing to compare against
    — ancestor and target would be the same file — so it is skipped.
    """
    if getattr(args, "no_ancestor", False) or base.is_remembered_copy:
        return None
    try:
        shelf = store.Store(store.find_root(getattr(args, "store", None)))
        snapshot = shelf.ancestor_for(actor)
        if snapshot is None:
            return None
        raw = shelf.bytes_of(snapshot.digest)
        if raw == base.path.read_bytes():
            # The snapshot *is* the sheet we are merging into — often because
            # this run just took it. Every field would classify as "only the
            # export moved", which is two-way by another name, so claiming a
            # three-way merge here would be a lie about how much we know.
            return None
        return gcs.loads(raw.decode("utf-8")), snapshot
    except (OSError, ValueError):
        return None


def _remember(path: Path, where: str | None) -> None:
    """Snapshot a base sheet, reporting what happened but never failing on it.

    A store that cannot be written is a lost future convenience; the merge the
    user actually asked for still has to happen (docs/06-architecture.md 6.9).
    """
    try:
        snapshot, is_new = store.Store(store.find_root(where)).remember(path)
    except (OSError, ValueError) as err:
        print(f"  · not remembered: {err}")
        return
    if is_new:
        print(f"  · remembered this sheet as {snapshot.digest} for future merges")


def cmd_remember(args: argparse.Namespace) -> int:
    shelf = store.Store(store.find_root(args.store))

    if args.list:
        found = shelf.snapshots()
        print(f"Store: {shelf.root}")
        if not found:
            print("  (empty — run 'json2gcs remember <sheet.gcs>')")
            return 0
        for snapshot in found:
            print(f"  {snapshot.describe()}")
            print(f"      from {snapshot.source}")
        return 0

    if not args.sheets:
        raise ValueError("give at least one .gcs sheet to remember, or pass --list")

    for name in args.sheets:
        path = Path(name)
        snapshot, is_new = shelf.remember(path)
        verb = "remembered" if is_new else "already stored"
        print(f"{verb}: {snapshot.describe()}")
    print(f"\nStore: {shelf.root}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    export = Path(args.export)
    actor = foundry.load(export)
    base = _resolve_base(args, actor, export)
    sheet = gcs.load(base.path)

    print(f"Foundry export : {export}   ({actor.name}, GGA {actor.system_version})")
    print(f"Base GCS sheet : {base.path}")
    if base.snapshot is not None:
        print(f"                 {base.how}")
    found = _ancestor_sheet(args, actor, base)
    if found is not None:
        print(f"Compared against the sheet as Foundry imported it [{found[1].digest}]")
    print()
    result = reconcile.reconcile(
        actor, sheet, rename=args.rename, ancestor=found[0] if found else None
    )
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


#: Keys GCS recomputes on load and writes back, the same way it does ``calc``.
#: ``defaulted_from`` is a skill's cached best default, written to disk from a
#: script evaluation (gcs/model/gurps/skill.go, entity.go 206) rather than
#: read back as input — comparing it would flag a real fixture's own file as
#: a writer bug for something json2gcs never touches (docs/04-mapping.md 4.5
#: lists it ❌, unrecoverable).
_DERIVED_KEYS = frozenset({"calc", "defaulted_from"})


def _strip_calc(value):
    """Drop every ``calc`` block and other GCS-recomputed-on-load keys."""
    if isinstance(value, dict):
        return {k: _strip_calc(v) for k, v in value.items() if k not in _DERIVED_KEYS}
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

    if args.synthesize:
        if args.base:
            raise ValueError(
                "--synthesize builds a sheet from the export alone; drop --base "
                "(or drop --synthesize to merge into it)"
            )
        return _convert_synthesized(args, actor, export)

    base = _resolve_base(args, actor, export)
    sheet = gcs.load(base.path)
    if base.snapshot is not None:
        print(f"Base sheet: {base.path}")
        print(f"  · {base.how}")
        print()
    if not args.no_remember and not base.is_remembered_copy:
        _remember(base.path, args.store)

    if args.output:
        out = Path(args.output)
    elif base.is_remembered_copy:
        # The base lives inside the store, and the store is ours, not the
        # user's workspace. Land beside the export under the sheet's own name.
        out = export.parent / f"{Path(base.snapshot.source).stem}.merged.gcs"
    else:
        out = base.path.with_suffix(".merged.gcs")
    if out.resolve() == base.path.resolve() and not args.force:
        raise ValueError(
            f"refusing to overwrite the base sheet {base.path}; "
            "choose a different -o, or pass --force"
        )

    found = _ancestor_sheet(args, actor, base)
    if found is not None:
        print(
            f"Three-way merge: comparing against the sheet as Foundry imported "
            f"it [{found[1].digest}]"
        )
        print()
    result = reconcile.reconcile(
        actor, sheet, rename=args.rename, ancestor=found[0] if found else None
    )
    print(report.render(result))
    print()

    if args.dry_run:
        outcome = applymod.plan(
            result, deletions=args.deletions, include_lossy=args.include_lossy
        )
        print(
            f"Dry run: would write {len(outcome.applied)} field change(s), "
            f"{len(outcome.added)} new row(s), move {len(outcome.moved)}, "
            f"drop {len(outcome.dropped)}, keep {len(outcome.kept)}."
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
        f"{len(outcome.moved)} moved, {len(outcome.dropped)} dropped, "
        f"{len(outcome.kept)} kept)"
    )
    for note in outcome.notes:
        if args.refresh_calc and note.startswith("calc blocks"):
            continue  # about to be superseded by GCS's own values
        print(f"  · {note}")
    if outcome.skipped:
        print(f"  · {len(outcome.skipped)} change(s) left for review — see above")

    return _hand_to_gcs(args, out)


def _hand_to_gcs(args: argparse.Namespace, out: Path) -> int:
    """Run the written file back through GCS, if asked and if GCS is there."""
    if not (args.verify or args.refresh_calc):
        return 0
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


def _convert_synthesized(
    args: argparse.Namespace, actor: foundry.Actor, export: Path
) -> int:
    """``convert --synthesize``: a sheet from the export alone, no base."""
    out = Path(args.output) if args.output else export.with_suffix(".gcs")
    if out.exists() and not args.force:
        raise ValueError(f"refusing to overwrite {out}; choose another -o, or --force")

    sheet, result, outcome = synthesize.synthesize(
        actor, include_lossy=args.include_lossy
    )
    for warning in result.warnings:
        print(f"  ! {warning}")

    if args.dry_run:
        print(
            f"Dry run: would write a new sheet with {len(outcome.added)} row(s) "
            f"and {len(outcome.sheet_fields)} sheet field(s)."
        )
        print(f"Nothing written. Output would be {out}")
        return 0

    jsonio.dump(out, sheet.data)
    print(
        f"Wrote {out}  ({len(outcome.added)} row(s), "
        f"{len(outcome.sheet_fields)} sheet field(s))"
    )
    print(
        "  · synthesized from the export alone: modifiers, features, prereqs, "
        "difficulty and library links are not in it to recover"
    )
    if outcome.skipped:
        print(f"  · {len(outcome.skipped)} change(s) left for review")
    if not args.refresh_calc:
        print(
            "  · GCS will reconcile the point total the first time it opens this; "
            "--refresh-calc does it now"
        )
    return _hand_to_gcs(args, out)


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
    diff.add_argument(
        "--rename",
        action="store_true",
        help=(
            "also carry the Foundry actor's name back to the sheet. Off by "
            "default: the actor is often named for its token or folder"
        ),
    )
    diff.add_argument(
        "--store", help="where snapshots are kept (or set JSON2GCS_STORE)"
    )
    diff.add_argument(
        "--no-ancestor",
        action="store_true",
        help="compare two-way, ignoring any remembered copy of the sheet",
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
        "--synthesize",
        action="store_true",
        help=(
            "build a new sheet from the export alone, with GCS's own defaults, "
            "instead of merging into a base. Lower fidelity: everything Foundry "
            "never knew about is not in the export to recover"
        ),
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
    convert.add_argument(
        "--store", help="where to keep snapshots of the base sheet (or set JSON2GCS_STORE)"
    )
    convert.add_argument(
        "--no-remember",
        action="store_true",
        help="do not snapshot the base sheet before merging into it",
    )
    convert.add_argument(
        "--no-ancestor",
        action="store_true",
        help="merge two-way: ignore any remembered copy of the sheet as Foundry "
        "imported it, and resolve every disagreement in the export's favour",
    )
    convert.add_argument(
        "--rename",
        action="store_true",
        help=(
            "also carry the Foundry actor's name back to the sheet. Off by "
            "default: the actor is often named for its token or folder"
        ),
    )
    convert.set_defaults(func=cmd_convert)

    remember = sub.add_parser(
        "remember",
        help="keep a copy of a .gcs sheet, so a later export can be merged "
        "against the sheet as it was when Foundry imported it",
    )
    remember.add_argument("sheets", nargs="*", help="the .gcs sheet(s) to store")
    remember.add_argument(
        "--list", action="store_true", help="show what is already stored"
    )
    remember.add_argument(
        "--store", help="where to keep snapshots (or set JSON2GCS_STORE)"
    )
    remember.set_defaults(func=cmd_remember)

    window = sub.add_parser("gui", help="open the window (the default when run with no arguments)")
    window.set_defaults(func=cmd_gui)
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


def cmd_gui(args: argparse.Namespace) -> int:
    """Open the tkinter front end. Imported here so the CLI never needs tk."""
    from . import gui

    return gui.main()


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    if argv is None and len(sys.argv) == 1:
        # Double-clicked, rather than run from a shell: show the window.
        argv = ["gui"]
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as err:
        print(f"json2gcs: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
