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

from json2gcs import apply, fields, foundry, gcs, reconcile, report
from json2gcs.fields import Compare, Fidelity
from json2gcs.jsonio import Num
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
    assert not [d for d in control.deltas if d.moved]


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


def test_the_sheet_name_is_left_alone_by_default(control):
    """The actor is called 'Container'; the sheet is 'Stürm' and stays that way."""
    assert [c for c in control.profile if c.field == "name"] == []


def test_rename_opts_the_name_difference_back_in(sheet):
    """The difference is real; --rename is what acts on it."""
    result = reconcile.reconcile(
        foundry.load(DIR / "container.foundry.json"), sheet, rename=True
    )
    change = next(c for c in result.profile if c.field == "name")
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
    assert "follows its container" not in text


def test_report_summarises_long_text_edits(played):
    text = report.render(played)
    assert "appended" in text, "a note edit should say what was added"


def test_report_handles_an_empty_reconciliation():
    text = report.render(reconcile.Reconciliation())
    assert "No differences" in text


# --------------------------------------------------------------------------
# the sheet moving on without the export
# --------------------------------------------------------------------------


def _with_modified(base: gcs.Sheet, when: str) -> gcs.Sheet:
    """The same sheet, claiming it was last written at ``when``."""
    copy = gcs.load(DIR / "container.gcs")
    copy.data["modified_date"] = when
    return copy


def test_a_sheet_edited_after_the_import_is_flagged(sheet):
    """A two-way merge cannot see this, so it has to be said out loud: the
    export predates the sheet, and applying it reverts whatever GCS changed."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    assert actor.last_import == "Aug 27 2026 14:13:00"

    later = _with_modified(sheet, "2026-08-29T09:00:00-03:00")
    warnings = reconcile.reconcile(actor, later).warnings
    assert any("after Foundry imported it" in w for w in warnings)
    assert any("2026-08-29T09:00:00-03:00" in w and "Aug 27 2026" in w for w in warnings), (
        "the warning must show both timestamps; the comparison is not exact "
        "enough to assert a verdict on its own"
    )


def test_the_real_fixture_is_not_flagged(played):
    """container.gcs was written at 14:10 and imported at 14:13 — three
    minutes, and nothing touched in between."""
    assert not any("after Foundry imported it" in w for w in played.warnings)


def test_an_unreadable_timestamp_is_not_evidence(sheet):
    """GGA's format is fixed, but a hand-edited or future export must degrade
    to silence rather than to a false accusation."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    actor.system["lastImport"] = "sometime last Tuesday"
    later = _with_modified(sheet, "2026-08-29T09:00:00-03:00")
    assert not any(
        "after Foundry imported it" in w
        for w in reconcile.reconcile(actor, later).warnings
    )


# --------------------------------------------------------------------------
# three-way: telling a play edit from a GCS edit
# --------------------------------------------------------------------------

STEALTH = "st-aIJoQceF4-T__2"
ARROW = "eQvR7mN2xLkT4bH9c"
GOOD_REPUTATION = "t89rhDVCsi9fR6yJu"


@pytest.fixture(scope="module")
def ancestor() -> gcs.Sheet:
    """The sheet as Foundry imported it — the same file the export came from."""
    return gcs.load(DIR / "container.gcs")


def _edited(**edits) -> gcs.Sheet:
    """A fresh copy of the sheet with rows changed as if in GCS after the export."""
    sheet = gcs.load(DIR / "container.gcs")
    for row_tid, (key, value) in edits.items():
        sheet.by_tid[row_tid].data[key] = value
    return sheet


def _change(result, name: str, field_name: str):
    for delta in result.deltas:
        if delta.name == name:
            for change in delta.changes:
                if change.field == field_name:
                    return change
    return None


def test_only_foundry_moved_still_carries_back(ancestor):
    """The ordinary case must be untouched: the sheet still holds what Foundry
    imported, so the export is the newer truth."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    result = reconcile.reconcile(actor, _edited(), ancestor=ancestor)
    quantity = _change(result, "Arrow", "quantity")
    assert quantity is not None and quantity.applicable
    assert str(quantity.new) == "4"
    assert result.conflicts == []


def test_only_the_sheet_moved_is_superseded_not_reverted(ancestor):
    """The lost update this whole mechanism exists to prevent.

    The GM raised Stealth to 12 in GCS after exporting. The export still
    carries the 8 it was imported with — which is not an edit to carry back,
    it is simply older.
    """
    actor = foundry.load(DIR / "container-played.foundry.json")
    sheet = _edited(**{STEALTH: ("points", Num("12"))})

    two_way = reconcile.reconcile(actor, sheet)
    reverting = _change(two_way, "Stealth", "points")
    assert reverting is not None and reverting.applicable, (
        "a two-way merge cannot see the problem — this is the behaviour being fixed"
    )

    three_way = reconcile.reconcile(actor, sheet, ancestor=ancestor)
    assert _change(three_way, "Stealth", "points") is None
    assert [(d.name, c.field) for d, c in three_way.superseded] == [
        ("Stealth", "points")
    ]


def test_both_sides_moved_is_a_conflict_and_is_never_applied(ancestor):
    """Ten arrows became four in play and seven in GCS. Nothing can reconcile
    that but a person, so it is reported and left alone."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    sheet = _edited(**{ARROW: ("quantity", Num("7"))})
    result = reconcile.reconcile(actor, sheet, ancestor=ancestor)

    assert [(d.name, c.field) for d, c in result.conflicts] == [("Arrow", "quantity")]
    conflict = _change(result, "Arrow", "quantity")
    assert not conflict.applicable
    assert '"7"' in conflict.blocked and '"4"' in conflict.blocked
    assert '"10"' in conflict.blocked, "the reader needs the value both sides left"

    outcome = apply.apply(result, sheet, include_lossy=True)
    assert str(sheet.by_tid[ARROW].data["quantity"]) == "7", "the GM's value stands"
    assert any(field == "quantity" for _, field, _ in outcome.skipped)


def test_notes_are_judged_against_what_gga_would_have_rendered(ancestor):
    """A note is a rendering, not a value (docs/04-mapping.md 4.4), so "did the
    export move?" has to be asked of the reconstruction, not the raw string."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    sheet = _edited(**{GOOD_REPUTATION: ("local_notes", "Rewritten in GCS entirely.")})
    result = reconcile.reconcile(actor, sheet, ancestor=ancestor)

    assert _change(result, "Good Reputation", "local_notes") is None
    assert ("Good Reputation", "local_notes") in [
        (d.name, c.field) for d, c in result.superseded
    ]


def test_without_an_ancestor_nothing_changes(played):
    """The two-way path has to stay exactly as it was, since it is what runs
    whenever a sheet was never remembered."""
    assert played.superseded == []
    assert played.conflicts == []
    assert [d.name for d in played.changed_rows]


def test_a_row_the_ancestor_never_had_falls_back_to_two_way(ancestor):
    """A row added to the sheet after the import has no ancestor value, so
    there is nothing to be clever with — and being silently dropped would be
    the worst answer."""
    actor = foundry.load(DIR / "container-played.foundry.json")
    sheet = _edited(**{STEALTH: ("points", Num("12"))})
    trimmed = gcs.load(DIR / "container.gcs")
    del trimmed.by_tid[STEALTH]

    result = reconcile.reconcile(actor, sheet, ancestor=trimmed)
    assert _change(result, "Stealth", "points") is not None
    assert result.superseded == []


def test_synthesize_is_never_flagged():
    """Mode B merges against an empty sheet stamped 'now', which would
    otherwise trip the check on every single run."""
    from json2gcs import synthesize  # noqa: PLC0415

    _, result, _ = synthesize.synthesize(foundry.load(DIR / "container.foundry.json"))
    assert not any("after Foundry imported it" in w for w in result.warnings)
