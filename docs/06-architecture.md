# 6. Proposed architecture

This is a proposal for Phase 2, derived from the findings in docs 1–5. Nothing
here is built yet.

## 6.1 Pipeline

```
                      ┌──────────────────────┐
  foundry export ───► │  read + normalize    │
   (Actor JSON)       │  → intermediate      │
                      └──────────┬───────────┘
                                 │  flat rows keyed by TID
                                 ▼
  base .gcs ───────►  ┌──────────────────────┐
   (optional)         │  match by TID        │───► reconcile report
                      │  classify each row   │     (added / changed / removed /
                      └──────────┬───────────┘      ambiguous)
                                 ▼
                      ┌──────────────────────┐
                      │  apply field policy  │  ← the ✅/⚙️/🔶/❌/🗑️ table
                      │  (per docs/04)       │     from docs/04-mapping.md
                      └──────────┬───────────┘
                                 ▼
                      ┌──────────────────────┐
                      │  serialize GCS v5    │  tabs, LF, raw UTF-8,
                      │  + synthesized calc  │  struct field order
                      └──────────┬───────────┘
                                 ▼
                           output .gcs
```

Four stages, each independently testable: **read → reconcile → apply → write**.

## 6.2 The field policy is data, not code

Docs 04's table is the specification. Encode it as a declarative table rather than
scattering `if` statements through the transform:

```python
FieldRule(
    gcs_path="equipment[].base_weight",
    foundry_path="equipment.carried[].weight",
    fidelity=LOSSY,
    merge="prefer_base_unless_no_modifiers",
    synthesize="append_default_unit",
)
```

Benefits: the policy can be reviewed against the docs without reading transform
code; `--explain` can print why any given field was or was not written; and adding
GGA-version-specific quirks becomes a table edit.

## 6.3 Reconciliation, and the one genuinely ambiguous case

For each collection, partition rows by TID:

| Case | Meaning | Action |
|---|---|---|
| in both | edited in play | apply the field policy |
| Foundry only, `save: true` | added in Foundry | mint a TID (correct kind prefix!), synthesize a minimal row |
| Foundry only, no `save` | shouldn't happen; treat as added | same, but warn |
| base only | **ambiguous** | see below |

A row in the base GCS but not in the Foundry export is either *deleted in Foundry*
or *added to GCS after the export*. Nothing in either file distinguishes them. The
sample has exactly this: `Tracking`, `Jumping`, `Climbing`.

Compare `system.lastImport` (`"Aug 26 2026 20:13:18"`) against the base file's
`modified_date` (`2026-08-27T02:30:34-03:00`). The GCS file is newer, so
"added in GCS afterwards" is the likely reading — but it is a heuristic, not proof.

**Default to keeping the row, report it, and offer `--deletions=keep|drop|ask`.**
Silently dropping a player's skills is the worst possible failure mode.

## 6.4 Emitting `calc`

GCS ignores `calc` on load (`docs/02` §2.6), so for GCS-only output we could omit
it. But GGA's importer **hard-refuses** a file without a top-level `calc`, and reads
`calc.points`, `calc.level`, `calc.rsl`, `calc.damage`, `calc.extended_value`,
`calc.extended_weight` throughout. Since the whole point is a Foundry ⇄ GCS loop,
emit it.

The good news: every value GGA reads from `calc` is already sitting in the Foundry
export, because GGA put it there. So `calc` is a **copy-back**, not a computation:

| `calc` field | Copy from |
|---|---|
| top-level `swing` / `thrust` / `basic_lift` | `system.swing` / `system.thrust` / `system.liftingmoving.basiclift` |
| top-level `move[]` / `dodge[]` | `system.encumbrance[].move` / `.dodge` |
| trait `calc.points` | `ads[].points` |
| skill `calc.level` / `calc.rsl` | `skills[].import` / `.relativelevel` |
| equipment `calc.extended_value` / `extended_weight` | `costsum` / `weightsum` |
| weapon `calc.level` / `damage` / `range` | `melee[]`/`ranged[]` `import` / `damage` / `range` |

In merge mode, prefer the base file's `calc` for rows Foundry did not change.
Mark it in the output as advisory (GCS overwrites it on first save anyway).

## 6.5 Verification — use GCS itself

**The installed GCS application is a headless validator.** `gcs/main.go` exposes:

```
--convert   load every file given (recursively for directories),
            rewrite it in the current data format, then exit
--text      render via a text template and exit
```

That gives a real acceptance test with no GUI:

1. Write `out.gcs`.
2. Run `gcs --convert out.gcs`.
3. Non-zero exit or an error line → the file is malformed.
4. Zero exit → diff our bytes against GCS's rewrite. **Any difference is a
   serialization deviation on our side** — wrong key order, a `null` where
   `omitzero` applies, wrong indent, escaped UTF-8.

Step 4 is the highest-value test in the project: it makes GCS's own serializer the
oracle, so we never have to reason about Go struct field order by hand.

**This is now implemented and passing** (`tests/test_oracle.py`, skipped when GCS
is absent). GCS accepts our merged output and rewrites it identically apart from
`calc`, which it recomputes — see `docs/05-fidelity.md` §5.9. The same mechanism
gave us `--refresh-calc` for free.

Layer beneath it:

- **Round-trip test.** `sturm.gcs` → (GGA import rules, reimplemented) → synthetic
  Foundry JSON → converter → `.gcs`; assert only the documented lossy fields differ.
- **Golden test on the real pair.** Convert `sturm.foundry.json` with `sturm.gcs`
  as base; assert the diff contains exactly the five divergences in `docs/05` §5.1
  and nothing else. This is the regression net.
- **Idempotence.** Converting twice must produce identical bytes. This is what
  catches the accumulating-modifier-names-in-notes bug (`docs/04` §4.4).

## 6.6 Language choice

**Decided: Python 3.12+, packaged with PyInstaller.**

The work is JSON tree manipulation, string parsing, and policy application — no
hot loops, no concurrency. Python gets to a working tool fastest, and
`pyinstaller --onefile` produces the double-clickable `.exe` the project is
ultimately aiming for. A Tk or web-view GUI can wrap the same core later.

Two alternatives were considered:

**Go, reusing GCS's own model package.** Very attractive in principle — importing
`github.com/richardwilkes/gcs/v5/model/gurps` would make our writer schema-correct
by construction and automatically track upstream. **Rejected:** 17 files in that
package, including `trait.go`, `skill.go`, `equipment.go`, `note.go`, `weapon.go`
and `profile.go`, import `github.com/richardwilkes/unison` — a Skia-backed GUI
toolkit needing CGO. Pulling a GUI stack into a CLI converter is not worth it, and
maintaining a unison-stripped fork is ongoing work. (Revisit if upstream ever
splits the model from the UI types.)

**Go, with hand-written structs.** Single static binary, no runtime, no CGO —
genuinely nice for distribution. Costs more up-front code for the same result.
Reasonable if the project later prioritizes distribution over iteration speed.

Either way, `gcs --convert` (§6.5) remains the oracle, so the implementation
language does not compromise correctness.

### Concrete Python stack

| Concern | Choice | Why |
|---|---|---|
| CLI | `argparse` (stdlib) | zero dependencies; the surface in §6.7 is small |
| JSON | stdlib `json` | `object_pairs_hook=dict` preserves key order on read; `ensure_ascii=False` for raw UTF-8 on write |
| Serializer | hand-written emitter, **not** `json.dump` | `json.dump` cannot produce tab indent + GCS's struct field order + `omitzero` suppression. Field order comes from an explicit key list per row type, checked against §6.5 step 4 |
| Models | `dataclasses` | plain, inspectable, no runtime dependency |
| Tests | `pytest` | golden files under `tests/golden/` |
| Packaging | `pyinstaller --onefile` | the clickable `.exe` |

Two Python-specific traps for the writer:

- **Newlines.** Open every output file with `newline='
'`, or Windows turns GCS's
  LF into CRLF and every byte-comparison test fails.
- **Numbers.** GCS writes `0.25`, not `0.250000`; and `fxp.Int` is fixed-point, so
  binary floats can drift. Parse decimals with `decimal.Decimal` and format by
  trimming trailing zeros, never via `repr(float)`.

## 6.7 CLI shape

```bash
json2gcs convert actor.json --base character.gcs -o character.gcs
```

```bash
json2gcs convert actor.json --synthesize -o new-character.gcs
```

Worth having early:

- `--dry-run` — print the reconcile report, write nothing.
- `--report report.md` — per-field record of what was written, skipped, and why.
- `--deletions keep|drop|ask` — §6.3.
- `--verify` — shell out to `gcs --convert` and diff (§6.5).
- Auto-discovery of the base file from `system.additionalresources.importname`
  (`"Stürm.gcs"` in the sample) when `--base` is omitted.
- Refuse, loudly, when `items[]` is non-empty (Foundry-items mode) or
  `_stats.systemVersion` is outside the validated range.

Default to **never overwriting the base file in place** — write beside it and let
the user swap.

## 6.8 Build order

1. ~~Reader + normalizer for the Foundry export, with the flat-by-TID index.~~
   **Done** — `src/json2gcs/foundry.py`, `src/json2gcs/tid.py`.
2. ~~GCS reader/writer that round-trips `sturm.gcs` **byte-identically**.~~
   **Done** — `src/json2gcs/jsonio.py`. Round-trips all three available GCS
   files byte-for-byte, including the container fixture.
3. ~~Reconciler + report.~~ **Done** — `src/json2gcs/reconcile.py`,
   `fields.py`, `report.py`, and `json2gcs diff`. Writes nothing.
   The acceptance test is the control export: taken with nothing touched, it
   must yield **zero** applicable changes. Getting there took four corrections,
   each a real thing GGA does to the data (see `docs/05-fidelity.md` §5.8).
4. ~~Field policy table, starting with the ✅ rows only.~~ **Done** — `fields.py`.
5. ~~⚙️ rows (attributes via the points route, SM, dates).~~ **Done** —
   attributes invert through `points / cost_per_point` as designed. (`tags`
   turned out to be ❌, not ⚙️ — see `docs/04-mapping.md` §4.5.)
6. ~~🔶 rows, each behind its own guard.~~ **Done** — reported, never written
   unless `--include-lossy`, and blocked outright where the value is known to
   be contaminated.
7. ~~Writer.~~ **Done** — `apply.py` and `json2gcs convert`. Edits the base
   structure in place so everything Foundry never knew about survives by
   construction rather than by being copied correctly.
8. ~~`calc` refresh.~~ **Done**, and more cheaply than planned — `--refresh-calc`
   runs the output back through `gcs --convert`, so GCS computes its own derived
   values. No GURPS arithmetic reimplemented. See `docs/05-fidelity.md` §5.9.
9. Foundry re-import as an end-to-end check — needs a Foundry session.
10. Packaging.

### What the writer had to get right

**Setting a field to its zero value means deleting the key.** Almost every Go
tag is `omitzero`, so GCS never writes `equipped: false` — it omits the field.
Un-equipping is a deletion, not an assignment. `equipment.quantity` is the one
row field without `omitzero`, so a zero quantity really is written.

**New keys need canonical placement.** GCS writes in Go struct declaration
order, so `schema.py` carries the order transcribed from the struct definitions,
with embedded structs flattened at the position of the embedded field.
`tests/test_schema.py` checks the transcription against every row of every
fixture — 154 rows agree, which is what makes it trustworthy rather than
plausible.

**Idempotence is a test, not an aspiration.** Converting, re-reading and
converting again must be a no-op. That is what would catch the compounding
failures — modifier names accumulating in notes, indentation growing — and it
is locked in `tests/test_apply.py`.

Steps 1–3 are worth having before any mapping work, because a correct
byte-identical writer plus an honest report is already a usable tool: it tells the
user exactly what changed in play, even before it can apply the changes.

## 6.9 The snapshot store — turning inference into deduction

Proposed 2026-09-02, phase 0 built. Everything below applies to **merge mode**;
mode B is what it makes rare.

### The problem it solves

`--base current.gcs` is a **two-way** merge, and it silently assumes the sheet
is still what Foundry imported. When it is not, the failure is invisible: every
field the player did not touch reads as "unchanged" against a *stale* export,
so an edit made in GCS after the export gets reverted by a value nobody typed.
Nothing in the report can show this, because from a two-way comparison it is
indistinguishable from a real match.

The fix is the one every version-control system uses: keep the **common
ancestor**. A copy of the sheet as it was when Foundry imported from it turns a
two-way merge into a three-way one, and "the player changed it", "the GM changed
it" and "both changed it" stop being the same observation.

The same store answers a second question — *where is the base?* — because the
export carries the row TIDs, and a TID is an identity, not a name. Matching by
it is deduction. This is the whole reason to prefer a store over the GCS Master
Library (`docs/08-improvements.md`): the library can only ever tell us what the
*canonical* version of a row looks like, while the store knows what **this
character's** row actually was, homebrew and player customizations included.

### What the export gives us to work with

Measured on `samples/container/`:

| | |
|---|---|
| `system.additionalresources.importname` | `"container.gcs"` — names the source sheet |
| `system.lastImport` | `"Aug 27 2026 14:13:00"` — when Foundry imported |
| row TIDs resolving against the sheet | **77 of 77** |

`lastImport` is written by `actor-importer.js` as
`new Date().toString().split(' ').splice(1, 4).join(' ')` — JavaScript's fixed
date format with the weekday and the zone stripped. The format is
locale-independent by specification, so parsing it is safe; what it loses is the
offset, leaving browser-local time.

### Phases

**Phase 0 — say when the ancestor is missing. Built.**
`reconcile._import_is_stale` compares the sheet's `modified_date` against
`lastImport` and warns when the sheet is the newer of the two. It needs no
store at all, and it converts the silent failure above into a visible one. It
reports both timestamps rather than asserting a verdict, because the comparison
is exact only when both were written on the same machine — `lastImport` has no
zone to compare against. Guarded on `sheet.by_tid` being non-empty, so mode B,
which merges against a freshly stamped empty sheet, never trips it.

**Phase 1 — remember.** `json2gcs remember <sheet.gcs>`, plus an automatic
remember on every `convert --base`. Store the **whole file bytes**, not an
extraction: they are 20–100 KB, it is lossless by construction, and this project
already treats the byte stream as the contract (§6.5). Index row TID → snapshot,
so lookup never depends on a filename. Seeding is retroactive — any `.gcs` still
on disk can be remembered now, and every later export from it is covered.

**Phase 2 — find the base.** `convert export.json` with no `--base` looks its
own row TIDs up in the store. Mode B stops being the fallback for "I don't have
the sheet handy" and becomes what it should be: the mode for characters that
genuinely never had one.

**Phase 3 — three-way reconcile.** Snapshot as ancestor, current sheet as
theirs, export as ours. A field that changed on one side only is applied; a
field that changed on both is a **conflict** and gets reported instead of
silently resolved in Foundry's favour, which is what happens today.

### What it does not cover

Rows added inside Foundry have no TID and therefore no snapshot entry
(`docs/08-improvements.md` §8.6), and a character with no GCS origin has no
snapshot at all. Those are exactly the cases the Master Library idea addresses,
so the two compose rather than compete — **snapshot first (deduction), library
second (inference, marked as such), honest omission third.**
