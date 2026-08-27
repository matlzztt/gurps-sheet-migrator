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
4. Field policy table, starting with the ✅ rows only.
5. ⚙️ rows (attributes via the points route, SM, tags, dates).
6. 🔶 rows, each behind its own guard and each with a golden-test case.
7. `calc` emission, then Foundry re-import as an end-to-end check.
8. Packaging.

Steps 1–3 are worth having before any mapping work, because a correct
byte-identical writer plus an honest report is already a usable tool: it tells the
user exactly what changed in play, even before it can apply the changes.
