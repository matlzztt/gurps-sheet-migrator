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

The Foundry actor was originally *imported from* `sturm.gcs`
(`system.additionalresources.importname` records the filename, and
`system.lastImport` the timestamp). The GCS file was edited afterwards, so the
pair is deliberately **not** identical — the known divergences are listed in
`docs/05-fidelity.md`.
