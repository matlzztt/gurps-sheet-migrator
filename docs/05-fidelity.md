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
library, which drives GCS's "sync to library" feature · `tags[]` on traits and
skills (equipment `tags` survive as `categories`).

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

1. **Containers.** The sample has **none** — re-verified exhaustively:

   - No GCS row in any section carries a `children` key.
   - Every top-level GCS TID is lowercase: `t`×22, `s`×22, `q`×2, `e`×26, `n`×1.
     No `T`, `E`, `S`, or `N`.
   - Every Foundry row has `contains: {}` and `parentuuid: ""`.

   The single container anywhere in the file is a *trait modifier* container —
   `Frequency` on `Duty (@Duty@)`, TID `Mj0nfAjCFkqJZhzsS`, with four children.
   Modifiers do not survive into Foundry at all, so it is irrelevant to the round
   trip.

   **Note on a likely point of confusion:** GCS's two equipment tables, *Carried
   Equipment* (23 rows) and *Other Equipment* (3 rows: `Antler comb`,
   `The heavy roll`, `The stores`), are **two sibling top-level lists**
   (`equipment` and `other_equipment`), not a container relationship. They map to
   Foundry's `system.equipment.carried` / `.other`. Grouping equipment as
   "used vs. unused" this way involves no container and no nesting.

   So trait containers, equipment containers, skill containers, and the
   `parentuuid` ↔ `contains` double-bookkeeping remain **entirely untested**.
   Container TIDs are uppercase and GGA derives row type from that prefix, so
   getting this wrong produces silently mistyped rows. **A fixture with real
   nesting is still needed** — in GCS that means a row created via
   *Edit → New Trait Container* / *New Equipment Container* with rows dragged
   inside it, which renders as an expand/collapse triangle in the sheet.
2. **Spells.** `system.spells` is `{}` and `spells` is absent from the GCS file.
   The mapping is assumed to parallel skills; it is unverified.
3. **Foundry-items mode.** `SETTING_USE_FOUNDRY_ITEMS` moves data into real
   `Item` documents; `items: []` here. Detect it and refuse rather than drop data.
4. **Rows added inside Foundry.** Marked `save: true`, no GCS counterpart, need
   freshly minted TIDs. None in the sample.
5. **Rows deleted inside Foundry.** Present in the base GCS, absent from the
   export. Indistinguishable from "added to GCS after the export" — the sample's
   three GCS-only skills are exactly that. **This is genuinely ambiguous and needs
   a user decision, not a default.**
6. **Non-humanoid body plans**, alternate `damage_progression`, metric units.
7. **Version skew.** The sample was exported by GGA `0.18.13`; the pinned clone is
   `0.18.22`. GGA's actor schema does move between minor versions.
8. **`QN`/`QP` (Quintessence)** — a GGA-specific optional attribute pair with no
   entry in the sample's GCS `settings.attributes`. Writing `qn`/`qp` attributes
   into a sheet whose settings do not define them will produce rows GCS ignores or
   flags.
