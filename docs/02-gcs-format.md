# 2. The GCS file format (`.gcs`, data version 5)

Source of truth: `gcs/model/gurps/` at the pinned revision. Every JSON key below
comes from a Go struct tag, not from guessing at the sample file.

## 2.1 Serialization contract

From `gcs/model/jio/save.go` (`SaveToFile`), confirmed against `samples/sturm/sturm.gcs`:

| Property | Value |
|---|---|
| Encoding | UTF-8, **no BOM** (the loader strips one if present, but GCS never writes one) |
| Indentation | a single **tab** per level (`jsontext.WithIndent("\t")`) |
| Line endings | `LF`, including on Windows |
| Trailing newline | yes, exactly one — added explicitly "for POSIX compliance" |
| Non-ASCII | written raw, not `\u`-escaped (the sample contains `ü` and `†` as literal UTF-8) |
| Key order | Go struct field order — **not** alphabetical. `json.Deterministic(true)` only sorts genuine `map` keys |
| Omission | almost every field is `omitzero`; zero values are absent, not `null` |
| Unknown keys on load | **ignored** — `jio.UnmarshalRead` does not set `RejectUnknownMembers` |

Compressed variants exist (`SerializeAndCompress`) but are not used for `.gcs` sheet files.

## 2.2 TIDs — the identity system

`gcs/model/kinds/kinds.go` defines a one-character kind prefix; a TID is that
character followed by 16 base64url characters (17 total).

| Prefix | Kind | Prefix | Kind |
|---|---|---|---|
| `A` | Entity (the sheet itself) | `n` / `N` | Note / container |
| `t` / `T` | Trait / container | `p` / `P` | Spell / container |
| `s` / `S` | Skill / container | `r` | Ritual magic spell |
| `q` | Technique | `m` / `M` | Trait modifier / container |
| `e` / `E` | Equipment / container | `f` / `F` | Equipment modifier / container |
| `w` | Melee weapon | `W` | Ranged weapon |

Uppercase = the container form of the same kind. This is load-bearing: GGA's
importer derives the row type purely from the prefix —

```js
i.type = i.id.startsWith('t') ? 'trait' : 'trait_container'
i.type = i.id.startsWith('q') ? 'technique' : i.id.startsWith('s') ? 'skill' : 'skill_container'
i.type = i.id.startsWith('e') ? 'equipment' : 'equipment_container'
w.type = w.id.startsWith('w') ? 'melee_weapon' : 'ranged_weapon'
```

so a generated TID **must** carry the right prefix or the round trip breaks.

Where TIDs appear in the sample file:

```
/id                                 → A   (1)
/traits[]/id                        → t   (22)
/traits[]/source/id                 → t   (7)    ← the library row it came from
/traits[]/modifiers[]/id            → m/M (23)
/traits[]/modifiers[]/children[]/id → m   (4)
/traits[]/weapons[]/id              → w   (3)
/skills[]/id                        → s/q (24)
/equipment[]/id                     → e   (23)
/equipment[]/modifiers[]/id         → f   (20)
/equipment[]/weapons[]/id           → w/W (8)
/other_equipment[]/id               → e   (3)
/notes[]/id                         → n   (1)
```

Short `id` values also appear in `settings.attributes[].id` (`"st"`, `"basic_move"`)
and `settings.body_type.locations[].id` (`"torso"`). Those are **not** TIDs — they
are stable string keys. Do not conflate them with length-agnostic matching.

## 2.3 Top-level shape

`EntityData` in `gcs/model/gurps/entity.go`:

| Key | Go type | Notes |
|---|---|---|
| `version` | `int` | must be `5`; `2..5` load |
| `id` | `tid.TID` | `A`-prefixed |
| `total_points` | `fxp.Int` | |
| `points_record` | `[]*PointsRecord` | `{when, points, reason}` audit log |
| `profile` | `Profile` | |
| `settings` | `*SheetSettings` | attribute defs, body plan, page setup, display prefs |
| `attributes` | `*Attributes` | |
| `traits` | `[]*Trait` | |
| `skills` | `[]*Skill` | skills **and** techniques, same array |
| `spells` | `[]*Spell` | |
| `equipment` | `[]*Equipment` | carried |
| `other_equipment` | `[]*Equipment` | not carried |
| `notes` | `[]*Note` | |
| `created_date` | `jio.Time` | RFC3339 with offset, e.g. `2026-08-14T14:10:32-03:00` |
| `modified_date` | `jio.Time` | |
| `third_party` | `map[string]any` | free-form escape hatch — see §2.7 |
| `calc` | *(write-only)* | see §2.6 |

### `profile`

`ProfileRandom` + `Profile` in `profile.go`:

```
name, age, birthday, eyes, hair, skin, handedness, gender,
height (fxp.Length), weight (fxp.Weight),
player_name, title, organization, religion, tech_level,
portrait ([]byte → base64), SM (int)
```

Note the odd one out: the size-modifier key is `SM`, capitalized.

### `settings`

Contains `attributes` — the **full attribute definition list** (15 entries in the
sample), each with an `id`, `type`, `name`, `full_name`, a `base` expression, and
`cost_per_point`:

```json
{ "id": "st",          "type": "integer", "name": "ST", "full_name": "Strength",
  "base": "10", "cost_per_point": 10, "cost_adj_percent_per_sm": 10 }
{ "id": "basic_speed", "type": "decimal", "name": "Basic Speed", "base": "($dx + $ht) / 4", "cost_per_point": 20 }
```

and `body_type` — the hit-location table (`name`, `roll`, `locations[]` with `id`,
`table_name`, `hit_penalty`, `slots`, `dr_bonus`, `description`).

**None of this exists anywhere in the Foundry export.** GGA reads `body_type` on
import to derive `system.hitlocations`, but keeps only `where` / `penalty` /
`roll` / DR. `settings` is therefore a merge-only or default-template concern
(see `docs/05-fidelity.md`).

## 2.4 Row structs

All four row types compose the same way: `SourcedID` + an `EditData` struct +
`third_party` + `children`.

```go
type SourcedID struct {
    TID    tid.TID `json:"id"`
    Source Source  `json:"source,omitzero"`   // {library, path, id} → the library row
}
```

`children` is present **only on containers** (uppercase TID prefix).

### Trait — `trait.go`

```
name, reference, reference_highlight, local_notes, tags[], prereqs,
cr_adj, vtt_notes, userdesc, replacements{}, modifiers[], cr, frequency,
disabled, switched_on, preconfigured,
  (non-container) base_points, points_per_level, max_levels, weapons[],
                  features[], round_down, can_level, levels, study[], study_hours_needed
  (container)     ancestry, template_picker, container_type
```

### Skill / Technique — `skill.go`

```
name, reference, reference_highlight, local_notes, tags[], vtt_notes, replacements{},
  (non-container) specialization, difficulty, encumbrance_penalty_multiplier,
                  defaults[], default, limit, prereqs, weapons[], features[],
                  optional_specialization, tech_level, points, defaulted_from,
                  study[], study_hours_needed
  (container)     template_picker
```

`difficulty` is `"iq/h"` / `"dx/a"` style for skills; a technique carries only the
difficulty letter (`"h"`) plus a `default` object naming the base skill.

### Equipment — `equipment.go`

Watch the key name: the display name is **`description`**, not `name`.

```
description, reference, reference_highlight, local_notes, tech_level,
legality_class, tags[], base_value, base_weight, max_uses, prereqs,
weapons[], features[], ignore_weight_for_skills,
vtt_notes, replacements{}, modifiers[], rated_strength,
quantity (always written), level, uses, equipped
```

`base_value` / `base_weight` are **strings** (`"900"`, `"2.25 lb"`) because they may
carry units or expressions.

### Note — `note.go`

```
markdown, reference, reference_highlight, tags[], replacements{}
```

## 2.5 Weapons are nested, not a flat list

`Weapon` (`weapon.go`) is an array member of a Trait, Skill, Spell, or Equipment:

```go
TID        `json:"id"`   // 'w' melee, 'W' ranged
SubVersion `json:"sv"`
Damage     WeaponDamage    `json:"damage"`   // structured: {type, st, base, ...}
Strength, Usage, UsageNotes
Reach, Parry, Block, Accuracy, Range, RateOfFire, Shots, Bulk, Recoil
Defaults []*SkillDefault `json:"defaults"`
Hide     bool
```

From the sample, the Reflex Bow:

```json
"weapons": [{
  "id": "WRiXTtjhqHZUMjC6w", "sv": 1,
  "damage": { "type": "imp", "st": "thr", "base": "3" },
  "strength": "10†", "usage": "Shoot",
  "accuracy": "3", "range": "x20/x25", "rate_of_fire": "1",
  "shots": "1(2)", "bulk": "-7",
  "defaults": [ {"type":"dx","modifier":-5}, {"type":"skill","name":{"compare":"is","qualifier":"Bow"}} ],
  "calc": { "level": 14, "damage": "1d+1 imp", "range": "200/250" }
}]
```

`damage` is a **structure** (`thr+3 imp`) and `range` is a **formula** (`x20/x25`,
multiples of a ST-derived range). The evaluated forms (`"1d+1 imp"`, `"200/250"`)
live only in `calc`. Foundry stores only the evaluated forms.

## 2.6 `calc` is write-only

Every `MarshalJSONTo` in the model appends a `calc` object of derived values.
Every `UnmarshalJSONFrom` decodes into a struct that **has no `calc` field**, so
the block is silently discarded on load and everything is recomputed.

```go
// trait.go — the marshal side builds it...
type calc struct {
    Points            fxp.Int  `json:"points"`
    UnsatisfiedReason string   `json:"unsatisfied_reason,omitzero"`
    ResolvedNotes     string   `json:"resolved_notes,omitzero"`
    CurrentLevel      *fxp.Int `json:"current_level,omitzero"`
}
// ...and UnmarshalJSONFrom never mentions it.
```

**This is the single biggest simplification for the writer.** We do not have to
reproduce GCS's point maths, damage resolution, skill levelling, or encumbrance
to produce a file GCS accepts — GCS recomputes all of it on open.

The caveat: **GGA's importer refuses a GCS file with no top-level `calc`**
(`if (!r.calc) → "importOldGCSFile"`), and it reads `calc.points`, `calc.level`,
`calc.rsl`, `calc.damage`, `calc.extended_value`, `calc.extended_weight`
throughout. So if our output should also be re-importable into Foundry, we must
emit plausible `calc` blocks even though GCS ignores them. See
`docs/06-architecture.md`.

## 2.7 `third_party`

`EntityData`, `TraitData`, `SkillData`, `EquipmentData`, and `NoteData` each carry
`third_party map[string]any`, round-tripped verbatim by GCS. This is the sanctioned
place to stash converter metadata (source Foundry actor id, converter version, what
we chose not to overwrite) without corrupting the sheet.
