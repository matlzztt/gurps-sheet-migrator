# 8. Improvements — what is known to be worth doing

Everything here is a real, identified gap, not speculation. Each entry says what
is wrong, how it was found, and what fixing it would take. Ordered by value.

Nothing here is a known *defect* in shipped behaviour: the suite is green and
GCS accepts both modes' output. These are places where the tool does less than
it could, or knows less than it should.

Last updated: 2026-09-02.

## 8.1 Drive `_add_row` from the field policy — the one big one

**Done 2026-09-02.** `apply._add_row` now iterates `fields.RULES[section]` for
everything but `id`, `name`/`description` (the rule needs a base row to detect
a rename against, and a fresh row has none), `quantity` (its "default to 1
when absent" behaviour isn't part of the field policy), and traits'
`points` → `base_points` rename. A synthesized sheet now carries equipment's
`tech_level`, `legality_class`, `uses`, `max_uses`, `base_value` and
`base_weight`, and traits' `levels`.

The oracle (§8.4) found a real defect the moment it had `levels` to look at:
GCS's `trait.go` forces `can_level = true` on load whenever a non-container
trait has nonzero `levels`, and we were not writing it. Fixed alongside —
`_add_row` now sets it whenever it writes a nonzero `levels`.

**One follow-on, found on 2026-09-02 by auditing the output against the real
sheet** (`docs/05-fidelity.md` §5.10). A second candidate — that leveled traits
should be written with `points_per_level` rather than a flat `base_points` —
was investigated and **rejected**: it is not recoverable, and what we write is
already the best available answer. §5.10 records why, so nobody re-opens it.

### 8.1a `gga_default` suppression is merge-mode reasoning applied to mode B

`_add_row` skips any field whose value equals `Rule.gga_default`. In merge mode
that rule is right and necessary (§5.3): writing GGA's fabricated default back
would add a field the base sheet never had, dirtying every round trip. In
synthesize mode there is no base sheet to protect, so the same rule just
discards what Foundry reported.

Measured effect: 23 equipment rows lose `legality_class: "4"`, which the real
sheet records explicitly. Harmless — LC 4 is the GURPS default and GCS reads an
absent one the same way — but the reasoning is wrong for the mode, and the next
field to acquire a `gga_default` may not be harmless. `legality_class` is the
only one that currently changes the outcome; `tech_level`, `uses` and
`max_uses` would be dropped as zero anyway.

The fix is a flag on the loop rather than a change to the policy: honour
`gga_default` when there is a base row, ignore it when synthesizing.

## 8.2 Composed names are not decomposed

**Done 2026-09-02, for traits and skills** (not techniques — see §8.3, still
open). GGA composes several GCS fields into one Foundry `name` on import;
`docs/04-mapping.md` §4 documents the composition and how to reverse it.
Synthesize mode used to write the composed string straight into `name`:

| written now | before |
|---|---|
| `name: "Esoteric Medicine"`, `specialization: "Menkhu"` | `"Esoteric Medicine (Menkhu)"` |
| `name: "Survival"`, `specialization: "Swampland"` | `"Survival (Swampland)"` |
| `name: "Good Reputation"`, `levels: 3`, `can_level: true` | `"Good Reputation 3"` |

`fields.decompose_skill_name` splits the trailing ` (specialization)` group and
then a `/TL<n>` suffix off a skill's name, in that order — the order GGA's
`importSk` appends them in. It is only trusted when the row was not renamed in
Foundry (`row.display_name == row.gcs_name`); a genuine rename is written
as-is rather than guessed at, per §4.9. Traits reuse the existing rename check
(`fields._read_name` / `expected_display_name`) directly, fed a fabricated base
row carrying the level and the undecorated `originalName` — no new logic
needed there, since `originalName` never carried the level suffix to begin
with.

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
in this project — it found five real defects now (trait `points` vs
`base_points`, profile key order, a written zero, re-minted technique TIDs,
and the missing `can_level` from §8.1). It has only ever been pointed at one
character — **until 2026-09-02.**

**The `gcs/model/gurps/testdata/` well is dry.** It has exactly one `.gcs`
fixture (`issue767.gcs`), and it was already in the list. There is nothing
free left there; whoever wrote this entry expected more than there turned out
to be.

**What actually extended coverage: the user's own GCS install directory**
(`C:\GOTProject\gcs\`) had seven more real characters sitting next to
`sturm.gcs`, which was already a fixture. Four turned out to be clean fixed
points of GCS's own serializer and are now in `samples/characters/` and both
`FIXTURES` and `test_our_own_fixtures_are_exact_fixed_points` in
`test_oracle.py`: **Alys Dustin, Sharpbend, Surubash, Suruchin.**

Pointing the oracle at the other three found two more real things, neither a
json2gcs defect:

* **`_strip_calc` was missing `defaulted_from`.** A skill's cached best
  default is written to disk from a script evaluation
  (`gcs/model/gurps/skill.go`, `entity.go` around line 206) and recomputed on
  every load — exactly like `calc`, just not nested under that key. Two of the
  seven characters (Ashköl, Qanbash) differed *only* in this field, which
  means `--verify` would have wrongly called it a writer bug on any real
  sheet with skill defaults recorded. Fixed: `cli._strip_calc` now drops both
  `calc` and `defaulted_from` (via a `_DERIVED_KEYS` set).
* **Three characters (Ashköl, Qanbash, Mentash) carry an invalid entity
  `id`.** `A...` is the only valid entity TID prefix (`tid.Kind.ENTITY`);
  these three have a lowercase `b...` id, which is not a `Kind` GCS
  recognizes at all. GCS silently mints a fresh one on every load, the same
  "fix up old data" behaviour `trait.go` does for pre-TID trait ids. That
  means these three can never be an exact fixed point regardless of what
  json2gcs does, so they were **not** added as fixtures. Mentash also has a
  legacy `weapons[]` entry (a `"Thrown"`-usage knife) that loses `range`,
  `rate_of_fire`, `shots` and `bulk` on GCS's rewrite — a one-off migration
  artifact in that specific save, not a pattern worth generalizing since
  those fields are real, editable data everywhere else (docs/04-mapping.md
  4.6) and weapons are never written by json2gcs regardless (§8.5).

  Whether merge/synthesize mode should itself detect and remint an invalid
  base-file `id` (`docs/04-mapping.md` 4.1 currently just says "keep the
  base file's") is now a known open question, not yet acted on — no fixture
  exercises a merge against one of these three files.

  The three excluded originals are not committed; if that open question ever
  gets picked up, re-derive them from `C:\GOTProject\gcs\` rather than
  trusting this file's memory of their exact bytes.

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

## 8.11 Fill gaps from the GCS Master Library

Validated on 2026-09-02 against the library installed at `~/GCS/Master Library`
(43 `.skl` files, 2,005 name+specialization keys; 82 `.adq`; 3,329 equipment
rows carrying structured `weapons[]`).

Every row GCS takes from the library keeps a `source` pointer back to it. That
pointer does not survive into a Foundry export — but the *name* does, and since
§8.2 the name arrives decomposed. Looking a skill up by name recovers what the
export cannot carry.

**Measured, on the container fixture's 22 non-technique skills:**

| | |
|---|---|
| resolved against the library | 22 / 22 |
| difficulty matching the real sheet | **22 / 22** |
| wrong answers | 0 |
| rows it would actually fix | **16** |

`Surgery` `iq/e` → `iq/vh`; `Poisons` → `iq/h`; `Naturalist` → `iq/h`. These are
the rows `docs/05-fidelity.md` lists as unrecoverable.

**Match on name only, not name+specialization.** Doing both resolved just 13 of
22, missing `Religious Ritual (Turchin)`, `Theology (Turchin)`,
`Esoteric Medicine (Menkhu)` — campaign specializations that are not in any
book, and never will be. Name-only is not a loosening: **difficulty is a
property of the skill, not the specialization**, which is exactly why GURPS
writes "Religious Ritual (specific religion)". Two skills (`Tracking`,
`Stealth`) had conflicting difficulties across books and were resolved by
preferring Basic Set; that needs a real policy rather than a lucky default.

Also available: `defaults[]`, `tags`, `features`, `prereqs`, `points_per_level`,
`round_down`, the `source` block itself (restoring GCS's sync-to-library link),
and structured weapon `damage` — which is §8.5's "unrecoverable". 741 library
traits carry `@Nameable@` templates, so matching one against a resolved name
(`@Type@ Rank` vs `Religious Rank`) would recover both the template *and* the
`replacements` map that `docs/05-fidelity.md` §5.2 calls destroyed.

**The hazard, and it is the important part.** This is *inference*. Today a
missing field is visibly missing; library-filled, it becomes a plausible value
that is wrong whenever the player customized that row — and wrong invisibly.
That trades an honest loss for a silent one, which is the failure mode §6.5
exists to prevent. The discipline: only fill fields that would otherwise be
omitted entirely, never overwrite anything the export supplied, mark every
library-sourced value, and report it. `Fidelity` and `--include-lossy` are
already exactly this distinction.

**Ordering.** This ranks *behind* the snapshot store (`docs/06-architecture.md`
§6.9), which answers the same questions by deduction rather than inference. The
library's place is the residue the store cannot cover by definition: rows added
inside Foundry (§8.6) and characters that never had a `.gcs`. Traits matched
only 5 of 23 on exact name, so trait lookup needs the template matching above
before it is worth much; skills are the ready win.

Not vendorable — it is ~125 files of licensed content on the user's disk. It
would be an optional `--library PATH`, discovered the way `--gcs` is.
