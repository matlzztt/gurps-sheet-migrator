# 3. The Foundry export format

## 3.1 What the file actually is

`samples/sturm/sturm.foundry.json` is Foundry VTT's generic **Export Data** dump of
an `Actor` document. It is not a GGA-specific format — the outer envelope is
Foundry's, and everything the GURPS system owns lives under `system`.

The envelope, for orientation (we read a little of it, write none of it):

| Key | Type | Use to us |
|---|---|---|
| `name` | string | character name — but `system.traits` has no name field, so this **is** the source for `profile.name` |
| `type` | string | must be `"character"` |
| `img` | string | a world-relative path (`worlds/…/pL2Oded3MyENl1xg-img.png`), **not** image bytes — the portrait cannot be recovered from the JSON alone |
| `system` | object | everything below |
| `items` | array | empty in the sample; non-empty only when the world has GGA's "use Foundry items" setting on (see §3.5) |
| `effects` | array | active effects — out of scope |
| `flags` | object | `{ core: { sheetClass: "gurps.GurpsActorSheet" } }` |
| `_stats` | object | `systemId`, `systemVersion` (`0.18.13` in the sample), `coreVersion` (`13.351`), `exportSource.uuid` |
| `prototypeToken`, `ownership`, `folder` | | Foundry bookkeeping, ignored |

Read `_stats.systemVersion` and refuse (or warn) on versions the converter has not
been validated against — GGA's actor schema has changed shape across minor releases.

Encoding: UTF-8, no BOM. Non-ASCII is written raw (`ü` = U+00FC, `†` = U+2020 both
survive intact in the sample).

## 3.2 The `00000` key convention

GGA does not use JSON arrays for its lists. Every collection is an object keyed by
a zero-padded 5-digit decimal string, assigned by `zeroFill()` in
`gurps/lib/utilities.js` and `GURPS.put()` in `gurps/module/gurps.js`:

```json
"skills": { "00000": {...}, "00001": {...}, "00002": {...} }
```

Keys are insertion order and are **not** stable identity — they renumber whenever
rows are inserted or deleted. Use `uuid`, never the key.

## 3.3 Hierarchy: `contains` + `parentuuid`

Nested rows appear **twice over** in GGA's model: each child sits inside its
parent's `contains` object *and* records `parentuuid`. `foldList()` in
`actor-importer.js` builds this from a flat list:

```js
foldList(flat, target = {}) {
  flat.forEach(obj => {
    if (obj.parentuuid) {
      const parent = flat.find(o => o.uuid == obj.parentuuid)
      if (parent) GURPS.put(parent.contains, { ...obj })
      else obj.parentuuid = ''
    }
  })
  let index = 0
  flat.forEach(obj => { if (!obj.parentuuid) GURPS.put(target, { ...obj }, index++) })
  return target
}
```

Only top-level rows appear in the collection root. To enumerate everything you must
recurse into `contains`. In the sample every `contains` is `{}` and every
`parentuuid` is `""` — the character is completely flat — so **container handling is
untested against real data** and is a known risk (see `docs/05-fidelity.md` §5.6).

Do not mistake `equipment.carried` vs `equipment.other` for nesting: they are two
sibling collections mirroring GCS's `equipment` / `other_equipment` top-level lists,
and rows in them are unnested unless they carry a `parentuuid`.

## 3.4 `system` — section by section

Full key list from the sample, grouped by what we do with it.

### Derived attribute block — read-only for us, mostly

```
attributes: { ST, DX, IQ, HT, WILL, PER, QN }   each: { import, value, points, dtype }
HP / FP / QP: { value, min, max, points }
dodge: { value, enc_level }
basicmove: { value, points }        basicspeed: { value, points }
parry, currentmove, currentdodge, thrust, swing
frightcheck, hearing, tastesmell, vision, touch
liftingmoving: { basiclift, onehandedlift, twohandedlift, shove, runningshove,
                 carryonback, shiftslightly }
encumbrance: { "00000".."00004": { key, level, dodge, weight, move, current } }
```

`import` is the value as of the last GCS import; `value` is the live value. Both are
**final computed numbers** — GCS stores `adj` (the point-bought delta) instead, so
inverting requires subtracting the base formula and any trait bonuses (§`docs/04-mapping.md` §4.2).

`liftingmoving` and `encumbrance` are pure functions of Basic Lift and carried
weight — recomputed by GCS, so ignore them entirely.

### Identity

```
traits: { title, race, height, weight, age, birthday, religion, gender,
          eyes, hair, hand, skin, sizemod, techlevel,
          createdon, modifiedon, player, options }
```

Confusingly named: `system.traits` is the **profile**, not the advantage list. The
advantage list is `system.ads`.

`createdon` / `modifiedon` are **localized display strings**, not timestamps —
`importTraitsFromGCS` writes them as
`new Date(cd).toLocaleString('en-US', {dateStyle:'medium', timeStyle:'short'})`,
giving `"Aug 14, 2026, 2:10 PM"`. Second-level precision and the UTC offset are
gone. GCS wants RFC3339.

### The four content lists

```
ads:       { "00000": Advantage, ... }   ← traits (advantages + disadvantages + quirks)
skills:    { "00000": Skill, ... }       ← skills and techniques
spells:    { }                           ← empty in the sample
notes:     { "00000": Note, ... }
equipment: { carried: {...}, other: {...} }
```

Common fields on all four (the `actor-components.js` base class):

```
uuid, parentuuid, contains{}, name, originalName, notes, pageref,
save, itemid, itemInfo{}, fromItem, addToQuickRoll, itemModifiers, modifierTags
```

Type-specific additions:

| List | Extra fields |
|---|---|
| `ads` | `points` (string), `level`, `cr`, `note` |
| `skills` | `points` (string), `import`, `level`, `type` (`SKILL`/`TECHNIQUE`), `relativelevel`, `consumeAction` |
| `notes` | `title` |
| `equipment.*` | `equipped`, `carried`, `count`, `cost`, `weight`, `costsum`, `weightsum`, `location`, `techlevel`, `legalityclass`, `categories`, `uses`, `maxuses`, `originalCount`, `ignoreImportQty`, `collapsed{}` |

`save: true` marks a row the user added inside Foundry that should survive a
re-import from GCS. For our direction that flag identifies rows with **no GCS
counterpart** — they need freshly minted TIDs.

### Flattened weapons

```
melee:  { "00000": { name, mode, import, damage, reach, parry, block, st,
                     weight, cost, techlevel, notes, pageref, baseParryPenalty,
                     extraAttacks, consumeAction, ... } }
ranged: { "00000": { name, mode, import, damage, acc, range, rof, shots, rcl,
                     bulk, st, ammo, legalityclass, halfd, max, ... } }
```

**These are detached from their owners.** `importCombatFromGCS` walks every trait,
skill, spell and equipment row, pulls each nested `weapons[]` entry out, copies the
*owner's* name/notes/pageref/weight/cost onto it, and drops it into a flat list. The
weapon's own GCS TID is never written. Re-attachment is therefore a
`(owner name, usage)` match — see `docs/04-mapping.md` §4.6.

Everything stored here is the **evaluated** form: `damage: "1d+1 imp"` (from
`w.calc.damage`), `range: "200/250"` (from `w.calc.range`, not the `x20/x25`
formula), `import: "14"` (from `w.calc.level`), `parry` / `block` as resolved
strings.

### Derived / recomputable — ignore on the way back

```
hitlocations: { "00000": { where, import, equipment, penalty, roll, dr, drItem, drMod, drCap } }
reactions:    { "00000": { modifier, situation, modifierTags } }
conditionalmods: same shape
totalpoints:  { attributes, ads, disads, quirks, skills, spells, total, unspent, race }
conditions, conditionalinjury, currentdodge, parry
```

`hitlocations` is a lossy projection of `settings.body_type` (`_getBodyPlan()`
reverse-matches it to a named plan like `"humanoid"`). `reactions` and
`conditionalmods` are aggregated from trait `features[]`. `totalpoints` is a sum.
GCS recomputes every one of these — writing them back would be wrong, not merely
redundant.

Two are exceptions worth reading anyway: `hitlocations[].dr` and `drMod` carry
**user-entered** DR overrides that have no GCS equivalent. And `totalpoints.total`
is a useful cross-check on the merge (see §5).

### Bookkeeping

```
lastImport: "Aug 26 2026 20:13:18"
additionalresources: { bodyplan, tracker{}, importname }
backupItemInfo: {}
```

`additionalresources.importname` is **`"Stürm.gcs"`** in the sample — the filename
of the GCS file this actor was imported from. The merge mode should use it to
propose the base file.

## 3.5 The "Foundry items" mode

GGA has a setting (`SETTING_USE_FOUNDRY_ITEMS`) that mirrors traits/skills/spells/
equipment into real Foundry `Item` documents, referenced from the actor rows by
`itemid` and populated in `itemInfo`. In that mode the top-level `items` array is
populated and some data lives there instead of in `system`.

The sample has `items: []` and every `itemid` empty, so this project targets the
**non-item** mode first. Supporting item mode means additionally reading
`items[].system` — flag it as unsupported rather than silently dropping data.
