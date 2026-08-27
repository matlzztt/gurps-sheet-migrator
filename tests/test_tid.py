"""Tests for TID validation and minting."""

from __future__ import annotations

import pytest

from json2gcs import jsonio, tid
from json2gcs.tid import Kind

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("A0m9Zw_BuLDSOCLwc", "entity"),
        ("t89rhDVCsi9fR6yJu", "trait"),
        ("sa8aSCyDHxDVHKGL2", "skill"),
        ("qOjp2knMpcRJhQ18q", "technique"),
        ("e-dS0zhDCB1-L4rBI", "equipment"),
        ("EAAAAAAAAAAAAAAAA", "equipment_container"),
        ("n1sRyf8TbFfiovjAo", "note"),
        ("WRiXTtjhqHZUMjC6w", "ranged_weapon"),
        ("wAAAAAAAAAAAAAAAA", "melee_weapon"),
        ("Mj0nfAjCFkqJZhzsS", "trait_modifier_container"),
    ],
)
def test_kind_of_real_tids(value: str, kind: str):
    assert tid.is_valid(value)
    assert tid.kind_of(value) == kind


@pytest.mark.parametrize(
    "value",
    [
        "",
        "st",              # an attribute definition id, not a TID
        "basic_move",      # ditto
        "torso",           # a hit location id
        "t89rhDVCsi9fR6yJ",    # 16 chars, one short
        "t89rhDVCsi9fR6yJuX",  # 18 chars
        "z89rhDVCsi9fR6yJu",   # unknown kind prefix
        "t89rhDVCsi9fR6y!u",   # '!' is not base64url
        None,
        42,
    ],
)
def test_rejects_non_tids(value):
    assert not tid.is_valid(value)
    if isinstance(value, str) and value:
        assert tid.kind_of(value) is None


def test_short_ids_in_a_real_sheet_are_not_tids():
    """settings.attributes[].id and body_type location ids must not match."""
    sheet = jsonio.loads(jsonio.read_text(REPO / "samples" / "sturm" / "sturm.gcs"))
    for definition in sheet["settings"]["attributes"]:
        assert not tid.is_valid(definition["id"])
    for location in sheet["settings"]["body_type"]["locations"]:
        assert not tid.is_valid(location["id"])


def test_is_container():
    assert tid.is_container("EAAAAAAAAAAAAAAAA")
    assert not tid.is_container("eAAAAAAAAAAAAAAAA")
    assert not tid.is_container("A0m9Zw_BuLDSOCLwc")  # the entity is not a container


@pytest.mark.parametrize(
    "kind", [Kind.TRAIT, Kind.SKILL, Kind.TECHNIQUE, Kind.EQUIPMENT, Kind.NOTE]
)
def test_mint_produces_valid_tids_of_the_right_kind(kind: str):
    value = tid.mint(kind)
    assert tid.is_valid(value)
    assert value[0] == kind
    assert not tid.is_container(value)


@pytest.mark.parametrize(
    ("leaf", "container"),
    [
        (Kind.TRAIT, "T"),
        (Kind.SKILL, "S"),
        (Kind.EQUIPMENT, "E"),
        (Kind.NOTE, "N"),
        (Kind.SPELL, "P"),
    ],
)
def test_mint_container_uses_the_uppercase_prefix(leaf: str, container: str):
    value = tid.mint(leaf, container=True)
    assert value[0] == container
    assert tid.is_container(value)


def test_mint_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown TID kind"):
        tid.mint("z")


def test_minted_tids_are_unique():
    assert len({tid.mint(Kind.SKILL) for _ in range(2000)}) == 2000
