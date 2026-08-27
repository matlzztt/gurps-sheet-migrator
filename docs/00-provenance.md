# Provenance — pinned upstream references

The two upstream projects are cloned into this workspace for reference and are
**git-ignored** (they are large, and re-cloning is cheap). Everything documented
in `docs/` was read at the exact revisions below. If you re-clone, check these
out so the documentation still matches the code.

| Vendor dir | Upstream | Tag | Commit | Commit date |
|---|---|---|---|---|
| `gcs/` | https://github.com/richardwilkes/gcs.git | `v5.48.0` | `ee2778a86b8ef085a7e1b796d813c40c9ee1017e` | 2026-08-26 |
| `gurps/` | https://github.com/crnormand/gurps.git | `v0.18.22` | `a66e5d54640e9a91b1c831b47e4f9aeb3b29b37e` | 2026-08-26 |

Restore with:

```bash
git clone https://github.com/richardwilkes/gcs.git gcs && git -C gcs checkout ee2778a8
```

```bash
git clone https://github.com/crnormand/gurps.git gurps && git -C gurps checkout a66e5d54
```

## Format versions these docs target

| Thing | Version | Where it is declared |
|---|---|---|
| GCS data file format | `5` (`"version": 5`) | `gcs/model/jio/version.go` — `CurrentDataVersion` |
| GCS minimum readable | `2` | same file — `MinimumDataVersion` |
| GGA (Foundry system) | `0.18.22` | `gurps/system.json` |
| Foundry core | `13.351` | seen in the sample export's `_stats` |

## Sample fixtures

`samples/sturm/` holds one character in both formats — the primary regression
fixture for the whole project.

| File | What it is |
|---|---|
| `sturm.gcs` | GCS v5 sheet, saved by GCS |
| `sturm.foundry.json` | Foundry "Export Data" dump of the same actor |

`samples/container/` is a purpose-built pair capturing containers and a known
set of in-play edits. Captured 2026-08-27 against GGA `0.18.13` / Foundry
`13.351`, with GGA's "Use Foundry Items for Equipment" setting **off**.

| File | What it is |
|---|---|
| `container.gcs` | Stürm plus four containers — trait, skill, carried equipment (two levels deep) and other equipment |
| `container.foundry.json` | exported immediately after import, nothing touched — the control |
| `container-played.foundry.json` | exported again after: deleted the *Poisons* skill, arrows 10 → 4, un-equipped the Backpack and *Yarqap*, renamed *The Book of Lines*, edited a note, took HP 10 → 6 and FP 11 → 3 |

The control export is what makes this set valuable: with no play in between,
every difference from `container.gcs` is GGA's transform and nothing else. See
`docs/05-fidelity.md` §5.7 for what it turned up.

`samples/upstream/` holds two fixtures copied out of the `gcs` clone, used to
test the reader/writer against GCS output we did not produce:

| File | What it covers |
|---|---|
| `issue767.gcs` | a small complete sheet (150 points, 1 trait, 2 skills, 2 techniques) |
| `container_with_own_data.eqp` | **the only container fixture we have** — an `E`-prefixed equipment container with its own `weapons`, `modifiers` and `replacements`, plus a nested `e` child |

Both were extracted from git blobs (`git -C gcs show HEAD:model/gurps/testdata/...`),
not copied from the working tree. **The clones are checked out with
`core.autocrlf=true`, so their working-tree files have CRLF** and would fail
byte-exactness tests for reasons that have nothing to do with our code. This
repository pins `eol=lf` in `.gitattributes` so its own copies stay correct.

The Foundry actor was originally *imported from* `sturm.gcs`
(`system.additionalresources.importname` records the filename, and
`system.lastImport` the timestamp). The GCS file was edited afterwards, so the
pair is deliberately **not** identical — the known divergences are listed in
`docs/05-fidelity.md`.
