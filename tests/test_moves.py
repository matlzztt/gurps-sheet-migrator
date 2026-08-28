"""Rows that changed container or list between the sheet and the export.

No fixture pair we have contains a move, and the user could not produce one
through the GGA sheet UI on request.  So these tests synthesize moves by
editing the *control* export — the one that is otherwise identical to the base
sheet — which has the useful property that a move is then the only difference
the reconciler can possibly find.  Anything else it reports is a defect in this
file's surgery, not in the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from json2gcs import apply, cli, foundry, gcs, jsonio, reconcile, report

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
SHEET = DIR / "container.gcs"
CONTROL = DIR / "container.foundry.json"

BACKPACK = "Et0bRTzaIEVIXAlQi"  # a carried container
METABACKPACK = "EeBidMeu7scIX-zhc"  # inside the Backpack, itself a container
BOOK = "eMNPcBPVkCDd_WRHP"  # a leaf, inside the Metabackpack
YARQAP = "ed3RQvtTljPJkjcih"  # a carried leaf at the top level
STORES = "esXbdQMVTukOfBaWa"  # an *other* equipment leaf at the top level
OTHER_BOX = "Ekb27Az5KUlErsNLy"  # an *other* equipment container


# --------------------------------------------------------------------------
# surgery on the export
# --------------------------------------------------------------------------


def _collection(raw: dict, path: list[str]) -> dict:
    """Walk to a ``{"00000": {...}}`` collection by carried/other and TIDs."""
    equipment = raw["system"]["equipment"]
    node = equipment[path[0]]
    for tid in path[1:]:
        node = _find(node, tid)["contains"]
    return node


def _find(collection: dict, tid: str) -> dict:
    for row in collection.values():
        if row.get("uuid") == tid:
            return row
    raise AssertionError(f"{tid} not in this collection")


def _pop(collection: dict, tid: str) -> dict:
    for key, row in list(collection.items()):
        if row.get("uuid") == tid:
            return collection.pop(key)
    raise AssertionError(f"{tid} not in this collection")


def _push(collection: dict, row: dict, *, first: bool = False) -> None:
    """Add a row, keeping the zero-padded counter keys GGA writes.

    Those keys are the export's ordering, so putting a row at the front means
    renumbering — which is what GGA itself does.
    """
    width = len(next(iter(collection), "00000"))
    if not first:
        nxt = max((int(k) for k in collection), default=-1) + 1
        collection[str(nxt).zfill(width)] = row
        return
    existing = [collection[k] for k in sorted(collection)]
    collection.clear()
    for i, item in enumerate([row, *existing]):
        collection[str(i).zfill(width)] = item


def _moved_raw(*, tid: str, frm: list[str], to: list[str], first: bool = False) -> dict:
    """The control export, as raw JSON, with one row relocated."""
    raw = json.loads(CONTROL.read_text(encoding="utf-8"))
    row = _pop(_collection(raw, frm), tid)
    _push(_collection(raw, to), row, first=first)
    return raw


def _moved(*, tid: str, frm: list[str], to: list[str], first: bool = False):
    return foundry.loads(json.dumps(_moved_raw(tid=tid, frm=frm, to=to, first=first)))


def merge(actor: foundry.Actor) -> tuple[gcs.Sheet, apply.Plan]:
    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(actor, sheet)
    return sheet, apply.apply(result, sheet)


def tids(rows: list) -> list[str]:
    return [row["id"] for row in rows]


# --------------------------------------------------------------------------
# out of a container
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def out_of_container() -> foundry.Actor:
    """The Book of Lines taken out of the Metabackpack and carried loose."""
    return _moved(tid=BOOK, frm=["carried", BACKPACK, METABACKPACK], to=["carried"])


def test_leaving_a_container_is_the_only_change_reported(out_of_container):
    result = reconcile.reconcile(out_of_container, gcs.load(SHEET))
    assert [d.name for d in result.deltas if d.moved] == ["The Book of Lines"]
    assert [(d.name, c.label) for d in result.deltas for c in d.changes if c.applicable] == []


def test_a_move_to_the_top_level_is_still_a_move(out_of_container):
    """``moved_to`` is None here — the row has no new parent — but it moved."""
    result = reconcile.reconcile(out_of_container, gcs.load(SHEET))
    delta = next(d for d in result.deltas if d.moved)
    assert delta.moved_from == METABACKPACK
    assert delta.moved_to is None
    assert (delta.moved_from_label, delta.moved_to_label) == (
        "Metabackpack",
        "carried equipment",
    )


def test_the_row_is_reattached_at_the_top_level(out_of_container):
    sheet, plan = merge(out_of_container)
    assert [d.name for d in plan.moved] == ["The Book of Lines"]

    meta = sheet.by_tid[METABACKPACK]
    assert BOOK not in tids(meta.data.get("children", []))
    assert BOOK in tids(sheet.data["equipment"])

    entry = sheet.by_tid[BOOK]
    assert entry.parent_tid is None
    assert entry.section == "equipment"


def test_the_moved_row_lands_where_the_export_has_it(out_of_container):
    """Appended in the export, so appended in the sheet — not first."""
    sheet, _ = merge(out_of_container)
    assert tids(sheet.data["equipment"])[-1] == BOOK


def test_nothing_else_in_the_sheet_changes(out_of_container):
    sheet, plan = merge(out_of_container)
    assert [f for _, f in plan.applied] == []
    assert plan.sheet_fields == []
    # The subtree is intact apart from the one row that left it.
    assert tids(sheet.by_tid[BACKPACK].data["children"]) == [
        METABACKPACK,
        "eV5VApxpWKHkXCuu2",
    ]


# --------------------------------------------------------------------------
# into a container, and between the two equipment lists
# --------------------------------------------------------------------------


def test_moving_into_a_container():
    """Yarqap, carried loose, put inside the Backpack."""
    actor = _moved(tid=YARQAP, frm=["carried"], to=["carried", BACKPACK])
    sheet, plan = merge(actor)
    assert [d.name for d in plan.moved] == ["Yarqap"]
    assert YARQAP not in tids(sheet.data["equipment"])
    assert tids(sheet.by_tid[BACKPACK].data["children"])[-1] == YARQAP
    assert sheet.by_tid[YARQAP].parent_tid == BACKPACK


def test_moving_between_carried_and_other_changes_the_section():
    """The two GCS equipment lists are one Foundry collection with a flag."""
    actor = _moved(tid=YARQAP, frm=["carried"], to=["other"])
    result = reconcile.reconcile(actor, gcs.load(SHEET))
    delta = next(d for d in result.deltas if d.moved)
    assert (delta.moved_from, delta.moved_to) == ("equipment", "other_equipment")
    assert (delta.moved_from_label, delta.moved_to_label) == (
        "carried equipment",
        "other equipment",
    )

    sheet, plan = merge(actor)
    assert YARQAP not in tids(sheet.data["equipment"])
    assert YARQAP in tids(sheet.data["other_equipment"])
    assert sheet.by_tid[YARQAP].section == "other_equipment"


def test_equipped_survives_a_move_to_other_equipment():
    """GCS does not clear ``equipped`` when an item stops being carried, and
    neither do we — ``ReallyEquipped`` is gated on the list, not the flag."""
    before = gcs.load(SHEET).by_tid[YARQAP].data.get("equipped")
    actor = _moved(tid=YARQAP, frm=["carried"], to=["other"])
    sheet, _ = merge(actor)
    assert sheet.by_tid[YARQAP].data.get("equipped") == before


def test_a_container_carries_its_children_with_it():
    """The Metabackpack moves; the Book inside it is not separately reported."""
    actor = _moved(tid=METABACKPACK, frm=["carried", BACKPACK], to=["other"])
    result = reconcile.reconcile(actor, gcs.load(SHEET))
    assert [d.name for d in result.deltas if d.moved] == ["Metabackpack"]

    sheet, plan = merge(actor)
    moved = sheet.by_tid[METABACKPACK]
    assert tids(moved.data["children"]) == [BOOK]
    assert METABACKPACK in tids(sheet.data["other_equipment"])
    # The child's section follows its container, even though the child itself
    # was never moved.
    assert sheet.by_tid[BOOK].section == "other_equipment"


def test_an_emptied_container_loses_its_children_key():
    """``children`` is omitzero, so GCS would not write an empty list."""
    actor = _moved(tid=BOOK, frm=["carried", BACKPACK, METABACKPACK], to=["other"])
    sheet, _ = merge(actor)
    assert "children" not in sheet.by_tid[METABACKPACK].data


def test_a_row_lands_where_the_export_orders_it_not_merely_at_the_end():
    """Put first among the container's children in the export, first in the
    sheet: the anchor is the nearest following sibling, not the list length."""
    actor = _moved(tid=YARQAP, frm=["carried"], to=["other", OTHER_BOX], first=True)
    sheet, _ = merge(actor)
    assert tids(sheet.by_tid[OTHER_BOX].data["children"]) == [
        YARQAP,
        "eg3N2rlmOKk5_0XoM",  # Antler comb
        "eirlAZQ6Sr_rn1z1j",  # The heavy roll
    ]


# --------------------------------------------------------------------------
# moves that must not be made
# --------------------------------------------------------------------------


def test_a_move_into_a_missing_container_is_refused():
    actor = _moved(tid=YARQAP, frm=["carried"], to=["carried", BACKPACK])
    row = actor.by_tid[YARQAP]
    row.parent_tid = "EnotInTheSheet00"

    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(actor, sheet)
    plan = apply.apply(result, sheet)
    assert plan.moved == []
    assert [(d.name, f) for d, f, _ in plan.skipped if f == "position"] == [
        ("Yarqap", "position")
    ]
    assert YARQAP in tids(sheet.data["equipment"])


def test_a_move_inside_the_rows_own_subtree_is_refused():
    """A container cannot become its own grandchild."""
    actor = _moved(tid=BACKPACK, frm=["carried"], to=["carried"])
    actor.by_tid[BACKPACK].parent_tid = BOOK

    sheet = gcs.load(SHEET)
    plan = apply.apply(reconcile.reconcile(actor, sheet), sheet)
    assert plan.moved == []
    reason = next(r for d, f, r in plan.skipped if f == "position")
    assert "its own children" in reason
    assert BACKPACK in tids(sheet.data["equipment"])


def test_a_move_into_a_leaf_row_is_refused():
    """Containers are a distinct TID kind; a leaf must not grow children."""
    actor = _moved(tid=YARQAP, frm=["carried"], to=["carried"])
    actor.by_tid[YARQAP].parent_tid = STORES

    sheet = gcs.load(SHEET)
    plan = apply.apply(reconcile.reconcile(actor, sheet), sheet)
    assert plan.moved == []
    reason = next(r for d, f, r in plan.skipped if f == "position")
    assert "not a container" in reason
    assert "children" not in sheet.by_tid[STORES].data


# --------------------------------------------------------------------------
# the merged sheet is still a sheet
# --------------------------------------------------------------------------


def test_the_merged_output_still_round_trips(out_of_container):
    sheet, _ = merge(out_of_container)
    text = jsonio.dumps(sheet.data)
    assert jsonio.dumps(jsonio.loads(text)) == text


def test_no_row_is_lost_or_duplicated(out_of_container):
    before = set(gcs.load(SHEET).by_tid)
    sheet, _ = merge(out_of_container)
    seen = [
        row["id"]
        for section in gcs.SECTIONS
        for row in _flatten(sheet.data.get(section) or [])
    ]
    assert sorted(seen) == sorted(before)
    assert len(seen) == len(set(seen))


def _flatten(rows: list) -> list:
    out = []
    for row in rows:
        out.append(row)
        out.extend(_flatten(row.get("children") or []))
    return out


def test_the_report_names_the_move(out_of_container):
    text = report.render(reconcile.reconcile(out_of_container, gcs.load(SHEET)))
    assert "Moved (1)" in text
    assert "The Book of Lines: Metabackpack → carried equipment" in text


def test_a_plan_predicts_the_move_without_writing():
    actor = _moved(tid=YARQAP, frm=["carried"], to=["carried", BACKPACK])
    sheet = gcs.load(SHEET)
    before = jsonio.dumps(sheet.data)
    predicted = apply.plan(reconcile.reconcile(actor, sheet))
    assert [d.name for d in predicted.moved] == ["Yarqap"]
    assert predicted.total == 1
    assert jsonio.dumps(sheet.data) == before


def test_convert_moves_a_row_end_to_end(tmp_path, capsys):
    """The whole pipeline, from a file on disk to a merged file on disk."""
    export = tmp_path / "moved.foundry.json"
    export.write_text(
        json.dumps(_moved_raw(tid=YARQAP, frm=["carried"], to=["carried", BACKPACK])),
        encoding="utf-8",
    )
    out = tmp_path / "merged.gcs"
    code = cli.main(["convert", str(export), "--base", str(SHEET), "-o", str(out)])
    text = capsys.readouterr().out

    assert code == 0
    assert "1 moved" in text
    assert "moved Yarqap: carried equipment → Backpack" in text
    merged = gcs.load(out)
    assert YARQAP in tids(merged.by_tid[BACKPACK].data["children"])
