# 1. The problem

## Goal

Take the JSON that Foundry VTT emits when you export a GURPS actor, and produce
a `.gcs` file that GCS (GURPS Character Sheet) will open.

## The direction that already exists

The Foundry system **GURPS Game Aid** (GGA, `crnormand/gurps`) can already read a
`.gcs` file and build a Foundry actor from it. That code lives in
`gurps/module/actor/actor-importer.js`, in the `*FromGCS` family of methods:

```
importActorFromGCS(json, ...)
  ├─ importAttributesFromGCS(r.attributes, r.equipment, r.calc)
  ├─ importTraitsFromGCS(r.profile, r.created_date, r.modified_date)
  ├─ importSizeFromGCS(...)
  ├─ importAdsFromGCS(r.traits)          → importAd()   (recursive)
  ├─ importSkillsFromGCS(r.skills)       → importSk()   (recursive)
  ├─ importSpellsFromGCS(r.spells)       → importSp()   (recursive)
  ├─ importEquipmentFromGCS(r.equipment, r.other_equipment) → importEq()
  ├─ importNotesFromGCS(r.notes)         → importNote() (recursive)
  ├─ importProtectionFromGCS(r.settings.body_type)
  ├─ importPointTotalsFromGCS(...)
  ├─ importReactionsFromGCS(...)
  └─ importCombatFromGCS(...)            → system.melee / system.ranged
```

There is **no** export in the other direction. `grep -rl gcs` over `gurps/module`
and `gurps/lib` turns up only importer, sheet, and settings code. That gap is
what this project fills.

## The one fact that shapes the whole design

**GCS object IDs survive the round trip.** GCS gives every row a TID (a one-char
kind prefix plus 16 base64url chars). GGA's importer copies it verbatim into the
Foundry row's `uuid` field (`a.uuid = i.id`, `s.uuid = i.id`, `e.uuid = i.id`,
`n.uuid = i.id`), and Foundry's export writes it back out.

Measured on the sample pair:

| Collection | GCS rows | Foundry rows | Matching IDs |
|---|---|---|---|
| Traits / `ads` | 22 | 22 | 22 |
| Skills | 24 | 21 | 21 (3 skills were added in GCS after the export) |
| Equipment (carried + other) | 26 | 26 | 26 |

So a Foundry row can be matched back to the exact GCS row it came from, with no
name matching and no heuristics.

## The other fact that shapes the whole design

**The forward conversion is heavily lossy.** GGA does not carry GCS's structured
data across; it flattens GCS's *computed* output into display strings. A worked
example from the sample — the trait `Good Reputation`:

GCS holds the inputs:

```json
{
  "id": "t89rhDVCsi9fR6yJu",
  "name": "Good Reputation",
  "tags": ["Advantage", "Social"],
  "points_per_level": 5,
  "levels": 3,
  "modifiers": [ /* 7 of them, 6 disabled, one "x1/3" active */ ],
  "features": [ { "type": "reaction_bonus", "amount": 1, "situation": "..." } ],
  "source": { "library": "richardwilkes/gcs_master_library", "path": "...", "id": "..." }
}
```

Foundry keeps the outputs:

```json
{
  "uuid": "t89rhDVCsi9fR6yJu",
  "name": "Good Reputation 3",
  "originalName": "Good Reputation",
  "level": 3,
  "points": "5",
  "notes": "...B26-28; People Affected"
}
```

Seven modifiers collapsed into the four-word suffix `"; People Affected"`.
`points_per_level`, `features`, `tags`, `source`, and the enabled/disabled state
of each modifier are simply gone. `points: "5"` is `3 × 5 × 1/3` already
evaluated — you cannot recover `points_per_level` from it.

## Consequence: two modes, not one

A pure Foundry-JSON → GCS transform **cannot** reconstruct a faithful sheet. It
can only produce a plausible one. So the tool needs two modes:

### Mode A — Merge (the primary mode)

Inputs: the Foundry export **and** the original `.gcs` file.
Take the GCS file as the base, walk it by TID, and write back only the fields
Foundry is authoritative for (see `docs/04-mapping.md`). Everything Foundry
never knew about — modifiers, features, prereqs, defaults, difficulty, tags,
library `source` links, attribute definitions, body plan, page settings — is
preserved untouched because it was never overwritten.

This is high fidelity and is the mode that matches the real workflow: build in
GCS, play in Foundry, pull the session's changes back into GCS.

Locating the base file is partly automatic:
`system.additionalresources.importname` records the original GCS filename
(`"Stürm.gcs"` in the sample).

### Mode B — Synthesize (fallback)

Input: the Foundry export alone.
Emit a structurally valid GCS v5 file with default settings (GCS ships defaults
at `gcs/model/gurps/embedded_data/Standard.attr` and `Humanoid.body`), fresh
TIDs where Foundry has none, and everything GCS derives left for GCS to compute.
Useful for actors that were never in GCS (GCA imports, hand-built NPCs), and
honest about being lower fidelity.

## Scope note

The Foundry export used here is Foundry's own generic **Export Data** dump of an
Actor document — not a GGA-specific format. That means the envelope
(`name`, `img`, `type`, `flags`, `_stats`, `prototypeToken`, `ownership`,
`items`, `effects`) is Foundry's, and everything we care about lives under
`system`. See `docs/03-foundry-format.md`.
