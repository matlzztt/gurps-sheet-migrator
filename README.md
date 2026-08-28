# GURPS Sheet Migrator

Converts a GURPS character exported from **Foundry VTT** (via the
[GURPS Game Aid](https://github.com/crnormand/gurps) system) back into a **GCS**
(`.gcs`) character sheet — closing a loop that currently only runs one way.

**Status: Phase 2 — both modes work, and GCS itself verifies them.** Read a
Foundry export and the original `.gcs`, and it writes a merged sheet that GCS
loads and rewrites unchanged.

**Picking this up?** Read [`docs/07-handoff.md`](docs/07-handoff.md) — current
state, the agreed next steps, and the environment facts that are not
discoverable from the code (notably: GCS lives at `C:\GOTProject\gcs\gcs.exe`
and is not on `PATH`).

```bash
python -m pip install -e ".[dev]"
```

**Just want to use it?** Run the executable with no arguments and a window
opens: choose the Foundry export, and it finds the sheet, picks the mode and
fills in the output path. Preview shows the report; Convert writes the file.

```bash
python -m pip install -e ".[build]"
python -m PyInstaller --distpath build/dist json2gcs.spec
```

That produces one self-contained `build/dist/json2gcs.exe` (~13 MB) needing no
Python. Run it with arguments and it is the command line below, unchanged.

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

Point at the binary with `--gcs PATH` or `JSON2GCS_GCS` if it is not on `PATH`.

For an actor that was never in GCS — a GCA import, a hand-built NPC, or a
character whose `.gcs` is lost — build a sheet from the export alone:

```bash
json2gcs convert actor.json --synthesize --refresh-calc
```

The defaults are GCS's own: the template it starts from is what GCS writes when
handed a stub file, and a test re-derives it from the application on every run.
Rows keep the TIDs they already had, so the result is a usable base to merge
into later. Honestly lower fidelity, though — modifiers, features, tags,
library links and a skill's difficulty letter are not in a Foundry export to
recover, and `docs/07-handoff.md` says so field by field.

`inspect` gives a plainer summary of one export. `inspect` and `diff` never
write; `convert` writes only to its output file.

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

## The short version

GGA can import `.gcs` into Foundry. Nothing goes the other way. But GGA copies
GCS's object IDs verbatim into each Foundry row's `uuid`, and Foundry's export
writes them back out — so every Foundry row can be matched to the exact GCS row it
came from.

That matters because the forward conversion is *heavily* lossy: modifiers,
features, prereqs, tags, library links, and the point-buy inputs are all either
flattened into display strings or dropped. Reconstructing them from the Foundry
JSON alone is impossible.

So the tool has two modes:

- **Merge** (primary) — Foundry export **+** the original `.gcs`. Match by ID and
  write back only the fields Foundry is authoritative for. High fidelity.
- **Synthesize** (fallback) — Foundry export alone. Structurally valid, GCS
  defaults, honestly lower fidelity.

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
| [`docs/06-architecture.md`](docs/06-architecture.md) | Pipeline, verification strategy, language choice, build order |
| [`docs/07-handoff.md`](docs/07-handoff.md) | **Current state, next steps, and the traps that cost time** |
| [`docs/08-improvements.md`](docs/08-improvements.md) | The backlog: known gaps, how each was found, and what closing it takes |

## Layout

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
  report.py              renders a reconciliation as readable text
  cli.py                 command line entry point
  gui.py                 tkinter window; builds a command line and runs it
  __main__.py            entry point for `python -m` and the packaged .exe
  data/default.gcs       GCS's own empty sheet, the synthesize template
json2gcs.spec            PyInstaller build definition
tests/                   pytest suite (277 tests)
docs/                    the Phase 1 analysis
samples/sturm/           the regression fixture — one character, both formats
samples/container/       container + known-changelog fixture set
samples/upstream/        GCS-authored fixtures from the gcs repo
gcs/                     upstream clone, git-ignored (richardwilkes/gcs)
gurps/                   upstream clone, git-ignored (crnormand/gurps)
```

Run the tests with:

```bash
python -m pytest
```

The two upstream clones are reference material, not dependencies. They are
git-ignored and pinned by commit in `docs/00-provenance.md`; re-clone them with the
commands there if you need them.

## Key findings so far

- **IDs survive the round trip.** 22/22 traits, 21/21 skills, 26/26 equipment
  matched by ID across the sample pair.
- **`calc` is write-only in GCS.** Every unmarshaller discards it, so we never have
  to reimplement GCS's point maths or damage resolution — GCS recomputes on open.
  (We emit it anyway, so GGA can re-import our output.)
- **`gcs --convert` is a free validator.** The GCS binary will load, rewrite, and
  exit headlessly. Diffing our output against its rewrite makes GCS's own
  serializer the test oracle.
- **The lossy fields are enumerable**, not a vague fog — see `docs/05-fidelity.md`.
- **A control export is a sharp acceptance test.** Exported with nothing touched,
  it must reconcile to *zero* applicable changes. The first run reported 32 —
  every one a real defect in the field policy. See `docs/05-fidelity.md` §5.8.
- **Zero means delete.** GCS's `omitzero` tags mean it never writes
  `equipped: false`; it omits the key. Un-equipping is a deletion, and the
  writer has to know that for every field.
- **Merge preserves by construction.** The writer edits the base structure in
  place, so modifiers, features, prereqs, library `source` links, settings and
  the points log survive because nothing touches them — not because they were
  copied correctly.
- **GCS validates the output.** `gcs --convert` runs headlessly, so the real
  application is the test oracle. It accepts our merged sheet and rewrites it
  identically apart from `calc`, which it recomputes — and that recomputation
  confirms the edits landed. See `docs/05-fidelity.md` §5.9.
- **`calc` is delegated, not reimplemented.** `--refresh-calc` runs the output
  back through GCS, so no GURPS arithmetic had to be rewritten.
- **Containers round-trip cleanly.** `samples/container/` covers nesting two
  levels deep across traits, skills and both equipment lists; uppercase TIDs,
  `contains` and `parentuuid` all survive intact.
- **Three corruption vectors found by experiment**, not by reading source: note
  indentation compounds on every save cycle (0 → 8 → 44 spaces), `equipped`
  cascades through containers, and a rename lands in `name` while
  `originalName` stays put. See `docs/05-fidelity.md` §5.7.
