# GURPS Sheet Migrator

Converts a GURPS character exported from **Foundry VTT** (via the
[GURPS Game Aid](https://github.com/crnormand/gurps) system) back into a **GCS**
(`.gcs`) character sheet — closing a loop that currently only runs one way.

**Status: Phase 2 — the merge path works end to end.** Read a Foundry export
and the original `.gcs`, and it writes a merged sheet: `json2gcs convert`.
Still to come: refreshing `calc` so output re-imports cleanly into Foundry, and
packaging as an executable.

```bash
python -m pip install -e ".[dev]"
```

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

`inspect` gives a plainer summary of one export. `inspect` and `diff` never
write; `convert` writes only to its output file.

Changes flagged **lossy** are reported but not written unless you pass
`--include-lossy`; changes flagged **blocked** are never written, because the
Foundry value is known to be contaminated (a post-modifier weight, say). Rows
missing from the export are kept by default — they are ambiguous — and
`--deletions drop` removes them instead.

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
| [`docs/06-architecture.md`](docs/06-architecture.md) | Proposed pipeline, verification strategy, language choice, build order |

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
  report.py              renders a reconciliation as readable text
  cli.py                 command line entry point
tests/                   pytest suite (198 tests)
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
- **Containers round-trip cleanly.** `samples/container/` covers nesting two
  levels deep across traits, skills and both equipment lists; uppercase TIDs,
  `contains` and `parentuuid` all survive intact.
- **Three corruption vectors found by experiment**, not by reading source: note
  indentation compounds on every save cycle (0 → 8 → 44 spaces), `equipped`
  cascades through containers, and a rename lands in `name` while
  `originalName` stays put. See `docs/05-fidelity.md` §5.7.
