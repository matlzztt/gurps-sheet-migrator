"""Container and round-trip behaviour, verified against samples/container/.

This fixture set is a controlled experiment, not a found artifact:

* ``container.gcs`` — Stürm with four containers added, one nested two deep.
* ``container.foundry.json`` — exported from Foundry **immediately after
  import**, nothing touched.  Any divergence from the GCS file here is GGA's
  transform, never play.
* ``container-played.foundry.json`` — exported again after a known list of
  edits.  The diff between the two exports is the reconciler's ground truth.

Between them they close the container gap docs/05-fidelity.md 5.6 flagged, and
they turned up three behaviours worth pinning down: renames land in ``name``
only, ``equipped`` cascades through containers, and note indentation grows on
every save cycle.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from json2gcs import foundry, jsonio, tid

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
SHEET = DIR / "container.gcs"
CONTROL = DIR / "container.foundry.json"
PLAYED = DIR / "container-played.foundry.json"


@pytest.fixture(scope="module")
def sheet() -> dict:
    return jsonio.loads(jsonio.read_text(SHEET))


@pytest.fixture(scope="module")
def control() -> foundry.Actor:
    return foundry.load(CONTROL)


@pytest.fixture(scope="module")
def played() -> foundry.Actor:
    return foundry.load(PLAYED)


# --------------------------------------------------------------------------
# the GCS side
# --------------------------------------------------------------------------


def test_sheet_roundtrips_byte_identically():
    original = SHEET.read_bytes()
    assert jsonio.dumps(jsonio.loads(jsonio.read_text(SHEET))).encode("utf-8") == original


def test_sheet_containers_use_uppercase_tids(sheet: dict):
    containers = {
        "traits": "T_7dZkq1Ziwxfz--o",
        "skills": "Sjj3Skr06jC0nmHcX",
        "equipment": "Et0bRTzaIEVIXAlQi",
        "other_equipment": "Ekb27Az5KUlErsNLy",
    }
    for section, expected in containers.items():
        row = next(r for r in sheet[section] if r["id"] == expected)
        assert tid.is_container(row["id"]), section
        assert row["children"], section


def test_sheet_nests_two_deep(sheet: dict):
    backpack = next(r for r in sheet["equipment"] if r["id"] == "Et0bRTzaIEVIXAlQi")
    meta = next(c for c in backpack["children"] if c["id"] == "EeBidMeu7scIX-zhc")
    book = meta["children"][0]
    assert book["id"] == "eMNPcBPVkCDd_WRHP"
    assert book["description"] == "The Book of Lines"
    assert "children" not in book


# --------------------------------------------------------------------------
# the Foundry side
# --------------------------------------------------------------------------


def test_reader_handles_containers_without_warnings(control: foundry.Actor):
    assert control.warnings == []
    assert len(control.by_tid) == len(list(control.rows())) == 78


def test_every_container_survives_the_import(control: foundry.Actor):
    containers = {r.tid: r for r in control.rows() if r.is_container}
    assert set(containers) == {
        "T_7dZkq1Ziwxfz--o",
        "Sjj3Skr06jC0nmHcX",
        "Et0bRTzaIEVIXAlQi",
        "EeBidMeu7scIX-zhc",
        "Ekb27Az5KUlErsNLy",
    }
    assert containers["T_7dZkq1Ziwxfz--o"].kind == "trait_container"
    assert containers["Sjj3Skr06jC0nmHcX"].kind == "skill_container"
    assert containers["Et0bRTzaIEVIXAlQi"].kind == "equipment_container"


def test_nesting_depth_is_preserved(control: foundry.Actor):
    backpack = control.by_tid["Et0bRTzaIEVIXAlQi"]
    meta = control.by_tid["EeBidMeu7scIX-zhc"]
    book = control.by_tid["eMNPcBPVkCDd_WRHP"]
    assert [r.tid for r in backpack.walk()] == [
        "Et0bRTzaIEVIXAlQi",
        "EeBidMeu7scIX-zhc",
        "eMNPcBPVkCDd_WRHP",
        "eV5VApxpWKHkXCuu2",
    ]
    assert meta.parent_tid == backpack.tid
    assert book.parent_tid == meta.tid
    # Children of a carried container are themselves carried.
    assert all(r.carried is True for r in backpack.walk())


def test_container_in_other_equipment(control: foundry.Actor):
    box = control.by_tid["Ekb27Az5KUlErsNLy"]
    assert box.carried is False
    assert all(r.carried is False for r in box.walk())


def test_hierarchy_matches_the_gcs_file(sheet: dict, control: foundry.Actor):
    """Every parent/child edge in the sheet appears in the export."""
    edges: set[tuple[str | None, str]] = set()

    def walk(rows, parent=None):
        for row in rows or ():
            edges.add((parent, row["id"]))
            walk(row.get("children"), row["id"])

    for section in ("traits", "skills", "equipment", "other_equipment", "notes"):
        walk(sheet[section])

    exported = {(r.parent_tid, r.tid) for r in control.rows()}
    assert edges == exported


# --------------------------------------------------------------------------
# what the played export proves
# --------------------------------------------------------------------------


def test_deleting_a_row_removes_it_entirely(control, played):
    """Poisons was deleted in Foundry."""
    assert "sU3-t1E9yl1E581hQ" in control.by_tid
    assert "sU3-t1E9yl1E581hQ" not in played.by_tid
    assert set(control.by_tid) - set(played.by_tid) == {"sU3-t1E9yl1E581hQ"}
    assert not set(played.by_tid) - set(control.by_tid)


def test_deletion_renumbers_the_collection_keys(control, played):
    """Keys are positional, so identity must come from uuid alone."""
    raw_control = jsonio.loads(CONTROL.read_text("utf-8"))["system"]["skills"]
    raw_played = jsonio.loads(PLAYED.read_text("utf-8"))["system"]["skills"]
    assert raw_control["00000"]["uuid"] == "sU3-t1E9yl1E581hQ"  # Poisons
    assert raw_played["00000"]["uuid"] == "sf9Uf7f7EmQioEQ3D"  # everything shifted up


def test_rename_lands_in_name_and_spares_original_name(control, played):
    before = control.by_tid["eMNPcBPVkCDd_WRHP"]
    after = played.by_tid["eMNPcBPVkCDd_WRHP"]
    assert before.display_name == "The Book of Lines"
    assert after.display_name == "The Book of Metabackpacking"
    # originalName is untouched, which is what keeps it usable as an anchor.
    assert before.gcs_name == after.gcs_name == "The Book of Lines"


def test_a_renamed_row_stays_in_place(control, played):
    """A rename must not be mistaken for a delete plus an add."""
    after = played.by_tid["eMNPcBPVkCDd_WRHP"]
    assert after.parent_tid == "EeBidMeu7scIX-zhc"
    assert after.carried is True


def test_quantity_change(control, played):
    assert control.by_tid["eQvR7mN2xLkT4bH9c"].data["count"] == "10"
    assert played.by_tid["eQvR7mN2xLkT4bH9c"].data["count"] == "4"


def test_unequipping_a_container_cascades_to_its_contents(control, played):
    """Un-equipping the Backpack cleared 'equipped' on everything inside it."""
    tree = ["Et0bRTzaIEVIXAlQi", "EeBidMeu7scIX-zhc", "eMNPcBPVkCDd_WRHP", "eV5VApxpWKHkXCuu2"]
    assert all(control.by_tid[t].data["equipped"] for t in tree)
    assert not any(played.by_tid[t].data["equipped"] for t in tree)
    # An unrelated item outside the container is unaffected.
    assert played.by_tid["eq5TLpplO66680bYD"].data["equipped"] is True


def test_current_hp_and_fp_track_play(control, played):
    assert (control.system["HP"]["value"], played.system["HP"]["value"]) == (10, 6)
    assert (control.system["FP"]["value"], played.system["FP"]["value"]) == (11, 3)
    # Maxima are unchanged: only the current pool moved.
    assert control.system["HP"]["max"] == played.system["HP"]["max"] == 10


# --------------------------------------------------------------------------
# corruption vectors these fixtures exposed
# --------------------------------------------------------------------------


def _indents(text: str) -> list[int]:
    return [len(line) - len(line.lstrip()) for line in text.split("\n")]


def test_note_indentation_grows_on_every_save_cycle(sheet, control, played):
    """Leading whitespace compounds: 0 in GCS, 8 after import, 44 after a save.

    The text is otherwise unchanged, and the row was never edited by the
    player.  Writing Foundry's 'notes' straight back into 'local_notes' would
    import this and make it worse every round trip, so notes need whitespace-
    insensitive comparison and must not be copied verbatim.
    """
    container = next(r for r in sheet["traits"] if r["id"] == "T_7dZkq1Ziwxfz--o")
    original = next(c for c in container["children"] if c["id"] == "tqSpM2benfhhMyiq1")

    gcs_note = original["local_notes"]
    first = control.by_tid["tqSpM2benfhhMyiq1"].data["notes"]
    second = played.by_tid["tqSpM2benfhhMyiq1"].data["notes"]

    assert max(_indents(gcs_note)) == 0
    assert max(_indents(first)) == 8
    assert max(_indents(second)) == 44

    collapse = lambda s: re.sub(r"[ \t]+", " ", s)
    assert collapse(first) == collapse(second), "only whitespace changed"


def test_skill_level_is_populated_lazily_not_by_the_player(control, played):
    """'level' is empty right after import and filled in later by GGA.

    Every skill gained a level between the two exports without the player
    touching them, so it is a derived display value, not an input to carry back.
    """
    skills = [
        t
        for t, row in control.by_tid.items()
        if row.section == "skills" and t in played.by_tid
    ]
    changed = [
        t for t in skills
        if control.by_tid[t].data.get("level") != played.by_tid[t].data.get("level")
    ]
    # Every real skill gained one; only the container, which has no level, did not.
    assert set(skills) - set(changed) == {"Sjj3Skr06jC0nmHcX"}
    for t in changed:
        assert control.by_tid[t].data["level"] == ""
        assert isinstance(played.by_tid[t].data["level"], int)
    # 'points' — a real GCS input — did not move.
    assert all(
        control.by_tid[t].data["points"] == played.by_tid[t].data["points"]
        for t in changed
    )


def test_derived_defence_fields_appear_out_of_nowhere(control, played):
    """equippedparry/equippedblock are null until GGA computes them."""
    assert control.system.get("equippedparry") is None
    assert played.system.get("equippedparry") == 10
    assert control.system.get("equippedblock") is None
    assert played.system.get("equippedblock") == 11
