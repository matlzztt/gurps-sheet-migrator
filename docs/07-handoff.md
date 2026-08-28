# 7. Handoff — read this second

Read [`README.md`](../README.md) first for what the project is. This file is the
working state: what is done, what is next, and the things that cost time to
learn and would cost it again.

Last updated: 2026-08-28.

## Where the project stands

The merge round trip works and is verified by GCS itself. `json2gcs convert`
reads a Foundry export plus the original `.gcs` and writes a merged sheet that
GCS loads and rewrites unchanged.

```bash
JSON2GCS_GCS="C:/GOTProject/gcs/gcs.exe" python -m pytest
```

231 pass with GCS present; 221 pass and 10 skip without it. **A green run without
that env var is not a full run** — the oracle tests skip silently.

| Module | State |
|---|---|
| `jsonio.py` | done — byte-exact GCS reader/writer |
| `tid.py` | done |
| `foundry.py` | done — containers verified |
| `gcs.py` | done |
| `fields.py` | done for traits/skills/equipment/notes; spells entries are **unvalidated** |
| `reconcile.py` | done |
| `schema.py` | done — key order validated against 154 real rows |
| `apply.py` | done |
| `report.py` | done |
| `cli.py` | `inspect`, `diff`, `convert` |

## The agreed pipeline

Decided with the user on 2026-08-27, in this order:

1. ~~**Name fix.**~~ **Done 2026-08-28.** `reconcile()` takes `rename=False`;
   `_diff_profile` only proposes `profile.name` when it is set, and `diff` and
   `convert` both expose `--rename`. The control export now merges to *nothing
   at all* — `test_a_control_export_changes_nothing` asserts the output is
   byte-identical to the base sheet, which is a stronger invariant than the old
   "one line plus the timestamp".
2. ~~**Moved rows.**~~ **Done 2026-08-28.** `apply._move_row` detaches and
   re-attaches; `_move_blocked` refuses the four impossible destinations
   (missing container, a leaf, the row itself, its own descendant) and reports
   them as skipped rather than corrupting the tree. Ordering comes from
   `RowDelta.move_before` — the TIDs following the row at its destination in
   the export — and the row is inserted before the nearest of those the sheet
   actually has. Two things found on the way: a move *out* to the top level has
   `moved_to = None`, so `RowDelta.moved` is now a separate flag rather than a
   `moved_to` truth test; and carrying a container across to other equipment
   re-sections every row inside it, which `_collapse_move_cascades` attributes
   to the container instead of reporting eleven moves.

   No fixture has a real move, so `tests/test_moves.py` synthesizes them by
   relocating rows in the *control* export — which makes the move the only
   difference the reconciler can find. GCS itself accepts the result
   (`test_gcs_accepts_a_sheet_whose_rows_moved`) and leaves the row where we
   put it.
3. **Synthesize mode.** Mode B in [`01-problem.md`](01-problem.md): build a sheet
   from a Foundry export alone, with no base. `convert` currently requires a
   base. GCS ships defaults at `gcs/model/gurps/embedded_data/Standard.attr` and
   `Humanoid.body` to seed `settings`.
4. **Packaging.** `pyinstaller --onefile`, then a GUI. The stated end goal is
   "a few clicks".

**Spells were explicitly deprioritised.** The user has no casters, every fixture
has `spells: {}`, and the policy entries in `fields.py` have never executed.
Treat them as unwritten code, not as working code.

## What would actually de-risk this

The whole suite validates against **one fixture pair whose edits and assertions
were both chosen by the same author**. That is subtly circular. The single
highest-information action available is to use the tool on a real play session
and see what breaks. Untested in particular:

- an attribute **point** change (the `adj = points / cost_per_point` inversion
  has never fired on a real change — only on values that already matched)
- a skill raised with earned points
- rows added inside Foundry (the code exists; a real GGA-minted id has never
  been seen — `_getGGAId` in `actor-sheet.js`, and it is **not** a TID)

## Environment facts that are not discoverable

**GCS is installed at `C:\GOTProject\gcs\gcs.exe` and is not on `PATH`.** Pass
`--gcs PATH` or set `JSON2GCS_GCS`. `gcs --convert` runs headlessly, loads a
file, rewrites it in the current format and exits — that is the project's test
oracle and it works.

**The `gcs/` and `gurps/` clones are checked out with `core.autocrlf=true`, so
their working trees have CRLF.** Never copy a fixture out of them directly; it
will fail byte-exactness for reasons unrelated to the code. Take it from the git
blob instead:

```bash
git -C gcs show HEAD:model/gurps/testdata/issue767.gcs > samples/upstream/issue767.gcs
```

This repository pins `eol=lf` in `.gitattributes` for everything, which is load
bearing — `core.autocrlf` is `true` on this machine.

**The Bash tool mangles backslashes inside heredocs.** Writing a Python script
containing `\n`, `\s` or a regex through `python - <<'EOF'` corrupts it silently
or noisily. Use the Write tool for anything with escapes.

## Hard-won invariants — do not weaken these

**`test_control_export_yields_nothing_to_apply`** is the most valuable test in
the project. The control export was taken immediately after import with nothing
touched, so a correct reconciler must find *zero* applicable changes in it. It
started at 32 and every one was a real defect. If it starts failing, the fix is
almost certainly in the field policy, not in the test.

**`equipped` survives a move to other equipment.** Checked against the Go
source: nothing in GCS production code clears the flag when an item stops being
carried — `ReallyEquipped` is gated on which list the row is in, not on the
flag. So a section move must leave `equipped` exactly as it found it.

**Zero means delete.** Nearly every Go tag is `omitzero`, so GCS never writes
`equipped: false` — it omits the key. `equipment.quantity` is the one row field
without `omitzero`. See `schema.py`.

**`calc` is write-only.** GCS discards it on load and recomputes. Do not
reimplement GURPS arithmetic to populate it — `--refresh-calc` runs the output
back through GCS, which is both less code and more correct.

**Key order comes from the Go source, not from the fixtures.** An early attempt
to derive it by topological sort over observed rows produced confident, wrong
answers for keys that never co-occur. `schema.py` is transcribed from the struct
definitions; `tests/test_schema.py` validates the transcription.

**Notes must never be compared literally.** GGA re-indents them on every save
cycle (0 → 8 → 44 spaces observed) and appends every enabled modifier's *name*.
`fields.expected_notes()` reconstructs what GGA would have produced; comparison
is whitespace-insensitive.

## Working with the user

They can produce Foundry fixtures on request and did so promptly and correctly —
ask for a **control export** (taken immediately after import, nothing touched)
alongside any "played" one. The control half is what makes a fixture pair
valuable.

They could not add a row through the GGA sheet UI when asked; `addItemMenu` is a
right-click context menu on the section header, and it may not be exposed in all
views. Do not block on it.

They correct premises directly and expect the same in return — one earlier
belief about the sample having containers was wrong, and saying so plainly was
the right call.
