# 4. Field mapping — Foundry → GCS

Notation for the **Fidelity** column:

| | |
|---|---|
| ✅ | exact — the value round-trips unchanged |
| ⚙️ | derivable — needs a computation, but deterministic and exact |
| 🔶 | lossy — a best-effort reconstruction; prefer the base file's value in merge mode |
| ❌ | unrecoverable from the Foundry export — merge mode only |
| 🗑️ | do not write — GCS recomputes it (see `docs/02-gcs-format.md` §2.6) |

## 4.1 Top level

| GCS | Foundry source | Fidelity | Notes |
|---|---|---|---|
| `version` | — | ✅ | literal `5` |
| `id` | — | ❌ | keep the base file's; else mint a fresh `A`-TID |
| `total_points` | `system.totalpoints.total` | ⚙️ | see §4.7 |
| `points_record[]` | — | ❌ | GCS-only audit log; preserve from base |
| `profile` | `system.traits` + `name` + `img` | 🔶 | §4.3 |
| `settings` | — | ❌ | attribute defs, body plan, page setup. Preserve, or fall back to GCS's embedded defaults (`gcs/model/gurps/embedded_data/Standard.attr`, `Humanoid.body`) |
| `attributes` | `system.attributes` etc. | ⚙️ | §4.2 |
| `traits` | `system.ads` | 🔶 | §4.4 |
| `skills` | `system.skills` | 🔶 | §4.5 |
| `spells` | `system.spells` | 🔶 | same shape as skills; empty in the sample, so **unvalidated** |
| `equipment` | `system.equipment.carried` | 🔶 | §4.5 |
| `other_equipment` | `system.equipment.other` | 🔶 | §4.5 |
| `notes` | `system.notes` | ✅ | `markdown` ← `notes`, `reference` ← `pageref` |
| `created_date` | `system.traits.createdon` | 🔶 | display string; loses seconds and offset. Prefer base |
| `modified_date` | `system.traits.modifiedon` or `_stats.modifiedTime` | ⚙️ | `_stats.modifiedTime` is epoch ms and is the better source |
| `calc` | — | 🗑️ | but emit for GGA re-import — see `docs/06-architecture.md` |

## 4.2 Attributes

Foundry stores **final values**; GCS stores **`adj`**, the point-bought delta above
the computed base. Two independent inversion routes exist, and they should agree:

**Route A — from points (preferred).** From `attribute_def.go`:

```go
cost := value.Mul(a.CostPerPoint)          // value here is the *adjustment*
// then reduced by SM discount (only when sizeModifier > 0 && CostAdjPercentPerSM > 0)
// and by any CostReduction features, capped at 80%
```

so `adj = points / cost_per_point`, exact whenever SM ≤ 0 and no cost-reduction
feature applies. Verified against the sample:

| Attribute | Foundry `points` | `cost_per_point` | `adj` | GCS `adj` |
|---|---|---|---|---|
| ST | 0 | 10 | 0 | 0 ✓ |
| DX | 40 | 20 | 2 | 2 ✓ |
| IQ | 80 | 20 | 4 | 4 ✓ |
| HT | 10 | 10 | 1 | 1 ✓ |
| PER | 5 | 5 | 1 | 1 ✓ |
| basic_speed | 5 | 20 | 0.25 | 0.25 ✓ |

Route A is robust to trait-granted attribute bonuses, which change `value` but not
`points`.

**Route B — from value.** `adj = value − eval(base) − bonuses`. Requires evaluating
the `base` expression from `settings.attributes[]` (`"$iq"`, `"($dx + $ht) / 4"`,
`"Math.floor($basic_speed)"`) and summing every `attribute_bonus` feature on every
enabled trait. Use it only as a cross-check, or when `settings` is unavailable.

Field sources:

| GCS `attr_id` | Foundry | Fidelity |
|---|---|---|
| `st` `dx` `iq` `ht` `will` `per` | `system.attributes.{ST,DX,IQ,HT,WILL,PER}.points` | ⚙️ |
| `qn` | `system.attributes.QN.points` | ⚙️ |
| `hp` | `system.HP.points`; `calc.current` ← `system.HP.value` | ⚙️ |
| `fp` | `system.FP.points`; `calc.current` ← `system.FP.value` | ⚙️ |
| `qp` | `system.QP.points` / `.value` | ⚙️ |
| `basic_speed` | `system.basicspeed.points` | ⚙️ |
| `basic_move` | `system.basicmove.points` | ⚙️ |
| `fright_check` | `system.frightcheck` (value only, no points field) | 🔶 |
| `vision` `hearing` `taste_smell` `touch` | `system.{vision,hearing,tastesmell,touch}` (value only) | 🔶 |
| `damage` (per-attribute) | `system.HP.value` vs `max` | ⚙️ | current damage = max − value |

The 🔶 rows have no Foundry `points` field at all, so Route A is unavailable and
Route B is the only option. In merge mode, prefer the base file's `adj` and only
override when the Foundry value differs from what the base file computes.

**Type hazard.** Foundry is inconsistent about number types: in the sample
`ST.import` is the integer `10` but `HT.import` is the string `"11"`. Coerce every
numeric read.

**Never write** `system.liftingmoving`, `system.encumbrance`, `system.thrust`,
`system.swing`, `system.currentmove`, `system.currentdodge`, `system.dodge`,
`system.parry` — all derived. 🗑️

## 4.3 Profile

| GCS `profile` | Foundry | Fidelity |
|---|---|---|
| `name` | top-level `name` | ✅ |
| `age` | `system.traits.age` | ✅ |
| `birthday` | `system.traits.birthday` | ✅ |
| `eyes` `hair` `skin` `gender` | `system.traits.{eyes,hair,skin,gender}` | ✅ |
| `handedness` | `system.traits.hand` | ✅ |
| `height` | `system.traits.height` | ✅ (`"5'8.8\""`) |
| `weight` | `system.traits.weight` | ✅ (`"140 lb"`) |
| `player_name` | `system.traits.player` | ✅ |
| `title` | `system.traits.title` | ✅ |
| `religion` | `system.traits.religion` | ✅ |
| `tech_level` | `system.traits.techlevel` | ✅ |
| `SM` | `system.traits.sizemod` (`"+0"` → `0`; omit when zero) | ⚙️ |
| `organization` | — | ❌ **no Foundry field exists** (`"Yarga"` in the sample GCS, absent from the export) |
| `portrait` | — | ❌ `img` is a world-relative path, not bytes. Base64 image data cannot come from the JSON |

Unused on the Foundry side: `system.traits.race` (GCS models ancestry as a trait
container) and `system.traits.options` (empty).

## 4.4 Traits ← `system.ads`

| GCS | Foundry | Fidelity |
|---|---|---|
| `id` | `uuid` | ✅ — the anchor for the whole merge |
| `name` | `originalName` | ✅ — **not** `name`; `name` has the level appended (`"Good Reputation 3"`). But see §4.9: a player *rename* also lands in `name`, so the two fields are not interchangeable |
| `levels` | `level` | ✅ |
| `reference` | `pageref` | ✅ |
| `local_notes` | `notes` | 🔶 — see below |
| `cr` | `cr` | ✅ |
| `children[]` | `contains{}` | ✅ |
| `base_points` / `points_per_level` | `points` | 🔶 — `points` is `calc.points`, the **evaluated total** after levels and modifiers. Not invertible |
| `modifiers[]` | — | ❌ |
| `features[]` | — | ❌ (`system.reactions` / `system.conditionalmods` are aggregated projections, not per-trait) |
| `tags[]` | — | ❌ |
| `source` | — | ❌ |
| `prereqs`, `cr_adj`, `frequency`, `disabled`, `can_level`, `round_down`, `max_levels`, `study[]` | — | ❌ |
| `userdesc` | folded into `notes` | ❌ |
| `vtt_notes` | GGA's OtF migration keeps this in `notes`; see `_migrateOtfsAndNotes` | 🔶 |

**Why `local_notes` is 🔶.** `importAd` builds the Foundry `notes` string by
concatenation:

```js
a.notes = this._resolveNotes(i)                       // calc.resolved_notes ?? notes ?? local_notes
if (i.cr) a.notes = '[' + CR label + ': ' + a.name + ']'
for (let j of i.modifiers) if (!j.disabled)
    a.notes += `${a.notes ? '; ' : ''}${j.name}${j.notes ? ' (' + j.notes + ')' : ''}`
if (a.userdesc) a.notes += (a.notes ? '\n' : '') + a.userdesc
```

Three separate GCS fields plus every enabled modifier's name are glued into one
string. Writing it straight back into `local_notes` **duplicates the modifier names
into the notes on every round trip**, and they accumulate. In merge mode: keep the
base file's `local_notes` unless the user actually edited the note in Foundry, and
detect that by comparing against the string GGA *would* have produced from the base.

Also: `_resolveNotes` prefers `calc.resolved_notes`, which is the note with
`@nameable@` placeholders already substituted. Writing that back destroys the
template and desynchronizes `replacements{}`.

## 4.5 Skills, techniques, equipment

### Skills ← `system.skills`

| GCS | Foundry | Fidelity |
|---|---|---|
| `id` | `uuid` | ✅ |
| `points` | `points` | ✅ — `importSk` assigns `s.points = i.points`, the raw input, **not** `calc` |
| `reference` | `pageref` | ✅ |
| `local_notes` | `notes` | 🔶 (no modifier concatenation for skills, so cleaner than traits) |
| `name` + `specialization` + `tech_level` | `name` | ⚙️ — composed as `` `${name}${tech_level ? '/TL'+tl : ''}${spec ? ' ('+spec+')' : ''}` ``; split it back on the trailing parenthesized group and `/TL` |
| `difficulty` | `relativelevel` | 🔶 — `relativelevel` is `calc.rsl` (`"IQ+1"`), which gives the controlling attribute but not the difficulty letter |
| `defaults[]`, `default`, `limit`, `prereqs`, `features[]`, `tags[]`, `source`, `defaulted_from`, `encumbrance_penalty_multiplier` | — | ❌ |
| — | `import` | 🗑️ (that is `calc.level`) |
| — | `level` | 🗑️ — empty right after import, filled in by GGA later. Verified derived in `docs/05-fidelity.md` §5.7 |

Verified on the sample: every `points` value matches exactly across all 21 shared
skills, and every composed name decomposes cleanly —
`"Esoteric Medicine (Menkhu)"` → `name: "Esoteric Medicine"`, `specialization: "Menkhu"`.

**Techniques** (`type: "TECHNIQUE"`, `q`-prefixed TID) are worse. `importSk`
appends the resolved base skill in a second parenthesized group, and in the sample
it does so **buggily**:

```
GCS   name: "Targeted Attack (@Skill@ @Attack@/Vitals)"   (nameable template)
FVTT  name: "Targeted Attack (Spear Thrust/Vitals) ([object Object])"
```

The `[object Object]` is `i.default.specialization` — an object `{compare, qualifier}`
that GGA stringifies without unwrapping. Any technique name coming back from
Foundry must have a trailing `([object Object])` or `(BaseSkill (Spec))` group
stripped, and the `@nameable@` template can only be restored from the base file.

### Equipment ← `system.equipment.carried` / `.other`

| GCS | Foundry | Fidelity |
|---|---|---|
| `id` | `uuid` | ✅ |
| `description` | `originalName` (fall back to `name`) | ✅ |
| `quantity` | `count` | ✅ |
| `equipped` | `equipped` | ✅ |
| carried vs other | which collection the row is in | ✅ |
| `reference` | `pageref` | ✅ |
| `tech_level` | `techlevel` | ✅ |
| `legality_class` | `legalityclass` | ✅ |
| `tags[]` | `categories` (comma-joined) | ⚙️ — `categories.split(', ')` |
| `uses` / `max_uses` | `uses` / `maxuses` | ✅ |
| `children[]` | `contains{}` | ✅ |
| `base_value` | `cost` | 🔶 — `importEq` computes `cost = calc.extended_value / quantity`, i.e. **after** modifiers. Round-tripping it bakes the modifier in permanently |
| `base_weight` | `weight` | 🔶 — same, from `calc.extended_weight`; also loses the unit suffix (`"2.25 lb"` → `"2.25"`) |
| `local_notes` | `notes` | 🔶 — modifier names concatenated, same as traits |
| `modifiers[]`, `features[]`, `prereqs`, `rated_strength`, `source` | — | ❌ |

**Container arithmetic hazard.** `importEq` subtracts children's cost and weight
from their parent:

```js
for (let j of ch) { e.cost -= j.cost * j.count; e.weight -= j.weight * j.count }
```

so a container's Foundry `cost` is its *own* cost, not the extended total. Do not
re-add children when inverting. `costsum` / `weightsum` are the extended totals and
are 🗑️.

**`ignoreImportQty`.** When set, the user has pinned `count`/`uses`/`maxuses` in
Foundry against GCS re-imports. Treat it as an explicit signal that Foundry is
authoritative for those three fields.

### Notes ← `system.notes`

| GCS | Foundry | Fidelity |
|---|---|---|
| `id` | `uuid` | ✅ |
| `markdown` | `notes` | ✅ |
| `reference` | `pageref` | ✅ |
| `tags[]` | — | ❌ |

## 4.6 Weapons — the re-attachment problem

`system.melee` and `system.ranged` are flat lists with no owner link and no weapon
TID. Reconstructing `weapons[]` inside the right trait/skill/equipment row requires
matching on `(owner name, usage)`:

- Foundry `melee[].name` ← the **owner's** `name`/`description`
- Foundry `melee[].mode` ← the weapon's `usage`

GGA itself matches this way (`_findElementIn('melee', false, m.name, m.mode)`), so
the ambiguity is pre-existing: two weapons on the same owner with the same usage
are indistinguishable.

Sample counts line up exactly — 8 melee (5 on equipment, 3 on traits) and 3 ranged
(all equipment) on both sides — so the match is clean here.

Even with correct attachment, the payload is evaluated-only:

| GCS weapon field | Foundry | Fidelity |
|---|---|---|
| `id` | — | ❌ mint a `w`/`W` TID, or take it from the base file |
| `usage` | `mode` | ✅ |
| `strength` | `st` | ✅ (`"10†"` survives) |
| `reach` / `accuracy` / `bulk` / `rate_of_fire` / `shots` / `recoil` | `reach` / `acc` / `bulk` / `rof` / `shots` / `rcl` | ✅ raw strings |
| `damage` | `damage` | ❌ Foundry has `"1d+1 imp"` (`calc.damage`); GCS wants `{type:"imp", st:"thr", base:"3"}` |
| `range` | `range` | ❌ Foundry has `"200/250"` (`calc.range`); GCS wants the formula `"x20/x25"` |
| `parry` / `block` | `parry` / `block` | 🔶 resolved strings |
| `defaults[]` | — | ❌ |
| `usage_notes` | folded into `notes` | ❌ |

`acc` has an extra wrinkle: `importCombatFromGCS` splits `"3+2"` into `acc: "3"`
plus a `" [+2 acc]"` suffix appended to `notes`. Reverse that before writing.

**Recommendation: in merge mode, do not write `weapons[]` at all.** Nothing Foundry
holds about a weapon is an input GCS accepts, and everything it holds GCS will
recompute. The only exception worth considering is a weapon the user *added* in
Foundry, which has no base-file counterpart.

## 4.7 Points

| GCS | Foundry | Fidelity |
|---|---|---|
| `total_points` | `system.totalpoints.total` | ⚙️ |
| `points_record[]` | — | ❌ |

In the sample `total_points` is `209` and `system.totalpoints.total` is `206` — the
3-point "Session award" in `points_record` was added in GCS after the export. That
is the expected shape of the discrepancy, and a good merge-mode assertion: after
merging, GCS's recomputed spend should equal `system.totalpoints` minus `unspent`.

`system.totalpoints.{attributes, ads, disads, quirks, skills, spells, unspent, race}`
are all 🗑️ — GCS recomputes them. So are `equippedparry` and `equippedblock`,
which are `null` until GGA computes them (`docs/05-fidelity.md` §5.7).

## 4.9 Renames, and telling them from decoration

`originalName` is written once at import and never updated; `name` carries both
GGA's decoration *and* any rename the player makes. Confirmed against
`samples/container/`: renaming an item changed `name` alone.

So for any row, `name` differing from `originalName` means one of two things:

| Pattern | Meaning | Action |
|---|---|---|
| `name == originalName + " " + str(levels)` | trait level decoration | ignore |
| `name == originalName + " (" + base_skill + ")"` (or `([object Object])`) | technique decoration | ignore |
| anything else | **a real rename** | carry back to `name` / `description` |

Match the decoration patterns explicitly and treat everything else as a rename;
the reverse — trying to enumerate rename shapes — cannot work.

Equipment has no decoration at all, so there `name != originalName` is always a
rename.

## 4.10 `equipped` cascades

Un-equipping a container in Foundry clears `equipped` on every descendant
(`docs/05-fidelity.md` §5.7): one action, four changed rows in the export.

Writing each row's `equipped` back independently is *correct* — GCS stores the
flag per row too — but the change **report** should collapse a cascade into the
action that caused it, or a player who un-equipped one backpack will be shown
four unexplained edits and lose confidence in the diff.

## 4.8 Everything Foundry adds that GCS has no home for

These are genuinely Foundry-side concepts. Stash them in `third_party` if they
matter, or drop them.

`system.conditions` (posture, maxActions, user modifiers) · `system.conditionalinjury` ·
`system.additionalresources.tracker` (resource trackers) · `system.hitlocations[].dr`,
`.drMod`, `.drCap` (manual DR overrides) · `addToQuickRoll` · `modifierTags` ·
`consumeAction` · `extraAttacks` · On-the-Fly formulas embedded in note fields ·
`effects[]` (Foundry active effects) · `system.melee[].baseParryPenalty`.
