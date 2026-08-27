# 5. Fidelity — what survives, what does not, and what actively corrupts

Everything here is measured against `samples/sturm/`, not assumed.

## 5.1 The sample pair, verified

The Foundry actor was imported from `sturm.gcs`; the GCS file was then edited.
Comparing the two by TID, matched rows agree almost perfectly:

| Check | Result |
|---|---|
| Trait IDs | 22 / 22 matched |
| Trait `calc.points` vs Foundry `points` | **all 22 identical** |
| Skill IDs | 21 / 21 Foundry rows matched (GCS has 3 more) |
| Skill `points` vs Foundry `points` | **all 21 identical** |
| Equipment IDs | 26 / 26 matched |
| Equipment name, `quantity`/`count`, `tech_level`, `equipped` | all identical |
| Melee weapons | 8 in GCS (5 equipment, 3 traits) / 8 in Foundry |
| Ranged weapons | 3 / 3 |

The known, expected divergences:

1. **Three skills exist only in GCS** — `Tracking`, `Jumping`, `Climbing`, added
   after the export.
2. **`total_points` 209 vs `totalpoints.total` 206** — a 3-point "Session award"
   recorded in `points_record` after the export.
3. **Two nameable traits differ by design** (§5.2).
4. **`legality_class` appears in Foundry but not GCS** (§5.3).
5. **`base_weight` / `base_value` differ where modifiers apply** (§5.4).

This is a good fixture precisely because those five categories are the five real
hazards.

## 5.2 Nameable templates get resolved away

GCS stores a template plus a substitution map. Foundry stores only the result.

| GCS `name` | GCS `replacements` | Foundry `originalName` |
|---|---|---|
| `@Type@ Rank` | `{"Type": "Religious"}` | `Religious Rank` |
| `Duty (@Duty@)` | `{"Duty": "the Yarga"}` | `Duty (the Yarga)` |

Naively writing `originalName` back into `name` destroys the template and orphans
`replacements`, and GCS will then render `Religious Rank` with a stale replacement
map. The same applies to `local_notes` — `_resolveNotes()` prefers
`calc.resolved_notes`, the already-substituted text.

**Rule: never write `name` or `local_notes` back onto a row whose base-file version
contains `@…@`.** In synthesize mode there is no template to protect, so the
resolved string is simply the best available answer.

Techniques take this further. In the sample:

```
GCS   "Targeted Attack (@Skill@ @Attack@/Vitals)"
FVTT  "Targeted Attack (Spear Thrust/Vitals) ([object Object])"
```

GGA appends the technique's base skill in a second parenthesized group, and
stringifies `default.specialization` — an object `{compare, qualifier}` — straight
into the name. Strip a trailing `([object Object])` group before any name matching,
or it will poison fallback name-based matching in synthesize mode.

## 5.3 Defaults get injected on the way in

`importEq` fills a default where GCS omitted the field:

```js
e.legalityclass = i.legality_class || '4'
```

Six equipment rows have no `legality_class` in GCS and `"4"` in Foundry. Writing
that back adds a field the sheet never had. Harmless in isolation, but it makes
every round trip dirty the file and pollutes diffs.

**Rule: for any field GGA defaults, suppress the write when the Foundry value
equals GGA's default and the base file omits the field.** Known defaulted fields:
`legalityclass` → `'4'`, `categories` → `''`, `uses`/`maxuses` → `0`,
`cr` → `null`, `originalCount` → `1`.

## 5.4 Computed values masquerading as inputs

The clearest case in the sample is `Cloth, Padded`:

| | GCS (input) | Foundry (what it stored) |
|---|---|---|
| weight | `base_weight: "1"` | `weight: "8"` |
| value | `base_value:` *absent* | `cost: "50"` |

An equipment modifier multiplies the base weight up to the 8 lb that
`calc.extended_weight` reports, and `importEq` stores **that** as the item's
weight. Write it back into `base_weight` and the modifier is applied twice — this
round trip time and every one after. The same item's `cost` of `50` is entirely
modifier-derived; GCS had no `base_value` at all.

Foundry also drops the unit: GCS `"2.25 lb"` becomes `"2.25"`. And GCS accepts
unitless values (`base_weight: "0.1"` on one row), so a missing unit is not itself
a reliable signal of anything.

Arrows show the quantity division working correctly: GCS `base_value "2"` ×
quantity 10 → `calc.extended_value 20` → Foundry `cost = 20 / 10 = "2"`. ✓

**Rule: treat `cost` and `weight` as read-only in merge mode unless the base row
has no modifiers.**

## 5.5 The full loss inventory

Present in GCS, absent from the Foundry export, unrecoverable without the base file:

**Structure and rules data**
`settings` in its entirety — attribute definitions (`base` expressions,
`cost_per_point`), the `body_type` hit-location table, `damage_progression`,
`default_length_units` / `default_weight_units`, `page`, `block_layout`, all
display preferences · `prereqs` on every row · `features[]` on traits, skills and
equipment (Foundry keeps only the aggregated `reactions` / `conditionalmods`
projections) · skill `defaults[]` and `difficulty` letter · technique `default`
and `limit` · `study[]` / `study_hours_needed`.

**Modifiers**
Every trait `modifiers[]` (23 in the sample) and equipment `modifiers[]` (20).
Only the *names of the enabled ones* survive, glued onto the notes string.
Disabled modifiers vanish entirely.

**Provenance and organization**
`source` (`{library, path, id}`) on 29 rows — the link back to the GCS master
library, which drives GCS's "sync to library" feature · `tags[]` on **every**
row type. An earlier draft of this document had equipment tags surviving as
`categories`; they do not. `importEq` reads `i.categories`, and GCS v5 writes
the field as `tags`, so `categories` is empty on every equipment row in every
fixture.

**Points inputs**
Trait `base_points` / `points_per_level` / `can_level` / `round_down` /
`max_levels` — Foundry has only the evaluated total.

**Identity**
`profile.organization` (no Foundry field exists at all) · `profile.portrait`
(Foundry's `img` is a world-relative path, not bytes) · `points_record[]` ·
`third_party` · every weapon TID.

**Weapons**
Structured `damage` (`{type, st, base}` → `"1d+1 imp"`), `range` formula
(`"x20/x25"` → `"200/250"`), `defaults[]`, `usage_notes`, `hide`.

## 5.6 Risks not covered by the sample

Things the fixture cannot validate, ranked by how likely they are to bite:

1. ~~**Containers.**~~ **Resolved** by `samples/container/` — see §5.7. The
   `sturm` fixture is flat, but a purpose-built pair now covers containers up to
   two levels deep in traits, skills, carried equipment and other equipment.

2. **Spells.** `system.spells` is `{}` and `spells` is absent from the GCS file.
   The mapping is assumed to parallel skills; it is unverified.
3. **Foundry-items mode.** `SETTING_USE_FOUNDRY_ITEMS` moves data into real
   `Item` documents; `items: []` here. Detect it and refuse rather than drop data.
4. **Rows added inside Foundry.** Still uncovered — the one attempt to add a
   skill and an item through the sheet did not take. `addItemMenu` in
   `gurps/module/actor/actor-sheet.js` mints an id via `_getGGAId()`, which is
   **not** a GCS TID, and only sets `save: true` in Foundry-items mode. So the
   reader must treat "no valid TID" as the primary signal, which it does — but
   the shape of a real GGA-minted id is still unverified.
5. **Rows deleted inside Foundry.** **Resolved** — see §5.7. The row simply
   disappears and the collection renumbers. It remains *indistinguishable* from
   "added to GCS after the export" when comparing a single export against a
   sheet, so the ambiguity is a reconciler policy question, not a data one.
6. **Non-humanoid body plans**, alternate `damage_progression`, metric units.
7. **Version skew.** The sample was exported by GGA `0.18.13`; the pinned clone is
   `0.18.22`. GGA's actor schema does move between minor versions.
8. **`QN`/`QP` (Quintessence)** — a GGA-specific optional attribute pair with no
   entry in the sample's GCS `settings.attributes`. Writing `qn`/`qp` attributes
   into a sheet whose settings do not define them will produce rows GCS ignores or
   flags.

## 5.7 The container fixture, and what it exposed

`samples/container/` is a controlled experiment rather than a found artifact:

| File | What it is |
|---|---|
| `container.gcs` | Stürm with four containers added — trait, skill, carried equipment (nested two deep: Backpack → Metabackpack → The Book of Lines) and other equipment |
| `container.foundry.json` | exported **immediately after import**, nothing touched |
| `container-played.foundry.json` | exported again after a known list of edits |

The control export is the valuable half: with no play in between, every
difference from the GCS file is GGA's transform and nothing else. The played
export supplies a ground-truth changelog for the reconciler.

### Containers round-trip cleanly

Verified in `tests/test_containers.py`:

- Uppercase TIDs survive: `T_7dZkq1Ziwxfz--o`, `Sjj3Skr06jC0nmHcX`,
  `Et0bRTzaIEVIXAlQi`, `EeBidMeu7scIX-zhc`, `Ekb27Az5KUlErsNLy`.
- `contains` and `parentuuid` agree with each other and with the sheet — the
  full set of parent/child edges is identical on both sides.
- Depth is preserved; a three-level chain arrives intact.
- `carried` propagates: everything inside a carried container is carried, and
  everything inside the Other Equipment container is not.

So the reader needed no changes, and the synthetic tests written from
`foldList()` turn out to have modelled it correctly.

### Three things the fixture caught that source reading had not

**1. A rename lands in `name` only.** Renaming *The Book of Lines* to *The Book
of Metabackpacking* in Foundry changed `name` and left `originalName` at the GCS
value. That makes `originalName` a genuinely stable anchor — and it also means a
"display name" accessor that prefers `originalName` will silently miss real
renames. `Row.gcs_name` and `Row.display_name` are now separate for this reason.

**2. `equipped` cascades through containers.** Un-equipping the Backpack cleared
`equipped` on the Metabackpack, the Book inside it, and the Horn-tip — four rows
from one action. A naive per-row diff reports four independent edits. The
reconciler should recognise a cascade and carry back the intent, not the
fan-out.

**3. Note indentation compounds on every save cycle.** `Green Sight`'s note,
which the player never touched:

| | max leading whitespace |
|---|---|
| `container.gcs` | 0 |
| after import | 8 |
| after one save | 44 |

The text is identical once runs of spaces are collapsed. Writing Foundry's
`notes` verbatim into `local_notes` would import this and make it worse every
round trip — the compounding-corruption failure mode predicted in
`docs/06-architecture.md` §6.5, arriving by a different mechanism than expected.
**Notes must be compared whitespace-insensitively and must never be copied
verbatim.**

### Two more fields confirmed as derived (🗑️)

- **`skills[].level`** is `""` right after import and an integer afterwards, for
  every skill the player never touched. It is a lazily populated display value,
  not an input. (`points` — a real GCS input — did not move.) Only the skill
  *container*, which has no level, stayed empty.
- **`equippedparry` / `equippedblock`** are `null` after import and computed
  later (`10` and `11`).

Anything that materialises between two exports with no player action in between
is by definition derived. That is a reusable test, and worth re-running against
any future fixture pair.

## 5.8 The control test, and the four corrections it forced

The control export — taken immediately after import, nothing touched — gives an
acceptance criterion sharp enough to be worth building around:

> **Reconciling the control export against `container.gcs` must produce zero
> applicable changes.**

Anything it reports is by definition a phantom: something GGA does to the data
on the way in that the comparison failed to account for. The first run reported
**32 changed rows**. Each was a real defect in the field policy, not noise to be
tuned away.

**1. Weights lose their unit.** GCS `"2.25 lb"` arrives as `"2.25"`, because
`importEq` divides `calc.extended_weight` by the quantity and keeps the number.
Fixed with a quantity comparison that matches magnitudes and ignores a trailing
unit. *(19 rows.)*

**2. GGA fills absent values with `"0"`.** GCS omits `base_value` on anything
free; Foundry reports `cost: "0"`. Writing that back adds a field the sheet
never had. Fixed by suppressing a proposal that equals GGA's default when the
base row omits the field — the same mechanism §5.3 needed for `legality_class`.
*(18 rows.)*

**3. Notes must be compared against a reconstruction, not against
`local_notes`.** GGA glues every enabled modifier's *name* onto the note, so a
row with modifiers reports a phantom edit forever. Fixed by replaying that
concatenation from the base row and comparing against the result:

```
local_notes + "; " + each enabled modifier's name + "\n" + userdesc
```

One detail matters: GGA reads `modifier.notes`, but GCS v5 writes `local_notes`,
so the parenthetical GGA means to add never appears. The reconstruction has to
reproduce the bug, not the intent. Self-control rows are the one case this
cannot handle — GGA replaces the note with a localized `[CR: name]` string — so
those are skipped rather than guessed at. *(3 rows.)*

**4. Equipment tags never survive at all.** `importEq` reads `i.categories`;
GCS v5 calls the field `tags`. The mapping table had this as ⚙️ derivable and it
is ❌. *(5 rows.)*

That left two rows, both genuine and both correctly withheld: `Cloth, Padded`
(modifier-inflated weight and value, §5.4) and `Poison doses in sealed gut`
(unitless base weight on a metric sheet, `docs/04-mapping.md` §4.11).

The reverse test is the played export, which must report **exactly** the edits
that were actually made and nothing else. It does: the arrow count, the rename,
four `equipped` flips (one action plus its three-row cascade), the deleted
skill, the note edit, and HP/FP damage.

Both directions are locked in `tests/test_reconcile.py`. The pair is worth more
than either half: the control catches phantom changes, the played export catches
missed ones, and neither alone would have caught all four corrections above.

## 5.9 Verified against GCS itself

`gcs --convert` loads a file, rewrites it in the current data format and exits.
It runs headlessly, so the real application can be used as the oracle
(`docs/06-architecture.md` §6.5). Three results, all in `tests/test_oracle.py`:

**The fixtures are a fixed point.** `gcs --convert` rewrites `sturm.gcs` and
`container.gcs` byte-for-byte identically. That matters on its own: it means
byte-comparing our writer against them tests something real rather than
comparing against an arbitrary encoding.

*(`issue767.gcs` is the exception, and instructively so — it is upstream's
regression fixture for a damage-calculation bug, recording `"1d-2 cr"` where
current GCS computes `"1d-1 cr"`. The difference is inside a `calc` block and so
is inert.)*

**GCS accepts our merged output and rewrites it identically apart from `calc`.**
This is the strongest available check on the writer: key order, `omitzero`
handling, number formatting, indentation and encoding all agree with GCS's own
serializer, on a file we constructed rather than copied.

**GCS's recomputation confirms the edits landed correctly.** Every `calc`
difference is GCS deriving something from a value we wrote:

| GCS recomputed | Because we wrote |
|---|---|
| `hp.calc.current` 10 → 6 | `hp.damage: 4` |
| `fp.calc.current` 11 → 3 | `fp.damage: 8` |
| Arrow `extended_value` 20 → 8, `extended_weight` 1 lb → 0.4 lb | `quantity: 4` |
| `basic_lift` 20 lb → 5 lb, `move` 6 → 3, `dodge` 9 → 5, two skills −3 | see below |

That last row is worth understanding rather than dismissing. FP 3 of 11 is under
a third, so GCS applies **Very Tired**: ST is halved, 10 → 5. Basic Lift is
ST²/5, so it drops from 20 lb to 5 lb, which pushes the character into a worse
encumbrance band, which costs Move, Dodge, and three levels on every
encumbrance-penalised skill.

Nothing is wrong here — it is the sheet correctly modelling a character at 3 FP.
But it shows how far a single carried-back value propagates, and it is a good
argument for the report naming what it changed rather than just doing it.

### Letting GCS compute `calc`

Since `gcs --convert` produces exactly the right derived values, `--refresh-calc`
runs the output back through GCS. After that the file is a **byte-exact fixed
point** of GCS's own serializer — `--verify` reports "rewrote it identically".

This closes the `calc` question (`docs/02-gcs-format.md` §2.6) without
reimplementing any GURPS arithmetic. GCS ignores `calc` on load, so it is
optional for GCS; GGA's importer *requires* it, so it matters for the trip back
into Foundry. Delegating it to GCS is both less code and more correct than
copying values across from the export would have been.

The flag needs GCS installed. Point at it with `--gcs PATH` or `JSON2GCS_GCS`;
installs are frequently not on `PATH`.
