"""Reconciler tests.

The headline test is :func:`test_control_export_yields_nothing_to_apply`.  The
control export was taken immediately after import with nothing touched, so a
correct reconciler must find no actionable change in it.  Every phantom change
that test would catch is a real bug: a comparison that does not account for
something GGA does to the data on the way in.

The played export then supplies the positive case, checked against the changelog
recorded in docs/00-provenance.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from json2gcs import fields, foundry, gcs, reconcile, report
from json2gcs.fields import Compare, Fidelity
from json2gcs.reconcile import Status

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"


@pytest.fixture(scope="module")
def sheet() -> gcs.Sheet:
    return gcs.load(DIR / "container.gcs")


@pytest.fixture(scope="module")
def control(sheet):
    return reconcile.reconcile(foundry.load(DIR / "container.foundry.json"), sheet)


@pytest.fixture(scope="module")
def played(sheet):
    return reconcile.reconcile(
        foundry.load(DIR / "container-played.foundry.json"), sheet
    )


# --------------------------------------------------------------------------
# the control export
# --------------------------------------------------------------------------


def test_control_export_yields_nothing_to_apply(control):
    """Nothing happened in play, so nothing should be carried back."""
    applicable = [
        (d.name, c.label)
        for d in control.deltas
        for c in d.changes
        if c.applicable
    ]
    assert applicable == []


def test_control_matches_every_row(control):
    assert control.summary()["matched"] == 78
    assert control.by_status(Status.ADDED) == []
    assert control.by_status(Status.MISSING) == []
    assert not [d for d in control.deltas if d.moved_to]


def test_control_reports_no_warnings(control):
    assert control.warnings == []


def test_control_flags_only_known_lossy_rows(control):
    """The two rows that do differ are documented losses, and both are blocked."""
    flagged = {d.name for d in control.changed_rows}
    assert flagged == {"Cloth, Padded", "Poison doses in sealed gut"}
    assert all(c.blocked for _, c in control.blocked)


def test_modifier_bearing_values_are_blocked(control):
    delta = next(d for d in control.changed_rows if d.name == "Cloth, Padded")
    reasons = {c.field: c.blocked for c in delta.changes}
    assert reasons["base_weight"].startswith("row has modifiers")
    assert reasons["base_value"].startswith("row has modifiers")


def test_unitless_quantity_is_blocked_not_reported_as_an_edit(control):
    """0.1 unitless is kg on this sheet; Foundry stores 0.2 lb. Not a change."""
    delta = next(d for d in control.changed_rows if d.name.startswith("Poison"))
    change = next(c for c in delta.changes if c.field == "base_weight")
    assert "unitless" in change.blocked
    assert not change.applicable


def test_sheet_name_difference_is_real(control):
    """The Foundry actor really is called 'Container'; the sheet says 'Stürm'."""
    change = next(c for c in control.profile if c.field == "name")
    assert (change.old, change.new) == ("Stürm", "Container")


# --------------------------------------------------------------------------
# the played export — checked against the recorded changelog
# --------------------------------------------------------------------------


def test_deleted_row_is_reported_as_ambiguous(played):
    missing = played.by_status(Status.MISSING)
    assert [d.name for d in missing] == ["Poisons"]
    assert missing[0].section == "skills"


def test_quantity_change_is_applicable(played):
    delta = next(d for d in played.changed_rows if d.name == "Arrow")
    change = next(c for c in delta.changes if c.field == "quantity")
    assert (str(change.old), str(change.new)) == ("10", "4")
    assert change.applicable
    assert change.fidelity is Fidelity.EXACT


def test_rename_is_detected_and_applicable(played):
    delta = next(d for d in played.changed_rows if d.name == "The Book of Lines")
    change = next(c for c in delta.changes if c.field == "description")
    assert change.new == "The Book of Metabackpacking"
    assert change.applicable


def test_unequip_cascade_is_attributed_to_the_container(played):
    by_tid = {d.tid: d for d in played.deltas}
    assert by_tid["Et0bRTzaIEVIXAlQi"].cascade_from is None  # the Backpack itself
    for child in ("EeBidMeu7scIX-zhc", "eV5VApxpWKHkXCuu2"):
        assert by_tid[child].cascade_from == "Et0bRTzaIEVIXAlQi"
    assert by_tid["eMNPcBPVkCDd_WRHP"].cascade_from == "EeBidMeu7scIX-zhc"
    # Yarqap was un-equipped on its own and must not be blamed on a container.
    assert by_tid["ed3RQvtTljPJkjcih"].cascade_from is None


def test_hp_and_fp_damage_are_derived(played):
    damage = {c.field: c.new for c in played.attributes}
    assert str(damage["attributes[hp].damage"]) == "4"
    assert str(damage["attributes[fp].damage"]) == "8"
    assert all(c.fidelity is Fidelity.DERIVED for c in played.attributes)


def test_note_edit_is_found_but_lossy(played):
    delta = next(d for d in played.changed_rows if d.section == "notes")
    change = next(c for c in delta.changes if c.field == "markdown")
    assert "THIS IS AN EDIT" in str(change.new)
    assert not change.applicable, "GGA re-indents notes, so they need review"


def test_the_played_changelog_is_exactly_what_we_expect(played):
    """No edits beyond the ones actually made in Foundry."""
    applicable = {
        (d.name, c.field) for d in played.deltas for c in d.changes if c.applicable
    }
    assert applicable == {
        ("Arrow", "quantity"),
        ("Backpack", "equipped"),
        ("Metabackpack", "equipped"),
        ("The Book of Lines", "equipped"),
        ("The Book of Lines", "description"),
        ("Horn-tip and tooth on a cord", "equipped"),
        ("Yarqap", "equipped"),
    }


# --------------------------------------------------------------------------
# the policy itself
# --------------------------------------------------------------------------


def test_notes_reconstruction_replays_gga_concatenation(sheet):
    """GGA appends each enabled modifier's name to the note."""
    entry = sheet.by_tid["t89rhDVCsi9fR6yJu"]  # Good Reputation, 7 modifiers, 1 enabled
    rebuilt = fields.expected_notes(entry)
    assert rebuilt.endswith("; People Affected")
    # Only the enabled one; the six disabled modifiers contribute nothing.
    assert rebuilt.count(";") == 1


def test_notes_reconstruction_applies_replacements(sheet):
    entry = sheet.by_tid["tEJZb4tkAleOZlFSG"]  # Duty (@Duty@)
    assert "@Duty@" not in fields.expected_notes(entry)


def test_notes_reconstruction_declines_on_self_control_rows():
    """GGA replaces the note with a localized '[CR: name]' we cannot reproduce.

    No trait in the fixtures has a self-control roll, so this one is built.
    """
    entry = gcs.Entry(
        tid="t" + "A" * 16,
        section="traits",
        data={"name": "Bad Temper", "local_notes": "anything", "cr": 12},
    )
    assert fields.expected_notes(entry) is None


def test_whitespace_only_changes_are_not_edits():
    assert fields.values_equal("a\n    b", "a\n            b", Compare.TEXT)
    assert not fields.values_equal("a b", "a c", Compare.TEXT)


def test_quantity_comparison_ignores_the_unit():
    assert fields.values_equal("2.25 lb", "2.25", Compare.QUANTITY)
    assert fields.values_equal("30 lb", "30", Compare.QUANTITY)
    assert not fields.values_equal("1", "8", Compare.QUANTITY)


def test_equipment_tags_are_not_in_the_policy():
    """GGA reads i.categories, but GCS v5 writes 'tags' — they never survive."""
    assert "tags" not in {rule.gcs for rule in fields.RULES["equipment"]}


def test_nameable_rows_block_name_and_notes(sheet):
    entry = sheet.by_tid["txT8V1Xwh4Wreh4Cf"]  # '@Type@ Rank'
    assert entry.is_nameable


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_report_names_the_real_changes(played):
    text = report.render(played)
    assert "Changes to carry back" in text
    assert "The Book of Metabackpacking" in text
    assert "follows its container" in text
    assert "ambiguous" in text
    assert "Poisons" in text
    assert "Needs review" in text


def test_report_on_a_clean_control_is_quiet(control):
    text = report.render(control)
    assert "Changes to carry back" in text  # the profile name genuinely differs
    assert "follows its container" not in text


def test_report_summarises_long_text_edits(played):
    text = report.render(played)
    assert "appended" in text, "a note edit should say what was added"


def test_report_handles_an_empty_reconciliation():
    text = report.render(reconcile.Reconciliation())
    assert "No differences" in text
