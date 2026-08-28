"""Applier tests — writing a reconciliation into the base sheet.

Two properties matter more than any individual field:

* **Idempotence.** Converting, re-exporting and converting again must not keep
  changing the file.  This is the test that catches compounding corruption —
  the modifier names accumulating in notes, the note indentation growing
  (docs/05-fidelity.md 5.7).
* **Non-destruction.** Merge mode only works if everything Foundry never knew
  about survives untouched: modifiers, features, prereqs, library `source`
  links, settings, points_record.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from json2gcs import apply, foundry, gcs, jsonio, reconcile, schema
from json2gcs.apply import DeletionPolicy
from json2gcs.reconcile import Status

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
CONTROL = DIR / "container.foundry.json"
PLAYED = DIR / "container-played.foundry.json"
SHEET = DIR / "container.gcs"

STAMP = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def merge(export: Path, **kwargs) -> tuple[gcs.Sheet, apply.Plan]:
    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(foundry.load(export), sheet)
    plan = apply.apply(result, sheet, now=STAMP, **kwargs)
    return sheet, plan


# --------------------------------------------------------------------------
# the merged output is a valid GCS file
# --------------------------------------------------------------------------


def test_output_round_trips_through_our_own_writer():
    sheet, _ = merge(PLAYED)
    text = jsonio.dumps(sheet.data)
    assert jsonio.dumps(jsonio.loads(text)) == text


def test_output_keeps_the_format_contract():
    sheet, _ = merge(PLAYED)
    raw = jsonio.dumps(sheet.data).encode("utf-8")
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_output_keys_stay_in_canonical_order():
    sheet, _ = merge(PLAYED)
    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    assert reparsed.warnings == []
    for entry in reparsed.by_tid.values():
        ranks = [schema.order_key(entry.section, k) for k in entry.data]
        assert ranks == sorted(ranks), f"{entry.name}: {list(entry.data)}"


def test_output_still_parses_as_a_sheet_with_every_row():
    sheet, _ = merge(PLAYED)
    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    assert len(reparsed.by_tid) == 78


# --------------------------------------------------------------------------
# the edits themselves
# --------------------------------------------------------------------------


def test_quantity_is_written():
    sheet, _ = merge(PLAYED)
    assert str(sheet.by_tid["eQvR7mN2xLkT4bH9c"].data["quantity"]) == "4"


def test_unequipping_removes_the_key_rather_than_writing_false():
    """`equipped` is omitzero in Go, so GCS never writes `equipped: false`."""
    sheet, _ = merge(PLAYED)
    for tid in ("Et0bRTzaIEVIXAlQi", "EeBidMeu7scIX-zhc", "eMNPcBPVkCDd_WRHP"):
        assert "equipped" not in sheet.by_tid[tid].data
    # Something still equipped keeps the key.
    assert sheet.by_tid["eq5TLpplO66680bYD"].data["equipped"] is True


def test_quantity_survives_a_zero_because_it_has_no_omitzero():
    row = {"id": "e" + "A" * 16, "description": "x", "quantity": jsonio.Num("1")}
    apply._set(row, "equipment", "quantity", jsonio.Num("0"))
    assert "quantity" in row and str(row["quantity"]) == "0"
    apply._set(row, "equipment", "equipped", False)
    assert "equipped" not in row


def test_rename_is_written_and_the_row_stays_nested():
    sheet, _ = merge(PLAYED)
    entry = sheet.by_tid["eMNPcBPVkCDd_WRHP"]
    assert entry.data["description"] == "The Book of Metabackpacking"
    assert entry.parent_tid == "EeBidMeu7scIX-zhc"


def test_damage_is_written_into_the_right_attribute():
    sheet, _ = merge(PLAYED)
    attrs = {a["attr_id"]: a for a in sheet.data["attributes"]}
    assert str(attrs["hp"]["damage"]) == "4"
    assert str(attrs["fp"]["damage"]) == "8"
    # Inserted after adj, before calc — the order GCS writes.
    assert list(attrs["hp"]) == ["attr_id", "adj", "damage", "calc"]


def test_modified_date_is_stamped():
    sheet, plan = merge(PLAYED)
    assert sheet.data["modified_date"] == "2026-08-27T12:00:00+00:00"
    assert any("modified_date" in note for note in plan.notes)


def test_lossy_changes_are_skipped_by_default():
    sheet, plan = merge(PLAYED)
    skipped = {(d.name, f) for d, f, _ in plan.skipped}
    assert ("Cloth, Padded", "base_weight") in skipped
    # The note keeps its original text.
    assert "THIS IS AN EDIT" not in sheet.by_tid["n1sRyf8TbFfiovjAo"].data["markdown"]


def test_include_lossy_writes_them():
    sheet, plan = merge(PLAYED, include_lossy=True)
    assert "THIS IS AN EDIT" in sheet.by_tid["n1sRyf8TbFfiovjAo"].data["markdown"]
    # Blocked changes are still withheld: --include-lossy is not --force.
    assert sheet.by_tid["eP-4BkxA6rFb7w50l"].data["base_weight"] == "1"


# --------------------------------------------------------------------------
# nothing else is disturbed
# --------------------------------------------------------------------------


def test_untouched_rows_are_byte_identical():
    before = gcs.load(SHEET)
    untouched = {
        tid: copy.deepcopy(entry.data)
        for tid, entry in before.by_tid.items()
    }
    sheet, plan = merge(PLAYED)
    edited = {d.tid for d, _ in plan.applied}
    for tid, entry in sheet.by_tid.items():
        if tid in edited:
            continue
        assert entry.data == untouched[tid], f"{entry.name} changed unexpectedly"


def test_everything_foundry_never_knew_about_survives():
    """The whole premise of merge mode."""
    before = gcs.load(SHEET)
    sheet, _ = merge(PLAYED)

    # Per-row: modifiers, features, library source, tags, prereqs.
    for tid, entry in sheet.by_tid.items():
        original = before.by_tid[tid].data
        for key in ("modifiers", "features", "source", "tags", "prereqs", "weapons"):
            assert entry.data.get(key) == original.get(key), f"{entry.name}.{key}"

    # Sheet-level: settings and the points log.
    assert sheet.data["settings"] == before.data["settings"]
    assert sheet.data["points_record"] == before.data["points_record"]
    assert sheet.data["id"] == before.data["id"]
    assert sheet.data["created_date"] == before.data["created_date"]


def test_a_control_export_changes_nothing():
    """Nothing was touched in play, so the merged sheet is the base sheet."""
    before = jsonio.read_text(SHEET)
    sheet, plan = merge(CONTROL)
    assert [f for _, f in plan.applied] == []
    assert plan.sheet_fields == []
    assert jsonio.dumps(sheet.data) == before


def test_rename_is_the_one_thing_a_control_export_can_still_change():
    """The actor name really does differ; --rename is what carries it back."""
    before = jsonio.read_text(SHEET)
    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(foundry.load(CONTROL), sheet, rename=True)
    plan = apply.apply(result, sheet, now=STAMP)
    assert plan.sheet_fields == ["name"]
    after = jsonio.dumps(sheet.data)
    # The name and the timestamp; nothing else.
    differing = [
        (a, b)
        for a, b in zip(before.split("\n"), after.split("\n"))
        if a != b
    ]
    assert len(differing) == 2, differing


# --------------------------------------------------------------------------
# idempotence
# --------------------------------------------------------------------------


def test_applying_the_same_export_twice_changes_nothing_the_second_time():
    sheet, _ = merge(PLAYED)
    once = jsonio.dumps(sheet.data)

    # Reconcile the merged sheet against the same export again.
    again = gcs.loads(once)
    result = reconcile.reconcile(foundry.load(PLAYED), again)
    plan = apply.apply(result, again, now=STAMP)

    assert [f for _, f in plan.applied] == [], "a second pass should be a no-op"
    assert jsonio.dumps(again.data) == once


def test_notes_do_not_accumulate_modifier_names():
    """The compounding-corruption case docs/04-mapping.md 4.4 warns about."""
    sheet, _ = merge(PLAYED, include_lossy=True)
    text = jsonio.dumps(sheet.data)
    again = gcs.loads(text)
    result = reconcile.reconcile(foundry.load(PLAYED), again)
    apply.apply(result, again, now=STAMP, include_lossy=True)

    good_rep = again.by_tid["t89rhDVCsi9fR6yJu"].data["local_notes"]
    assert good_rep.count("People Affected") == 0, (
        "the modifier name leaked into local_notes and would grow each pass"
    )


# --------------------------------------------------------------------------
# deletions
# --------------------------------------------------------------------------


def test_deletions_are_kept_by_default():
    sheet, plan = merge(PLAYED)
    assert [d.name for d in plan.kept] == ["Poisons"]
    assert plan.dropped == []
    assert "sU3-t1E9yl1E581hQ" in sheet.by_tid


def test_deletions_can_be_dropped_on_request():
    sheet, plan = merge(PLAYED, deletions=DeletionPolicy.DROP)
    assert [d.name for d in plan.dropped] == ["Poisons"]
    assert "sU3-t1E9yl1E581hQ" not in sheet.by_tid
    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    assert "sU3-t1E9yl1E581hQ" not in reparsed.by_tid
    assert len(reparsed.by_tid) == 77


def test_dropping_a_nested_row_leaves_the_parent_intact():
    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(foundry.load(PLAYED), sheet)
    # Pretend the nested book vanished from the export.
    book = sheet.by_tid["eMNPcBPVkCDd_WRHP"]
    result.deltas = [
        d for d in result.deltas if d.tid != "eMNPcBPVkCDd_WRHP"
    ] + [
        reconcile.RowDelta(
            tid=book.tid,
            section=book.section,
            name=book.name,
            status=Status.MISSING,
            entry=book,
        )
    ]
    apply.apply(result, sheet, deletions=DeletionPolicy.DROP, now=STAMP)
    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    assert "eMNPcBPVkCDd_WRHP" not in reparsed.by_tid
    assert "EeBidMeu7scIX-zhc" in reparsed.by_tid  # the Metabackpack survives
    assert "children" not in reparsed.by_tid["EeBidMeu7scIX-zhc"].data


def test_unknown_deletion_policy_is_rejected():
    sheet = gcs.load(SHEET)
    result = reconcile.reconcile(foundry.load(PLAYED), sheet)
    with pytest.raises(ValueError, match="unknown deletion policy"):
        apply.apply(result, sheet, deletions="maybe")


# --------------------------------------------------------------------------
# rows added inside Foundry
# --------------------------------------------------------------------------


def _actor_with_extra_row(section: str, row: dict) -> foundry.Actor:
    import json

    payload = json.loads(PLAYED.read_text("utf-8"))
    collection = (
        payload["system"]["equipment"]["carried"]
        if section == "equipment"
        else payload["system"][section]
    )
    collection["09999"] = row
    return foundry.loads(json.dumps(payload))


def test_a_row_added_in_foundry_gets_a_fresh_tid():
    sheet = gcs.load(SHEET)
    actor = _actor_with_extra_row(
        "skills", {"name": "Riding", "points": "2", "pageref": "B217", "save": True}
    )
    result = reconcile.reconcile(actor, sheet)
    added = result.by_status(Status.ADDED)
    assert [d.name for d in added] == ["Riding"]

    plan = apply.apply(result, sheet, now=STAMP)
    assert len(plan.added) == 1
    _, minted = plan.added[0]
    assert minted.startswith("s") and len(minted) == 17

    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    fresh = reparsed.by_tid[minted]
    assert fresh.data["name"] == "Riding"
    assert str(fresh.data["points"]) == "2"
    assert fresh.data["reference"] == "B217"
    assert list(fresh.data) == ["id", "name", "reference", "points"]


def test_an_added_equipment_row_gets_an_equipment_tid_and_a_quantity():
    sheet = gcs.load(SHEET)
    actor = _actor_with_extra_row(
        "equipment", {"name": "Rope", "count": "3", "equipped": True, "save": True}
    )
    result = reconcile.reconcile(actor, sheet)
    plan = apply.apply(result, sheet, now=STAMP)
    _, minted = plan.added[0]
    assert minted.startswith("e")

    fresh = gcs.loads(jsonio.dumps(sheet.data)).by_tid[minted]
    assert fresh.data["description"] == "Rope"
    assert str(fresh.data["quantity"]) == "3"
    assert fresh.data["equipped"] is True


# --------------------------------------------------------------------------
# planning without writing
# --------------------------------------------------------------------------


def test_plan_reports_without_touching_the_sheet():
    sheet = gcs.load(SHEET)
    before = jsonio.dumps(sheet.data)
    result = reconcile.reconcile(foundry.load(PLAYED), sheet)
    outcome = apply.plan(result)
    assert len(outcome.applied) == 7
    assert [d.name for d in outcome.kept] == ["Poisons"]
    assert jsonio.dumps(sheet.data) == before, "plan() must not mutate"


def test_a_row_added_inside_a_newly_added_container_nests_correctly():
    """Both rows are new to the sheet, and one is inside the other.

    The child can only find its parent if the parent was registered in
    ``sheet.by_tid`` when it was created -- and only if the parent was created
    first, which the export's depth-first order guarantees and the report's
    alphabetical order does not. The container is named to sort last precisely
    so that alphabetical order would fail this.

    Note the container needs a valid TID of its own for the child to reference:
    Foundry drops the link when a parent's uuid is not a TID. See the handoff.
    """
    import json

    payload = json.loads(PLAYED.read_text("utf-8"))
    payload["system"]["equipment"]["carried"]["09999"] = {
        "name": "Zzz Sack",  # sorts last, so alphabetical order would break this
        "count": "1",
        "save": True,
        "uuid": "EnewContainer01xy",
        "contains": {
            "00000": {"name": "Apple", "count": "2", "save": True, "uuid": ""},
        },
    }
    actor = foundry.loads(json.dumps(payload))

    sheet = gcs.load(SHEET)
    plan = apply.apply(reconcile.reconcile(actor, sheet), sheet, now=STAMP)
    minted = {delta.name: tid for delta, tid in plan.added}
    assert set(minted) == {"Zzz Sack", "Apple"}

    reparsed = gcs.loads(jsonio.dumps(sheet.data))
    sack = reparsed.by_tid[minted["Zzz Sack"]]
    assert sack.tid == "EnewContainer01xy", "a usable TID is kept, not replaced"
    assert [c.data["description"] for c in sack.children] == ["Apple"]
    assert reparsed.by_tid[minted["Apple"]].parent_tid == sack.tid
    # And not also loose at the top level.
    assert minted["Apple"] not in [row["id"] for row in reparsed.data["equipment"]]
