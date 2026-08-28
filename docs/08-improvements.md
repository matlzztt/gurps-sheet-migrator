# 8. Improvements — what is known to be worth doing

Everything here is a real, identified gap, not speculation. Each entry says what
is wrong, how it was found, and what fixing it would take. Ordered by value.

Nothing here is a known *defect* in shipped behaviour: the suite is green and
GCS accepts both modes' output. These are places where the tool does less than
it could, or knows less than it should.

Last updated: 2026-08-28.

## 8.1 Drive `_add_row` from the field policy — the one big one

`fields.RULES` already encodes the whole Foundry→GCS field mapping, per
section, as data. `apply._add_row` ignores it and hand-writes a much smaller
subset. They have diverged:

| section | `fields.RULES` knows | `_add_row` writes |
|---|---|---|
| equipment | `description`, `quantity`, `equipped`, `reference`, `tech_level`, `legality_class`, `uses`, `max_uses`, `base_value`, `base_weight`, `local_notes` | `description`, `quantity`, `equipped`, `reference`, `local_notes` |
| traits | `name`, `levels`, `reference`, `local_notes` | `name`, `base_points`, `reference`, `local_notes` |
| skills | `name`, `points`, `reference`, `local_notes` | `name`, `difficulty`, `points`, `reference`, `local_notes` |

So a synthesized sheet silently drops every item's **weight, value, legality
class, tech level and uses** — all of which Foundry has and the policy already
knows how to read. This is invisible in merge mode, where those fields come
from the base sheet, which is why it survived this long.

Fixing it is mostly deletion: iterate `RULES[section]`, call `rule.read(row,
{})`, skip zero values, and keep only the handful of things the policy does not
cover (`id`, `base_points` vs `points`, the difficulty guess). Do it with the
oracle in hand — GCS's rewrite is what will say whether each new field is
right, exactly as it did for the four defects in §8.4.

## 8.2 Composed names are not decomposed

GGA composes several GCS fields into one Foundry `name` on import.
`docs/04-mapping.md` §4 documents the composition and says how to reverse it.
Synthesize mode writes the composed string straight into `name`:

| written now | should be |
|---|---|
| `"Esoteric Medicine (Menkhu)"` | `name: "Esoteric Medicine"`, `specialization: "Menkhu"` |
| `"Survival (Swampland)"` | `name: "Survival"`, `specialization: "Swampland"` |
| `"Good Reputation 3"` | `name: "Good Reputation"`, `levels: 3`, `can_level: true` |

The rule is in `docs/04-mapping.md` §4.9 and it is one-directional for a
reason: match the decoration patterns explicitly and treat everything else as a
rename, never the reverse. `fields.expected_notes` and `Row.gcs_name` already
do the equivalent reasoning for notes and matching, so the shape to copy
exists.

Merge mode is unaffected — it compares names, it does not build them.

## 8.3 Techniques

Three separate problems, one row type:

- **GGA mangles the name.** `"Targeted Attack (Spear Thrust/Vitals)
  ([object Object])"` — the second parenthesized group is GGA's own bug,
  documented in `docs/04-mapping.md`. It goes into the sheet verbatim.
- **No `default` block.** A GCS technique is defined relative to a base skill,
  in `TechniqueDefault`. Foundry's `relativelevel` for a technique is a bare
  number (`"-1"`) with the base skill only present inside the mangled name.
- **Difficulty is a guess.** `a`, which is what GCS's own `NewTechnique` sets.
  Only Average and Hard are valid for a technique, so the guess is a coin flip
  rather than a fabrication, but it is still a guess.

The TID is preserved and GCS accepts the row, so this is lossy, not broken.

## 8.4 What the oracle would find next

Diffing our output against GCS's rewrite of it is the highest-yield technique
in this project — it found four real defects the moment synthesize mode gave it
new rows to look at (trait `points` vs `base_points`, profile key order, a
written zero, and re-minted technique TIDs). It has only ever been pointed at
one character.

Point it at more. Every `.gcs` in `gcs/model/gurps/testdata/` is a free
fixture, and `test_gcs_rewrites_the_fixtures_unchanged_apart_from_calc` already
parametrizes over a list — extending that list costs nothing and each new file
is a new chance to disagree.

## 8.5 Weapons are dropped entirely

`actor.melee()` and `actor.ranged()` are parsed, exposed, counted by `inspect`
— and never written. In GCS a weapon belongs to the trait, skill or equipment
row that grants it (`weapons` on the row); in Foundry the two lists are flat and
detached from their owners, which is why `docs/04-mapping.md` §4.6 marks them
unrecoverable.

For **merge** mode that is correct and should stay that way: the base sheet has
the real weapons, attached to the right rows. For **synthesize** mode they are
simply lost, and a synthesized fighter has no attacks at all. Re-attaching by
name match is possible and would be honestly lossy; worth doing only if someone
actually wants mode B for a combat-ready NPC.

## 8.6 A row nested inside a Foundry-created container loses its parent

`foundry._build_rows` passes the parent's `uuid` down as `parent_tid`, and sets
that uuid to `None` when it is not a valid TID. A child of a container created
inside Foundry therefore arrives with no parent and lands at the top level of
its section.

Fixing it needs the raw GGA id (`_getGGAId` in GGA's `actor-sheet.js`) threaded
through as a second linkage key, and `apply` mapping old id → minted TID as it
creates rows. **It also needs a real fixture** — no GGA-minted id has ever been
seen by this project, and guessing its shape is how you write code that is
confidently wrong.

## 8.7 Spells are unwritten code

Every fixture has `spells: {}`. The policy entries in `fields.py` have never
executed, and `_add_row` has a spells branch that has never run. Treat them as
unwritten, not as working. The user has no casters, so this is correctly last.

## 8.8 The GUI is deliberately thin

`gui.py` builds a command line and runs it. That is the right shape and should
stay, but it does mean the window inherits the CLI's assumptions:

- **No progress while GCS runs.** `--refresh-calc` and `--verify` shell out and
  can take seconds; the window disables its buttons and says "Working…" but
  cannot say more, because it is capturing a stream it only reads at the end.
  Line-by-line streaming would need `cli` to take an output callback.
- **No drag and drop.** Tkinter has none natively, and the export is the one
  file a user always has in hand. `tkinterdnd2` would do it at the cost of the
  project's first runtime dependency.
- **The report is plain text in a Text widget.** The blocked/lossy distinction
  that matters most is carried by indentation. Tags and colour would cost
  little.
- **No settings are remembered** between runs — the GCS path in particular is
  re-detected every launch.

## 8.9 Smaller things

- **`plan()` and `apply()` decide the same things twice.** `plan` re-derives
  what `apply` will do, and the two have already drifted once (moves had to be
  added to both). Better: `apply` builds the plan and a separate step writes
  it, so a dry run is the same code with the write skipped.
- **`--deletions ask`** is in `docs/06-architecture.md` §6.3 and was never
  built; only `keep` and `drop` exist.
- **`--report report.md`** — a per-field record of what was written, skipped
  and why. Listed in §6.7 as worth having early. `report.py` already renders
  everything needed.
- **Synthesize writes no `points_record`.** GCS appends a "Reconciliation"
  entry on first open (see the handoff). Seeding one with the export's total
  and an honest reason would be tidier, and would make the output a fixed point
  immediately.
- **`_diff_attributes` skips the SM discount case entirely** rather than
  handling it — `cost_adj_percent_per_sm` with positive SM makes the
  `points / cost_per_point` inversion inexact, so it declines. Correct and
  honest; solvable with the actual GURPS formula if it ever matters.

## 8.10 Still the highest-value thing available

Unchanged from `docs/07-handoff.md`: **use the tool on a real play session.**
The whole suite validates against one fixture pair whose edits and assertions
were chosen by the same author. An attribute *point* change, a skill raised
with earned points, and a row genuinely added through the Foundry UI have none
of them ever been seen. Everything in this file is a known unknown; a real
session is where the unknown unknowns are.
