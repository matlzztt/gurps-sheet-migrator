"""The snapshot store (docs/06-architecture.md 6.9).

The store exists to supply the one thing a two-way merge cannot have: the sheet
as it was when Foundry imported from it.  So the test that matters is
:func:`test_the_ancestor_is_the_state_foundry_actually_read` — given several
remembered states of a character, picking the wrong one is worse than having no
store at all.

Every test here points the store at ``tmp_path``. Nothing may touch the real one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from json2gcs import cli, foundry, gcs, jsonio, store

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
SHEET = DIR / "container.gcs"
CONTROL = DIR / "container.foundry.json"
PLAYED = DIR / "container-played.foundry.json"


@pytest.fixture
def shelf(tmp_path) -> store.Store:
    return store.Store(tmp_path / "store")


def _sheet_written(tmp_path: Path, when: str, name: str) -> Path:
    """A copy of the fixture claiming it was last written at ``when``."""
    data = jsonio.loads(jsonio.read_text(SHEET))
    data["modified_date"] = when
    out = tmp_path / name
    jsonio.dump(out, data)
    return out


# --------------------------------------------------------------------------
# remembering
# --------------------------------------------------------------------------


def test_a_snapshot_is_the_original_bytes(shelf):
    """Not a re-serialization: the byte stream is the contract (6.5)."""
    snapshot, is_new = shelf.remember(SHEET)
    assert is_new
    assert shelf.bytes_of(snapshot.digest) == SHEET.read_bytes()


def test_remembering_the_same_file_twice_stores_it_once(shelf):
    first, new_first = shelf.remember(SHEET)
    second, new_second = shelf.remember(SHEET)
    assert new_first and not new_second
    assert first.digest == second.digest
    assert len(shelf.snapshots()) == 1


def test_an_edited_sheet_is_a_second_snapshot(shelf, tmp_path):
    """The hash is the identity, so a changed sheet is a new state, not an
    overwrite — which is what makes a history of ancestors possible."""
    shelf.remember(SHEET)
    shelf.remember(_sheet_written(tmp_path, "2026-08-29T09:00:00-03:00", "later.gcs"))
    assert len(shelf.snapshots()) == 2


def test_a_snapshot_records_what_dates_the_content(shelf):
    """``modified_date`` dates the sheet; when we happened to store it does not."""
    snapshot, _ = shelf.remember(SHEET)
    assert snapshot.modified_date == "2026-08-27T14:10:00-03:00"
    assert snapshot.name == "Stürm"
    assert snapshot.rows == 78
    assert len(snapshot.tids) == 78


# --------------------------------------------------------------------------
# finding the base again
# --------------------------------------------------------------------------


def test_an_export_finds_its_sheet_by_tid(shelf):
    """A TID is an identity, so this is deduction — no filename involved."""
    snapshot, _ = shelf.remember(SHEET)
    matches = shelf.matches(foundry.load(PLAYED))
    assert [s.digest for s, _ in matches] == [snapshot.digest]
    assert matches[0][1] == 77, "every row in the export is in the sheet"


def test_an_unrelated_export_matches_nothing(shelf, tmp_path):
    """Overlap is the whole signal, so no overlap must mean no answer rather
    than the only snapshot we happen to hold."""
    shelf.remember(SHEET)
    payload = jsonio.loads(jsonio.read_text(CONTROL))
    for section in ("ads", "skills", "spells", "notes"):
        payload["system"][section] = {}
    payload["system"]["equipment"] = {"carried": {}, "other": {}}
    assert shelf.matches(foundry.loads(jsonio.dumps(payload))) == []


def test_the_ancestor_is_the_state_foundry_actually_read(shelf, tmp_path):
    """The point of the whole module.

    Foundry imported at 14:13. Of the three remembered states, the ancestor is
    the newest one written *before* that — not the newest overall, which is the
    sheet the GM has edited since and which a two-way merge would silently
    revert.
    """
    shelf.remember(_sheet_written(tmp_path, "2026-08-20T10:00:00-03:00", "old.gcs"))
    shelf.remember(SHEET)  # 2026-08-27T14:10 — three minutes before the import
    shelf.remember(_sheet_written(tmp_path, "2026-08-29T09:00:00-03:00", "newer.gcs"))

    actor = foundry.load(PLAYED)
    assert actor.last_import == "Aug 27 2026 14:13:00"
    ancestor = shelf.ancestor_for(actor)
    assert ancestor is not None
    assert ancestor.modified_date == "2026-08-27T14:10:00-03:00"


def test_an_unparseable_import_stamp_still_gets_an_answer(shelf):
    """Degrade to the best TID match rather than to nothing: a odd timestamp
    is a reason to be less certain, not a reason to be useless."""
    shelf.remember(SHEET)
    actor = foundry.load(PLAYED)
    actor.system["lastImport"] = "sometime last Tuesday"
    assert shelf.ancestor_for(actor) is not None


def test_no_snapshots_means_no_ancestor(shelf):
    assert shelf.ancestor_for(foundry.load(PLAYED)) is None


def test_snapshots_sharing_a_date_break_the_tie_the_same_way_every_time(
    shelf, tmp_path
):
    """``convert`` re-remembers its base, so a run routinely stores a second
    copy dated the same as one already held. Picking between them by luck made
    the three-way merge intermittent — which is worse than either answer."""
    first, _ = shelf.remember(SHEET)
    edited = gcs.load(SHEET)
    edited.by_tid["st-aIJoQceF4-T__2"].data["points"] = jsonio.Num("12")
    twin = tmp_path / "twin.gcs"
    jsonio.dump(twin, edited.data)  # same modified_date, different content
    second, _ = shelf.remember(twin)

    assert first.modified_date == second.modified_date
    actor = foundry.load(PLAYED)
    picked = {shelf.ancestor_for(actor).digest for _ in range(20)}
    assert picked == {first.digest}, "the earlier-remembered snapshot, always"


# --------------------------------------------------------------------------
# the store on disk
# --------------------------------------------------------------------------


def test_a_corrupt_index_does_not_lose_the_sheets(shelf):
    """The blobs are the data; the index is derivable from them."""
    snapshot, _ = shelf.remember(SHEET)
    (shelf.root / "index.json").write_text("{not json", encoding="utf-8")
    assert shelf.snapshots() == []
    assert shelf.bytes_of(snapshot.digest) == SHEET.read_bytes()


def test_find_root_prefers_the_argument_then_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("JSON2GCS_STORE", str(tmp_path / "from-env"))
    assert store.find_root(str(tmp_path / "explicit")) == tmp_path / "explicit"
    assert store.find_root() == tmp_path / "from-env"
    monkeypatch.delenv("JSON2GCS_STORE")
    assert store.find_root() == store.default_root()


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_remember_command_stores_and_lists(capsys, tmp_path):
    root = str(tmp_path / "store")
    code, text = run(capsys, "remember", str(SHEET), "--store", root)
    assert code == 0 and "remembered" in text

    code, text = run(capsys, "remember", "--list", "--store", root)
    assert code == 0
    assert "Stürm" in text and "78 rows" in text


def test_remember_with_nothing_to_do_says_so(tmp_path, capsys):
    code = cli.main(["remember", "--store", str(tmp_path / "store")])
    assert code == 2
    assert "at least one" in capsys.readouterr().err


def test_listing_an_empty_store_is_not_an_error(capsys, tmp_path):
    code, text = run(capsys, "remember", "--list", "--store", str(tmp_path / "nope"))
    assert code == 0
    assert "empty" in text


def test_convert_remembers_the_base_sheet(capsys, tmp_path):
    """So the ancestor is captured without the user having to think about it."""
    root = tmp_path / "store"
    code, text = run(
        capsys,
        "convert",
        str(PLAYED),
        "--base",
        str(SHEET),
        "-o",
        str(tmp_path / "out.gcs"),
        "--store",
        str(root),
    )
    assert code == 0
    assert "remembered this sheet" in text
    assert store.Store(root).matches(foundry.load(PLAYED))


def test_convert_can_be_told_not_to_remember(capsys, tmp_path):
    root = tmp_path / "store"
    code, text = run(
        capsys,
        "convert",
        str(PLAYED),
        "--base",
        str(SHEET),
        "-o",
        str(tmp_path / "out.gcs"),
        "--store",
        str(root),
        "--no-remember",
    )
    assert code == 0
    assert "remembered" not in text
    assert store.Store(root).snapshots() == []


def test_convert_finds_its_base_through_the_store(capsys, tmp_path):
    """Phase 2: no --base, no sheet beside the export — the row TIDs are enough.

    The base is the *live* sheet the snapshot was taken from, not the snapshot:
    merging into the snapshot would silently discard everything done in GCS
    since it was stored.
    """
    root = tmp_path / "store"
    store.Store(root).remember(SHEET)

    away = tmp_path / "elsewhere"
    away.mkdir()
    export = away / "actor.json"
    export.write_bytes(PLAYED.read_bytes())

    code, text = run(
        capsys, "convert", str(export), "-o", str(tmp_path / "out.gcs"),
        "--store", str(root), "--dry-run",
    )
    assert code == 0
    assert "found in the snapshot store" in text
    assert str(SHEET) in text, "it must merge into the live sheet, not the copy"


def test_diff_finds_its_base_through_the_store(capsys, tmp_path):
    root = tmp_path / "store"
    store.Store(root).remember(SHEET)
    away = tmp_path / "elsewhere"
    away.mkdir()
    export = away / "actor.json"
    export.write_bytes(PLAYED.read_bytes())

    code, text = run(capsys, "diff", str(export), "--store", str(root))
    assert "found in the snapshot store" in text
    assert "Arrow" in text, "and it actually reconciled against it"


def test_a_moved_sheet_falls_back_to_the_remembered_copy(capsys, tmp_path):
    """The original is gone, so the copy is the best base available — but the
    output must land beside the export, never inside the store."""
    root = tmp_path / "store"
    original = tmp_path / "will-be-deleted.gcs"
    original.write_bytes(SHEET.read_bytes())
    store.Store(root).remember(original)
    original.unlink()

    away = tmp_path / "elsewhere"
    away.mkdir()
    export = away / "actor.json"
    export.write_bytes(PLAYED.read_bytes())

    code, text = run(capsys, "convert", str(export), "--store", str(root))
    assert code == 0
    assert "the remembered copy" in text
    assert "no longer at" in text
    written = away / "will-be-deleted.merged.gcs"
    assert written.is_file(), "output belongs beside the export"
    assert not any(
        p.name.endswith(".merged.gcs") for p in root.rglob("*")
    ), "nothing may be written into the store"


def test_a_path_now_holding_someone_else_is_not_used(capsys, tmp_path):
    """A recorded path is not a promise. If the file there is a different
    character, the TID check must catch it rather than merging into a stranger."""
    root = tmp_path / "store"
    original = tmp_path / "sheet.gcs"
    original.write_bytes(SHEET.read_bytes())
    store.Store(root).remember(original)
    original.write_bytes((REPO / "samples" / "characters" / "Suruchin.gcs").read_bytes())

    away = tmp_path / "elsewhere"
    away.mkdir()
    export = away / "actor.json"
    export.write_bytes(PLAYED.read_bytes())

    code, text = run(capsys, "convert", str(export), "--store", str(root), "--dry-run")
    assert code == 0
    assert "the remembered copy" in text, "must fall back, not merge into Suruchin"


def test_without_a_snapshot_the_error_says_what_to_do(capsys, tmp_path):
    away = tmp_path / "elsewhere"
    away.mkdir()
    export = away / "actor.json"
    export.write_bytes(PLAYED.read_bytes())
    code = cli.main(["convert", str(export), "--store", str(tmp_path / "empty")])
    assert code == 2
    assert "json2gcs remember" in capsys.readouterr().err


def _sheet_the_gm_edited(tmp_path: Path) -> Path:
    """A copy of the fixture with Stealth raised, as if in GCS after exporting."""
    sheet = gcs.load(SHEET)
    sheet.by_tid["st-aIJoQceF4-T__2"].data["points"] = jsonio.Num("12")
    out = tmp_path / "sheet.gcs"
    jsonio.dump(out, sheet.data)
    return out


def test_convert_uses_the_remembered_sheet_as_the_ancestor(capsys, tmp_path):
    """Phase 3, end to end: remember, edit in GCS, then merge a play export."""
    root = tmp_path / "store"
    store.Store(root).remember(SHEET)
    live = _sheet_the_gm_edited(tmp_path)

    code, text = run(
        capsys, "convert", str(PLAYED), "--base", str(live),
        "-o", str(tmp_path / "out.gcs"), "--store", str(root),
    )
    assert code == 0
    assert "Three-way merge" in text
    assert "Already newer in the sheet" in text
    assert "keeping 12" in text

    merged = gcs.load(tmp_path / "out.gcs")
    assert str(merged.by_tid["st-aIJoQceF4-T__2"].data["points"]) == "12", (
        "the GM's edit must survive the merge"
    )


def test_no_ancestor_restores_the_two_way_behaviour(capsys, tmp_path):
    """The escape hatch has to actually reproduce the old answer, including
    the revert — otherwise it is not a comparison anyone can trust."""
    root = tmp_path / "store"
    store.Store(root).remember(SHEET)
    live = _sheet_the_gm_edited(tmp_path)

    code, text = run(
        capsys, "convert", str(PLAYED), "--base", str(live),
        "-o", str(tmp_path / "out.gcs"), "--store", str(root), "--no-ancestor",
    )
    assert code == 0
    assert "Three-way merge" not in text
    merged = gcs.load(tmp_path / "out.gcs")
    assert str(merged.by_tid["st-aIJoQceF4-T__2"].data["points"]) == "8", (
        "two-way reverts the GM's edit; that is what phase 3 exists to stop"
    )


def test_with_nothing_remembered_the_merge_is_still_two_way(capsys, tmp_path):
    """convert snapshots its base before merging, so without this the run
    would find the copy it had just taken and call comparing the sheet against
    itself a three-way merge."""
    live = _sheet_the_gm_edited(tmp_path)
    code, text = run(
        capsys, "convert", str(PLAYED), "--base", str(live),
        "-o", str(tmp_path / "out.gcs"), "--store", str(tmp_path / "empty"),
    )
    assert code == 0
    assert "Three-way merge" not in text
    assert "remembered this sheet" in text, "but it is still remembered for next time"


def test_a_snapshot_identical_to_the_base_is_not_an_ancestor(capsys, tmp_path):
    """An ancestor equal to the target teaches nothing — every field would
    classify as 'only the export moved', which is two-way by another name."""
    root = tmp_path / "store"
    live = tmp_path / "sheet.gcs"
    live.write_bytes(SHEET.read_bytes())
    store.Store(root).remember(live)

    code, text = run(
        capsys, "convert", str(PLAYED), "--base", str(live),
        "-o", str(tmp_path / "out.gcs"), "--store", str(root),
    )
    assert code == 0
    assert "Three-way merge" not in text


def test_an_unwritable_store_does_not_fail_the_merge(capsys, tmp_path, monkeypatch):
    """The merge is what the user asked for; the snapshot is a convenience."""
    def explode(self, path, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(store.Store, "remember", explode)
    code, text = run(
        capsys,
        "convert",
        str(PLAYED),
        "--base",
        str(SHEET),
        "-o",
        str(tmp_path / "out.gcs"),
        "--store",
        str(tmp_path / "store"),
    )
    assert code == 0
    assert "not remembered" in text
    assert (tmp_path / "out.gcs").exists()
