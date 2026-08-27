"""Tests for the Foundry actor export reader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from json2gcs import foundry, tid

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "samples" / "sturm" / "sturm.foundry.json"


@pytest.fixture(scope="module")
def actor() -> foundry.Actor:
    return foundry.load(SAMPLE)


def test_reads_the_sample_cleanly(actor: foundry.Actor):
    assert actor.name == "Stürm"
    assert actor.system_version == "0.18.13"
    assert actor.core_version == "13.351"
    assert actor.warnings == []


def test_counts_match_the_documented_shape(actor: foundry.Actor):
    assert len(actor.traits) == 22
    assert len(actor.skills) == 21
    assert len(actor.carried) == 23
    assert len(actor.other) == 3
    assert len(actor.notes) == 1
    assert actor.spells == []
    assert len(actor.melee()) == 8
    assert len(actor.ranged()) == 3


def test_every_row_has_a_valid_tid(actor: foundry.Actor):
    for row in actor.rows():
        assert row.tid is not None, f"{row.name} has no uuid"
        assert tid.is_valid(row.tid), f"{row.name} has invalid TID {row.tid}"


def test_tid_index_covers_every_row(actor: foundry.Actor):
    rows = list(actor.rows())
    assert len(actor.by_tid) == len(rows) == 70


def test_sample_is_flat(actor: foundry.Actor):
    """docs/05-fidelity.md 5.6 — no containers anywhere in this fixture."""
    for row in actor.rows():
        assert not row.is_container
        assert row.parent_tid is None
        assert not tid.is_container(row.tid)


def test_carried_flag(actor: foundry.Actor):
    assert all(r.carried is True for r in actor.carried)
    assert all(r.carried is False for r in actor.other)
    assert {r.name for r in actor.other} == {
        "Antler comb",
        "The heavy roll",
        "The stores",
    }


def test_gcs_name_and_display_name_are_distinct(actor: foundry.Actor):
    """GGA appends the level to 'name' and leaves 'originalName' as GCS had it."""
    row = actor.by_tid["t89rhDVCsi9fR6yJu"]
    assert row.gcs_name == "Good Reputation"
    assert row.display_name == "Good Reputation 3"
    assert row.name == row.display_name


def test_techniques_keep_their_q_prefix(actor: foundry.Actor):
    techniques = [r for r in actor.skills if r.tid.startswith("q")]
    assert len(techniques) == 2
    assert all(r.data["type"] == "TECHNIQUE" for r in techniques)
    # A 'q' TID in the skills collection is correct, not a mismatch.
    assert actor.warnings == []


def test_import_name_points_at_the_base_file(actor: foundry.Actor):
    assert actor.import_name == "Stürm.gcs"
    assert actor.last_import == "Aug 26 2026 20:13:18"


def test_nothing_added_in_foundry(actor: foundry.Actor):
    assert not any(r.added_in_foundry for r in actor.rows())


# --------------------------------------------------------------------------
# synthetic cases the sample cannot cover
# --------------------------------------------------------------------------


def _minimal(system: dict) -> str:
    return json.dumps({"name": "T", "type": "character", "system": system})


def test_nested_rows_are_indexed_and_linked():
    parent, child = "EAAAAAAAAAAAAAAAA", "eAAAAAAAAAAAAAAAB"
    actor = foundry.loads(
        _minimal(
            {
                "equipment": {
                    "carried": {
                        "00000": {
                            "uuid": parent,
                            "name": "Pack",
                            "contains": {
                                "00000": {
                                    "uuid": child,
                                    "name": "Rope",
                                    "contains": {},
                                }
                            },
                        }
                    }
                }
            }
        )
    )
    assert actor.warnings == []
    top = actor.carried[0]
    assert top.is_container and top.tid == parent
    kid = top.children[0]
    assert kid.tid == child
    assert kid.parent_tid == parent
    assert kid.carried is True
    assert set(actor.by_tid) == {parent, child}
    assert [r.tid for r in actor.rows()] == [parent, child]
    # 'contains' is consumed into children, not duplicated into the row data.
    assert "contains" not in top.data


def test_collection_order_follows_the_padded_keys():
    rows = {
        f"{i:05d}": {"uuid": "s" + f"{i:016d}", "name": f"S{i}"} for i in range(12)
    }
    actor = foundry.loads(_minimal({"skills": rows}))
    assert [r.name for r in actor.skills] == [f"S{i}" for i in range(12)]


def test_row_without_uuid_is_flagged_as_foundry_only():
    actor = foundry.loads(
        _minimal({"skills": {"00000": {"name": "Homebrew", "save": True}}})
    )
    row = actor.skills[0]
    assert row.tid is None
    assert row.added_in_foundry
    assert actor.by_tid == {}


def test_invalid_uuid_warns_and_degrades():
    actor = foundry.loads(
        _minimal({"skills": {"00000": {"uuid": "not-a-tid", "name": "Odd"}}})
    )
    assert actor.skills[0].tid is None
    assert any("not a valid GCS TID" in w for w in actor.warnings)


def test_wrong_kind_prefix_warns():
    actor = foundry.loads(
        _minimal({"skills": {"00000": {"uuid": "e" + "A" * 16, "name": "Wrong"}}})
    )
    assert any("expected 's'" in w for w in actor.warnings)


def test_duplicate_tid_warns():
    dup = "s" + "A" * 16
    actor = foundry.loads(
        _minimal(
            {
                "skills": {
                    "00000": {"uuid": dup, "name": "One"},
                    "00001": {"uuid": dup, "name": "Two"},
                }
            }
        )
    )
    assert any("duplicate TID" in w for w in actor.warnings)


def test_foundry_items_mode_is_refused_loudly():
    raw = json.dumps(
        {
            "name": "T",
            "type": "character",
            "system": {},
            "items": [{"name": "An Item"}],
        }
    )
    actor = foundry.loads(raw)
    assert any("use Foundry items" in w for w in actor.warnings)


def test_unknown_system_version_warns():
    raw = json.dumps(
        {
            "name": "T",
            "type": "character",
            "system": {},
            "_stats": {"systemVersion": "0.19.0"},
        }
    )
    assert any("not been validated" in w for w in foundry.loads(raw).warnings)


def test_wrong_document_type_warns():
    actor = foundry.loads(json.dumps({"name": "T", "type": "npc", "system": {}}))
    assert any("actor type" in w for w in actor.warnings)


def test_rejects_non_actor_json():
    with pytest.raises(ValueError, match="does not look like a Foundry actor"):
        foundry.loads('{"hello": "world"}')
    with pytest.raises(ValueError, match="top level"):
        foundry.loads("[]")


def test_bom_tolerated(tmp_path: Path):
    target = tmp_path / "a.json"
    target.write_bytes(b"\xef\xbb\xbf" + _minimal({}).encode("utf-8"))
    assert foundry.load(target).name == "T"
