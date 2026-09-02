# GURPS Sheet Migrator

Converts a GURPS character exported from **Foundry VTT** (via the
[GURPS Game Aid](https://github.com/crnormand/gurps) system) back into a **GCS**
(`.gcs`) character sheet — closing a loop that currently only runs one way.
GGA can import a `.gcs` file into Foundry; nothing goes the other way. This
tool reads a session's Foundry export and carries what changed back into the
original sheet, so a GM running a campaign in Foundry doesn't have to
hand-edit GCS to keep it in sync.

**Status: both modes work, GCS itself verifies them, and merges stay honest
across edits made on either side.** A snapshot store turns a plain two-way
merge into a three-way one, so a change made in GCS after the export is never
silently overwritten.

## Contents

- [How it works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
- [Architecture](#architecture)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Key findings so far](#key-findings-so-far)
- [License](#license)

## How it works

GGA copies GCS's object IDs verbatim into each Foundry row's `uuid`, and
Foundry's export writes them back out — so every Foundry row can be matched to
the exact GCS row it came from. That matters because the forward conversion
(GCS → Foundry) is *heavily* lossy: modifiers, features, prereqs, tags,
library links, and the point-buy inputs are all either flattened into display
strings or dropped. Reconstructing them from the Foundry JSON alone is
impossible.

So the tool has two modes:

- **Merge** (primary) — Foundry export **+** the original `.gcs`. Match rows by
  ID and write back only the fields Foundry is authoritative for. High
  fidelity, because everything the export can't tell you comes from the base
  sheet instead.
- **Synthesize** (fallback) — Foundry export alone, for a character that never
  had a `.gcs` (a GCA import, a hand-built NPC, a sheet that's been lost).
  Structurally valid, GCS's own defaults, honestly lower fidelity.

## Installation

Requires **Python 3.12+**.

```bash
python -m pip install -e ".[dev]"
```

This installs the `json2gcs` command and the `pytest` suite. To also build the
standalone Windows executable:

```bash
python -m pip install -e ".[build]"
python -m PyInstaller --distpath build/dist json2gcs.spec
```

That produces one self-contained `build/dist/json2gcs.exe` (~13 MB) needing no
Python install to run. Launched with no arguments it opens the GUI; launched
with arguments it's the CLI below, byte-for-byte the same output as running
the library directly.

## Usage

### GUI

Run the executable (or `python -m json2gcs`) with no arguments and a window
opens: choose the Foundry export, and it finds the sheet, picks the mode, and
fills in the output path. **Preview** shows the report without writing;
**Convert** writes the file.

### Merge into an existing sheet

```bash
json2gcs convert actor.json --base character.gcs
```

Writes `character.merged.gcs` beside the base — never over it. Add `--dry-run`
to see the report without writing, or use `diff` for the report alone:

```bash
json2gcs diff samples/container/container-played.foundry.json --base samples/container/container.gcs
```

```
Changes to carry back
  equipment (carried)
      Arrow
        quantity     10 → 4
      Backpack
        equipped     yes → no
    └ Metabackpack
        equipped     yes → no   (follows its container)
    └ The Book of Lines
        name         "The Book of Lines" → "The Book of Metabackpacking"
        equipped     yes → no   (follows its container)
  character
        HP damage    0 → 4
        FP damage    0 → 8

In the sheet but not the export (1) — ambiguous
    skills: Poisons
    Either deleted in Foundry or added to the sheet after the export.
    Nothing in either file tells them apart, so these are kept.

Needs review (4) — found, but not safe to apply
    Cloth, Padded
        weight       "1" → "8"
            row has modifiers, so Foundry's value is post-modifier
```

`inspect` gives a plainer summary of one export and how it lines up with a base
sheet. `inspect` and `diff` never write; `convert` writes only to its output
file.

Changes flagged **lossy** are reported but not written unless you pass
`--include-lossy`; changes flagged **blocked** are never written, because the
Foundry value is known to be contaminated (a post-modifier weight, say). Rows
missing from the export are kept by default — they are ambiguous — and
`--deletions drop` removes them instead.

A row put into a different container, or moved between carried and other
equipment, is re-attached where the export has it — in the export's own order,
and carrying its children with it. A move with nowhere valid to land (into a
container the sheet does not have, or into a row that is not a container) is
reported and skipped rather than forced.

The sheet's own character name is left alone. A Foundry actor is often named
for its token or its folder rather than for the character, so carrying that
name back would silently retitle the sheet; pass `--rename` if you do want it.

### Verifying against GCS

If GCS is installed, `--verify` has GCS itself load the result and confirm it
rewrites it unchanged, and `--refresh-calc` runs the output back through GCS so
its derived values are authoritative:

```bash
json2gcs convert actor.json --base character.gcs --refresh-calc --verify
```

```
  · calc refreshed by GCS itself; the file is now exactly what GCS would save
  · verify: GCS loaded the file and rewrote it identically
```

Point at the binary with `--gcs PATH` or `JSON2GCS_GCS` if it is not on
`PATH`.

### Synthesize mode

For an actor that was never in GCS, build a sheet from the export alone:

```bash
json2gcs convert actor.json --synthesize --refresh-calc
```

The defaults are GCS's own: the template it starts from is what GCS writes
when handed a stub file, and a test re-derives it from the application on
every run. Rows keep the TIDs they already had, so the result is a usable base
to merge into later. Honestly lower fidelity, though — modifiers, features,
tags, library links and a skill's difficulty letter are not in a Foundry
export to recover, and [`docs/07-handoff.md`](docs/07-handoff.md) says so
field by field.

### Keeping merges honest across edits

`--base` alone is a **two-way** comparison, and it quietly assumes the sheet is
still exactly what Foundry exported from. When a GM has since changed
something in GCS — raised a skill, adjusted an item's quantity — that
assumption fails silently: an untouched field reads as "unchanged" against a
now-stale export, and the GM's edit gets reverted by a value nobody typed.

```bash
json2gcs remember character.gcs
```

keeps a byte-exact copy of the sheet as it stood at that moment. `convert`
takes one automatically of whatever it merges into (unless `--no-remember`),
so in practice you rarely need to run `remember` yourself — the first
`convert` against a sheet is enough to cover every export from it after that.
`convert` and `diff` then look the copy up on their own: by the export's row
TIDs, not by filename, so the right snapshot is found even if the sheet has
moved. `--store PATH` or `JSON2GCS_STORE` says where they are kept (a
per-platform default otherwise); `remember --list` shows what is held.

With that ancestor in hand, the same disagreement resolves three ways instead
of one:

| ancestor vs. sheet vs. export | verdict |
|---|---|
| only Foundry's copy moved | carry it back |
| only the sheet moved, in GCS | **superseded** — leave it alone |
| both moved, to different values | **conflict** — reported, never applied |

Measured on the fixture, with the GM having raised Stealth 8 → 12 and set
Arrow's quantity to 7 in GCS after exporting:

```
two-way    Stealth  points  12 → 8      ← the lost update, applied silently
           Arrow    quantity 7 → 4      ← the GM's number overwritten

three-way  Already newer in the sheet (1) — left alone
               Stealth  points  keeping 12; the export still has 8
           Needs review
               Arrow    quantity 7 → 4
                   changed on both sides since the import: the sheet now has
                   "7" and the export "4", both from "10"
```

`--no-ancestor` turns this off and merges two-way, the way `--base` alone
always has. See [`docs/06-architecture.md`](docs/06-architecture.md) §6.9 for
the full design.

## Architecture

The pipeline is: read the Foundry export (`foundry.py`) and the base GCS sheet
(`gcs.py`), both indexed by row TID; reconcile them under a declarative field
policy (`fields.py`, `reconcile.py`) into a set of proposed changes; apply that
plan to the base sheet's own structure in place (`apply.py`), so anything not
touched — modifiers, prereqs, library links, the points log — survives by
construction rather than by being copied correctly. `jsonio.py` and
`schema.py` make the writer byte-exact against GCS's own serializer, which is
what makes `gcs --convert` usable as a test oracle (see
[Testing](#testing)).

Two things make this safer than a naive two-way diff:

- **The snapshot store** (`store.py`) keeps a copy of the sheet as it was when
  Foundry imported from it, turning the merge into a three-way one — see
  [Keeping merges honest across edits](#keeping-merges-honest-across-edits).
- **`calc` is delegated, not reimplemented.** GCS discards `calc` on load and
  recomputes it from the rows, so this project never had to reimplement GURPS's
  point math or damage resolution. `--refresh-calc` runs the output back
  through GCS instead.

`gui.py` reimplements nothing: it assembles the same argument list the command
line takes and calls `cli.main`, capturing what it prints. Every rule about
what may be written lives in one place, and the window shows the same report
the terminal does.

The full design record — including the two upstream formats this bridges,
every rejected alternative, and why merge mode was chosen over synthesize as
the primary path — is in [`docs/`](#documentation); start with
[`docs/01-problem.md`](docs/01-problem.md).

## Testing

```bash
python -m pytest
```

306 tests pass and 22 skip without GCS installed. To run the full 328,
including the tests that use GCS itself as an oracle, point at the binary:

```bash
JSON2GCS_GCS="C:/path/to/gcs.exe" python -m pytest
```

**A green run without that env var is not a full run** — the oracle tests skip
silently rather than failing.

The suite includes:

- Unit tests per module, plus `tests/test_schema.py` validating the GCS key
  order against 154 real rows.
- `test_oracle.py`, which runs `gcs --convert` on this project's own output and
  on a set of real characters, and asserts GCS rewrites them identically.
- `tests/test_gui.py`, which pins the three PyInstaller packaging traps (see
  [`docs/07-handoff.md`](docs/07-handoff.md)) so they can't regress silently
  between builds.
- `test_control_export_yields_nothing_to_apply` — the project's single most
  valuable test. A control export, taken immediately after import with
  nothing touched, must reconcile to *zero* applicable changes; it started at
  32 and every one was a real defect in the field policy.

## Project layout

```
src/json2gcs/
  jsonio.py              byte-exact GCS JSON reader/writer
  tid.py                 GCS TID validation, kind prefixes, minting
  foundry.py             Foundry actor export reader, flat-indexed by TID
  gcs.py                 TID-indexed view of a GCS sheet
  fields.py              the field policy, as data (docs/04-mapping.md)
  reconcile.py           matches by TID and diffs under that policy
  schema.py              canonical GCS key order and omitzero rules
  apply.py               writes a reconciliation into the base sheet
  synthesize.py          mode B: a new sheet from the export alone
  store.py               the snapshot store — remembered sheets, keyed by TID
  report.py              renders a reconciliation as readable text
  cli.py                 command line entry point
  gui.py                 tkinter window; builds a command line and runs it
  __main__.py            entry point for `python -m` and the packaged .exe
  data/default.gcs       GCS's own empty sheet, the synthesize template
json2gcs.spec            PyInstaller build definition
tests/                   pytest suite (328 tests)
docs/                    the design record — see Documentation below
samples/sturm/           the regression fixture — one character, both formats
samples/container/       container + known-changelog fixture set
samples/upstream/        GCS-authored fixtures from the gcs repo
gcs/                     upstream clone, git-ignored (richardwilkes/gcs)
gurps/                   upstream clone, git-ignored (crnormand/gurps)
```

The two upstream clones (`gcs/`, `gurps/`) are reference material, not
dependencies. They are git-ignored and pinned by commit in
[`docs/00-provenance.md`](docs/00-provenance.md); re-clone them with the
commands there if you need them.

## Documentation

Read in order:

| | |
|---|---|
| [`docs/00-provenance.md`](docs/00-provenance.md) | Pinned upstream revisions and format versions everything was verified against |
| [`docs/01-problem.md`](docs/01-problem.md) | What we are building and why merge mode is the design |
| [`docs/02-gcs-format.md`](docs/02-gcs-format.md) | The `.gcs` v5 format: serialization contract, TIDs, row structs, why `calc` is write-only |
| [`docs/03-foundry-format.md`](docs/03-foundry-format.md) | The Foundry actor export: the `00000` key convention, `contains`/`parentuuid`, section-by-section |
| [`docs/04-mapping.md`](docs/04-mapping.md) | **The field mapping.** Every GCS field, its Foundry source, and how faithful it is |
| [`docs/05-fidelity.md`](docs/05-fidelity.md) | Measured results on the sample pair, the full loss inventory, and the traps |
| [`docs/06-architecture.md`](docs/06-architecture.md) | Pipeline, verification strategy, language choice, build order, and the snapshot store (§6.9) |
| [`docs/07-handoff.md`](docs/07-handoff.md) | **Current state, next steps, and the traps that cost time — read this if you're picking up the project** |
| [`docs/08-improvements.md`](docs/08-improvements.md) | The backlog: known gaps, how each was found, and what closing it takes |

Environment facts worth knowing up front: **GCS is not assumed to be on
`PATH`** (pass `--gcs` or set `JSON2GCS_GCS`), and if you're working with the
upstream clones under `core.autocrlf=true`, take fixtures from the git blob
rather than the working tree — see `docs/07-handoff.md` for why.

## Key findings so far

- **IDs survive the round trip.** 22/22 traits, 21/21 skills, 26/26 equipment
  matched by ID across the sample pair.
- **`calc` is write-only in GCS.** Every unmarshaller discards it, so we never
  have to reimplement GCS's point maths or damage resolution — GCS recomputes
  on open. (We emit it anyway, so GGA can re-import our output.)
- **`gcs --convert` is a free validator.** The GCS binary will load, rewrite,
  and exit headlessly. Diffing our output against its rewrite makes GCS's own
  serializer the test oracle.
- **The lossy fields are enumerable**, not a vague fog — see
  `docs/05-fidelity.md`.
- **A control export is a sharp acceptance test.** Exported with nothing
  touched, it must reconcile to *zero* applicable changes. The first run
  reported 32 — every one a real defect in the field policy. See
  `docs/05-fidelity.md` §5.8.
- **Zero means delete.** GCS's `omitzero` tags mean it never writes
  `equipped: false`; it omits the key. Un-equipping is a deletion, and the
  writer has to know that for every field.
- **Merge preserves by construction.** The writer edits the base structure in
  place, so modifiers, features, prereqs, library `source` links, settings and
  the points log survive because nothing touches them — not because they were
  copied correctly.
- **Containers round-trip cleanly.** `samples/container/` covers nesting two
  levels deep across traits, skills and both equipment lists; uppercase TIDs,
  `contains` and `parentuuid` all survive intact.
- **Three corruption vectors found by experiment**, not by reading source: note
  indentation compounds on every save cycle (0 → 8 → 44 spaces), `equipped`
  cascades through containers, and a rename lands in `name` while
  `originalName` stays put. See `docs/05-fidelity.md` §5.7.
- **A common ancestor turns inference into deduction.** A two-way `--base`
  merge cannot tell "the player didn't touch this" from "the export is stale
  and the GM's edit would be reverted" — they look identical. The snapshot
  store keeps that ancestor, keyed by content hash and looked up by row TID, so
  the two cases stop being the same observation. See `docs/06-architecture.md`
  §6.9.

## License

This project is licensed under the **GNU General Public License v3.0** — see
[`LICENSE`](LICENSE).

Three files in this repository came from elsewhere and keep their own terms:

| File | Origin | License |
|---|---|---|
| `samples/upstream/issue767.gcs` | [GCS](https://github.com/richardwilkes/gcs) test data | MPL-2.0 |
| `samples/upstream/container_with_own_data.eqp` | GCS test data | MPL-2.0 |
| `src/json2gcs/data/default.gcs` | GCS's own default sheet, captured from the application | MPL-2.0 |

MPL-2.0 is file-level copyleft: those three files stay under MPL-2.0 wherever
they go, and §3.3 is what permits shipping them inside a GPL-3.0 larger work.
Modify one of them and the modification is MPL-2.0 too — the rest of the tree
is unaffected.

Nothing is copied from [GGA](https://github.com/crnormand/gurps) (MIT); it was
read to write `docs/03-foundry-format.md` and nothing more. Both upstreams are
pinned by commit in [`docs/00-provenance.md`](docs/00-provenance.md).

GURPS is a trademark of Steve Jackson Games. This project is an unofficial fan
tool, is not affiliated with or endorsed by SJG, and contains no game text.
