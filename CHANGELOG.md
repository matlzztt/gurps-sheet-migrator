# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-09-02

First public release. Version 0.1.0 was never tagged or published, so this
entry covers everything the tool does.

### Added

- **Merge mode** (`json2gcs convert --base`) — reads a Foundry VTT actor
  export plus the original `.gcs` sheet, matches rows by TID, and writes back
  only the fields Foundry is authoritative for. The writer edits the base
  sheet's own structure in place, so modifiers, features, prereqs, library
  `source` links, settings and the points log survive by construction.
- **Synthesize mode** (`json2gcs convert --synthesize`) — builds a structurally
  valid sheet from the export alone, for a character that never had a `.gcs`.
  It is merge against an empty sheet, not a second implementation. The template
  is GCS's own default sheet, and a test re-derives it from the application on
  every run so it cannot drift.
- **Three-way merge via a snapshot store** (`json2gcs remember`, `store.py`) —
  keeps a byte-exact copy of the sheet as it stood when Foundry imported from
  it, keyed by content hash and looked up by row TID. This distinguishes "the
  player didn't touch this" from "the export is stale and the GM's edit would
  be reverted", which a two-way `--base` merge cannot. Disagreements resolve as
  *carry back*, *superseded*, or *conflict*. `convert` takes a snapshot
  automatically unless `--no-remember`; `--no-ancestor` merges two-way.
- **Moved rows** — a row put into a different container, or moved between
  carried and other equipment, is re-attached where the export has it, in the
  export's order, carrying its children. A move with nowhere valid to land is
  reported and skipped rather than forced.
- **`inspect` and `diff` commands** — read-only reports on one export and how
  it lines up with a base sheet. Neither ever writes.
- **GCS as a verifier** — `--verify` has GCS load the result and confirm it
  rewrites it unchanged; `--refresh-calc` runs the output back through GCS so
  its derived values are authoritative. `calc` is never reimplemented.
- **tkinter GUI** (`json2gcs gui`, or the executable with no arguments) — it
  assembles the same argument list the command line takes and calls
  `cli.main`, so every rule about what may be written stays in one place.
- **Standalone Windows executable** — `json2gcs.spec` builds one self-contained
  ~13 MB `json2gcs.exe` needing no Python install.
- **Byte-exact GCS serializer** (`jsonio.py`, `schema.py`) — key order and
  `omitzero` rules transcribed from the GCS Go structs, which is what makes
  `gcs --convert` usable as a test oracle.
- **328-test suite**, including `test_control_export_yields_nothing_to_apply`:
  a control export taken with nothing touched must reconcile to *zero*
  applicable changes.
- **Design record** in [`docs/`](docs/) — the two upstream formats, the full
  field mapping, the measured fidelity and loss inventory, and every rejected
  alternative.

### Behaviour worth knowing

- The sheet's own character name is left alone by default, because a Foundry
  actor is often named for its token or folder. Pass `--rename` to carry it.
- Rows present in the sheet but missing from the export are **kept**; nothing
  in either file distinguishes "deleted in Foundry" from "added to the sheet
  after the export". `--deletions drop` removes them instead.
- Changes flagged **lossy** are reported but not written without
  `--include-lossy`; changes flagged **blocked** are never written, because the
  Foundry value is known to be contaminated.
- `convert` writes to a new file beside the base — never over it.

### Known limitations

Tracked in full in [`docs/08-improvements.md`](docs/08-improvements.md).

- **Spells are unwritten code.** Every fixture has `spells: {}` and the policy
  entries have never executed (§8.7).
- **Weapons are dropped entirely** (§8.5).
- **Techniques' composed names are not decomposed** (§8.3).
- **A row nested inside a Foundry-*created* container loses its parent** and
  lands at the top level of its section, because a GGA-minted id is not a TID
  (§8.6).
- **A skill's difficulty letter is not recoverable** from a Foundry export;
  synthesize writes the real attribute with GCS's own default letter.
- A synthesized sheet is not a GCS fixed point until GCS has opened it once —
  `--refresh-calc` settles it.

[0.2.0]: https://github.com/matlzztt/gurps-sheet-migrator/releases/tag/v0.2.0
