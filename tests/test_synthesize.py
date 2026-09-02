"""Mode B: a sheet built from a Foundry export alone.

The control export doubles as the fixture here.  It is an export of a character
that *does* have a `.gcs` sheet, which is exactly what makes it useful: the real
sheet is available to compare against, so these tests can ask how much of it a
synthesized sheet recovers, and say honestly what it does not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from json2gcs import cli, foundry, gcs, jsonio, schema, synthesize

REPO = Path(__file__).resolve().parent.parent
DIR = REPO / "samples" / "container"
SHEET = DIR / "container.gcs"
CONTROL = DIR / "container.foundry.json"

STAMP = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def actor() -> foundry.Actor:
    return foundry.load(CONTROL)


@pytest.fixture(scope="module")
def built(actor):
    return synthesize.synthesize(actor, now=STAMP)


def flatten(rows: list) -> list:
    out = []
    for row in rows or []:
        out.append(row)
        out.extend(flatten(row.get("children")))
    return out


# --------------------------------------------------------------------------
# the template
# --------------------------------------------------------------------------


def test_the_template_parses_as_a_sheet():
    sheet = gcs.loads(jsonio.read_text(synthesize.TEMPLATE))
    assert sheet.by_tid == {}
    assert sheet.version == 5
    assert [a["attr_id"] for a in sheet.data["attributes"]][:4] == [
        "st",
        "dx",
        "iq",
        "ht",
    ]
    assert sheet.settings["body_type"]["name"] == "Humanoid"


def test_the_template_is_a_byte_exact_round_trip():
    text = jsonio.read_text(synthesize.TEMPLATE)
    assert jsonio.dumps(jsonio.loads(text)) == text


def test_a_blank_sheet_is_not_a_copy_of_the_template():
    """Two synthesized characters must not share an entity id or a timestamp."""
    one = synthesize.blank_sheet(now=STAMP)
    two = synthesize.blank_sheet(now=STAMP)
    assert one.data["id"] != two.data["id"]
    assert one.data["id"].startswith("A")
    assert one.data["created_date"] == "2026-08-28T12:00:00+00:00"
    assert one.data["created_date"] == one.data["modified_date"]
    template = jsonio.loads(jsonio.read_text(synthesize.TEMPLATE))
    assert one.data["id"] != template["id"]


# --------------------------------------------------------------------------
# what comes out
# --------------------------------------------------------------------------


def test_every_row_in_the_export_reaches_the_sheet(actor, built):
    sheet, _, plan = built
    assert len(plan.added) == len(list(actor.rows())) == 78
    written = [row["id"] for s in gcs.SECTIONS for row in flatten(sheet.data.get(s))]
    assert len(written) == 78
    assert len(set(written)) == 78


def test_rows_keep_the_tids_they_already_had(actor, built):
    """The actor came from a GCS sheet. Reusing its ids means a later merge
    against the recovered sheet still lines up, row for row."""
    sheet, _, _ = built
    assert {row.tid for row in actor.rows() if row.tid} == set(sheet.by_tid)


def test_a_technique_keeps_its_own_kind_of_tid(built):
    """Techniques live in the skills section under a 'q' prefix, and GCS reads
    the row type off that letter — minting an 's' would retype the row."""
    sheet, _, _ = built
    techniques = [s for s in sheet.data["skills"] if s["id"].startswith("q")]
    assert len(techniques) == 2
    assert all(t["difficulty"] == "a" for t in techniques), (
        "GCS's own NewTechnique sets Average, and a technique has no "
        "controlling attribute to name"
    )


def test_containers_keep_their_contents(built):
    sheet, _, _ = built
    backpack = next(
        e for e in sheet.data["equipment"] if e["description"] == "Backpack"
    )
    assert [c["description"] for c in backpack["children"]] == [
        "Metabackpack",
        "Horn-tip and tooth on a cord",
    ]
    meta = backpack["children"][0]
    assert [c["description"] for c in meta["children"]] == ["The Book of Lines"]


def test_rows_are_in_the_export_order_not_the_reports(actor, built):
    """The report sorts alphabetically for reading; the sheet must not."""
    sheet, _, _ = built
    assert [e["description"] for e in sheet.data["equipment"]][:3] == [
        "Backpack",
        "Yarqap",
        "The Bosbash",
    ]
    assert [row["description"] for row in flatten(sheet.data["equipment"])] == [
        row.display_name for top in actor.carried for row in top.walk()
    ]


def test_carried_and_other_equipment_are_separated(built):
    sheet, _, _ = built
    assert len(sheet.data["equipment"]) == 22
    assert [e["description"] for e in sheet.data["other_equipment"]] == [
        "Oequipment Container",
        "The stores",
    ]


def test_an_empty_section_is_not_written(built):
    """Every fixture has no spells, and 'spells' is omitzero."""
    sheet, _, _ = built
    assert "spells" not in sheet.data


def test_the_profile_and_attributes_are_filled_in(built):
    sheet, _, _ = built
    assert sheet.profile["name"] == "Container"
    assert sheet.profile["player_name"] == "Matheus Lazzarotto"
    assert str(sheet.data["total_points"]) == "209"
    adjustments = {a["attr_id"]: a.get("adj") for a in sheet.data["attributes"]}
    assert str(adjustments["dx"]) == "2"


def test_profile_keys_are_in_gcs_order(built):
    """GCS writes handedness before gender, which nothing else does."""
    sheet, _, _ = built
    keys = list(sheet.profile)
    assert keys == sorted(keys, key=schema.profile_key)
    assert keys.index("handedness") < keys.index("gender")


# --------------------------------------------------------------------------
# the fields that are easy to get subtly wrong
# --------------------------------------------------------------------------


def test_a_trait_uses_base_points_not_points(built):
    """'points' is not a trait field. GCS drops it silently, so writing the
    wrong name loses the value rather than failing."""
    sheet, _, _ = built
    trait = next(
        t for t in flatten(sheet.data["traits"]) if t["name"] == "Green Sight"
    )
    assert str(trait["base_points"]) == "15"
    assert "points" not in trait


def test_a_skill_uses_points(built):
    sheet, _, _ = built
    skill = next(s for s in sheet.data["skills"] if s["name"] == "Poisons")
    assert str(skill["points"]) == "8"
    assert "base_points" not in skill


def test_a_container_gets_no_points_of_its_own(built):
    """GCS sums a container's children; a written total would be discarded."""
    sheet, _, _ = built
    container = next(t for t in sheet.data["traits"] if t["name"] == "Trait Container")
    assert "points" not in container and "base_points" not in container
    assert container["children"]


def test_zero_points_are_omitted_not_written(built):
    """base_points is omitzero, so a free trait must carry no key at all."""
    sheet, _, _ = built
    free = [
        t
        for t in flatten(sheet.data["traits"])
        if "base_points" in t and str(t["base_points"]) == "0"
    ]
    assert free == []


def test_a_skill_carries_the_controlling_attribute_it_does_have(built):
    """Foundry keeps relativelevel ('IQ+1'), which names the attribute but not
    the difficulty letter. The attribute half is real and worth keeping."""
    sheet, _, _ = built
    skill = next(s for s in sheet.data["skills"] if s["name"] == "Poisons")
    assert skill["difficulty"] == "iq/e"


def test_equipment_carries_the_fields_the_policy_knows(built):
    """apply._add_row is driven by fields.RULES, so TL/value/weight -- which
    merge mode has always written -- must reach a synthesized sheet too."""
    sheet, _, _ = built
    knife = next(e for e in sheet.data["equipment"] if e["description"] == "Large Knife")
    assert knife["tech_level"] == "0"
    assert str(knife["base_value"]) == "40"
    assert str(knife["base_weight"]) == "1"


def test_equipment_legality_class_is_not_written_when_it_is_ggas_default(built):
    """GGA defaults an absent LC to '4'; every item in this fixture has that
    value, so none of it is real data worth writing."""
    sheet, _, _ = built
    assert not any(
        "legality_class" in row for row in flatten(sheet.data["equipment"])
    )


def test_a_leveled_trait_gets_can_level_too(built):
    """trait.go forces can_level=true on load whenever levels != 0; omitting
    it here just means GCS's rewrite disagrees with what we wrote."""
    sheet, _, _ = built
    trait = next(
        t for t in flatten(sheet.data["traits"]) if t["name"] == "Good Reputation"
    )
    assert trait["levels"] == 3
    assert trait["can_level"] is True


def test_a_traits_level_decoration_is_stripped_from_the_name(built):
    """originalName has no level suffix; name does. Writing the decorated
    string into GCS's name field would duplicate what 'levels' already says."""
    sheet, _, _ = built
    names = [t["name"] for t in flatten(sheet.data["traits"])]
    assert "Bad Reputation" in names
    assert "Bad Reputation 3" not in names


def test_a_skills_specialization_is_decomposed(built):
    """'Esoteric Medicine (Menkhu)' -> name + specialization, not one string
    GCS would treat as an unrecognized skill name (docs/08-improvements 8.2)."""
    sheet, _, _ = built
    skill = next(s for s in sheet.data["skills"] if s["name"] == "Esoteric Medicine")
    assert skill["specialization"] == "Menkhu"


def test_a_technique_name_is_left_alone(built):
    """Techniques are lossy for a separate, already-known reason (8.3); this
    decomposition must not also mangle them further."""
    sheet, _, _ = built
    technique = next(s for s in sheet.data["skills"] if s["id"].startswith("q"))
    assert "specialization" not in technique
    assert "(" in technique["name"]


def test_row_keys_are_in_canonical_order(built):
    sheet, _, _ = built
    for section in gcs.SECTIONS:
        for row in flatten(sheet.data.get(section)):
            keys = list(row)
            assert keys == sorted(keys, key=lambda k: schema.order_key(section, k)), (
                f"{section}: {keys}"
            )


# --------------------------------------------------------------------------
# the output is a sheet
# --------------------------------------------------------------------------


def test_the_output_round_trips_through_our_own_writer(built):
    sheet, _, _ = built
    text = jsonio.dumps(sheet.data)
    assert jsonio.dumps(jsonio.loads(text)) == text
    assert gcs.loads(text).by_tid.keys() == sheet.by_tid.keys()


def test_top_level_keys_are_in_canonical_order(built):
    sheet, _, _ = built
    keys = list(sheet.data)
    order = schema.ENTITY_ORDER
    assert keys == sorted(keys, key=lambda k: order.index(k))


def test_nothing_is_left_for_review_on_a_clean_export(built):
    """There is no base sheet to conflict with, so nothing can be blocked as
    contaminated — the reasons for blocking are all comparisons."""
    _, _, plan = built
    assert [(d.name, f, r) for d, f, r in plan.skipped if "lossy" not in r] == []


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_synthesize_writes_a_sheet(capsys, tmp_path):
    out = tmp_path / "new.gcs"
    code, text = run(capsys, "convert", str(CONTROL), "--synthesize", "-o", str(out))
    assert code == 0
    assert "78 row(s)" in text
    assert "synthesized from the export alone" in text
    assert gcs.load(out).profile["name"] == "Container"


def test_synthesize_and_base_together_are_refused(capsys, tmp_path):
    code = cli.main(
        [
            "convert",
            str(CONTROL),
            "--synthesize",
            "--base",
            str(SHEET),
            "-o",
            str(tmp_path / "x.gcs"),
        ]
    )
    assert code == 2
    assert "drop --base" in capsys.readouterr().err


def test_synthesize_will_not_overwrite_without_force(capsys, tmp_path):
    out = tmp_path / "new.gcs"
    out.write_text("mine", encoding="utf-8")
    code = cli.main(["convert", str(CONTROL), "--synthesize", "-o", str(out)])
    assert code == 2
    assert "refusing to overwrite" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "mine"


def test_synthesize_dry_run_writes_nothing(capsys, tmp_path):
    out = tmp_path / "new.gcs"
    code, text = run(
        capsys, "convert", str(CONTROL), "--synthesize", "-o", str(out), "--dry-run"
    )
    assert code == 0
    assert not out.exists()
    assert "Nothing written" in text


# --------------------------------------------------------------------------
# what mode B costs, stated rather than assumed
# --------------------------------------------------------------------------


def test_what_a_synthesized_sheet_loses_against_the_real_one(built):
    """A record of the gap, not a pass/fail: this is the honest scope of mode B.

    If a later change starts recovering any of these, this test should be
    updated to say so — that would be progress, not a regression.
    """
    sheet, _, _ = built
    real = gcs.load(SHEET)

    def present(source, key: str) -> int:
        return sum(
            1
            for section in gcs.SECTIONS
            for row in flatten(source.data.get(section))
            if row.get(key)
        )

    for key in ("modifiers", "features", "tags", "source", "weapons"):
        assert present(sheet, key) == 0, f"{key} cannot come from a Foundry export"
        assert present(real, key) > 0, f"{key} is in the real sheet, so this is a loss"

    # The settings, by contrast, are GCS's own defaults and are complete.
    assert set(sheet.settings) == set(real.settings)
