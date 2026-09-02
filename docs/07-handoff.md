# 7. Handoff — read this second

Read [`README.md`](../README.md) first for what the project is. This file is the
working state: what is done, what is next, and the things that cost time to
learn and would cost it again.

Last updated: 2026-09-02.

## Where the project stands

The merge round trip works and is verified by GCS itself. `json2gcs convert`
reads a Foundry export plus the original `.gcs` and writes a merged sheet that
GCS loads and rewrites unchanged.

```bash
JSON2GCS_GCS="C:/GOTProject/gcs/gcs.exe" python -m pytest
```

328 pass with GCS present; 306 pass and 22 skip without it. **A green run without
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
| `synthesize.py` | done — mode B, as merge against an empty sheet |
| `cli.py` | `inspect`, `diff`, `convert` (merge or `--synthesize`), `gui` |
| `gui.py` | done — tkinter; builds a CLI argv and runs it |

## The agreed pipeline

Decided with the user on 2026-08-27, in this order. **All four are done**; what
is left is in [`08-improvements.md`](08-improvements.md).

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
3. ~~**Synthesize mode.**~~ **Done 2026-08-28.** `convert --synthesize`. It is
   *merge against an empty sheet*, not a second implementation: the reconciler
   sees a base with no rows, every export row is therefore ADDED, and the
   existing writer does the work.

   The seed did not need transcribing from `Standard.attr` and `Humanoid.body`
   after all. **Hand GCS a file containing `{"version":5}` and it writes back
   the entire default sheet** — attributes, body plan, page settings, 13 KB of
   it. That output is `src/json2gcs/data/default.gcs`, and
   `test_the_template_is_what_gcs_itself_produces` re-derives it from GCS on
   every run, so it can never drift into a transcription.

   Comparing our first output against GCS's rewrite of it found four real
   defects, none of which merge mode could have exposed: traits were getting
   `points` (the field is `base_points`; GCS discards the wrong name silently,
   so the value was simply lost), `profile` keys were written in policy order
   rather than GCS's (`handedness` precedes `gender`), zero `base_points` was
   written where `omitzero` means omit, and techniques lost their `q` TIDs.

   Prerequisites fixed along the way, both of which also affect merge mode:
   `_add_row` never registered what it created in `sheet.by_tid`, so a row
   nested inside a *newly created* container silently landed at the top level;
   and added rows were written in the report's alphabetical order rather than
   the export's. `RowDelta.order` records the depth-first position, which fixes
   the ordering and guarantees a container exists before its contents.
4. ~~**Packaging.**~~ **Done 2026-08-28.** `json2gcs.spec` builds one
   self-contained 13 MB `json2gcs.exe`:

   ```bash
   python -m pip install -e ".[build]"
   python -m PyInstaller --distpath build/dist json2gcs.spec
   ```

   Run it with no arguments and the window opens; run it with arguments and it
   is the CLI, byte-for-byte the same output (checked against the library's).

   `gui.py` reimplements nothing — it assembles the argv the command line
   takes and calls `cli.main`, capturing stdout. So every rule about what may
   be written stays in one place, and the window shows the same report the
   terminal does. The parts that decide anything (`build_argv`, `suggest`) are
   pure functions, tested without ever constructing a `tk.Tk`.

   Three packaging traps, all of which bit:

   * **`data/default.gcs` must be bundled explicitly.** A plain `--onefile`
     leaves it out and `--synthesize` then fails at run time, not build time.
   * **`__main__.py` cannot use a relative import.** PyInstaller runs it as a
     top-level script with no package context, so `from .cli import main`
     builds cleanly and dies on launch with "attempted relative import with no
     known parent package". Use `from json2gcs.cli import main`.
   * **`gui` needs a `hiddenimports` entry**, because `cmd_gui` imports it
     late (deliberately — the CLI should never need tk).

   `tests/test_gui.py` pins all three, so they cannot regress silently between
   builds. Note `.gitignore` has `*.spec`; `!/json2gcs.spec` exempts ours.

**Spells were explicitly deprioritised.** The user has no casters, every fixture
has `spells: {}`, and the policy entries in `fields.py` have never executed.
Treat them as unwritten code, not as working code.

## What to improve next

[`08-improvements.md`](08-improvements.md) is the backlog: every known gap, how
it was found, and what fixing it takes. §8.1 (driving `apply._add_row` from
`fields.RULES`) and §8.2 (decomposing the names GGA composes, for traits and
skills) are done as of 2026-09-02. §8.4 turned up two real, unrelated findings
along the way — `_strip_calc` was missing `defaulted_from`, and three of the
user's own characters carry an invalid entity id that GCS silently remints —
both are written up there. Techniques (§8.3) are still the next composed-name
gap, and spells remain untouched (§8.7).

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

**A row nested inside a Foundry-created *container* loses the link.**
`foundry._build_rows` passes the parent's `uuid` down as `parent_tid`, and sets
that uuid to `None` when it is not a valid TID — so a child of a GGA-minted
container arrives with no parent and lands at the top level of its section.
Everything else about added rows now works, including nesting inside a
container that carries a real TID
(`test_a_row_added_inside_a_newly_added_container_nests_correctly`). Fixing
this properly needs the raw GGA id threaded through as a second linkage key,
and a real fixture to check it against — which is the same fixture the item
above is waiting for.

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

**A synthesized sheet is not a GCS fixed point until GCS has seen it once.**
GCS appends a `points_record` entry with reason "Reconciliation" the first time
it opens one, because the total the export reported is not the total GCS
computes from the rows. That is GCS doing its job on a character it has never
met, not a defect — `--refresh-calc` settles it, and after that the file
rewrites to itself byte for byte.

**A skill's difficulty letter is not recoverable.** Foundry keeps
`relativelevel` (`"IQ+1"`), which names the controlling attribute but not
Easy/Average/Hard/Very Hard. Synthesize writes `iq/e` — the real attribute
with GCS's own default letter — because `difficulty` is not omitzero, so the
choice is between GCS's default *with* the attribute and GCS's default
*without* it. A technique gets `a`, which is what GCS's own `NewTechnique`
sets.

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
