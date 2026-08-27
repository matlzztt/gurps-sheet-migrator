# GURPS Sheet Migrator

Converts a GURPS character exported from **Foundry VTT** (via the
[GURPS Game Aid](https://github.com/crnormand/gurps) system) back into a **GCS**
(`.gcs`) character sheet — closing a loop that currently only runs one way.

**Status: Phase 1 complete — documentation and problem analysis. No code yet.**
Phase 2 will be written in **Python 3.12+**, packaged with PyInstaller
(see [`docs/06-architecture.md`](docs/06-architecture.md) §6.6).

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
docs/                    the Phase 1 analysis
samples/sturm/           the regression fixture — one character, both formats
  sturm.gcs
  sturm.foundry.json
gcs/                     upstream clone, git-ignored (richardwilkes/gcs)
gurps/                   upstream clone, git-ignored (crnormand/gurps)
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
- **The sample character is completely flat** — no containers anywhere (verified:
  no `children`, no uppercase TIDs, every `contains` empty). Container handling is
  the largest untested area; a fixture with real nesting is the top gap.
  `equipment` / `other_equipment` is a two-list split, not nesting.
