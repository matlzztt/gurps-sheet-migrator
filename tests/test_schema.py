"""Validate the transcribed key order against real GCS output.

The orders in :mod:`json2gcs.schema` were copied by hand out of Go struct
definitions, which is exactly the kind of thing that goes quietly wrong.  These
tests check them against every row of every fixture: if GCS wrote key A before
key B anywhere, the table has to agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from json2gcs import gcs, jsonio, schema

REPO = Path(__file__).resolve().parent.parent
SHEETS = [
    REPO / "samples" / "sturm" / "sturm.gcs",
    REPO / "samples" / "container" / "container.gcs",
    REPO / "samples" / "upstream" / "issue767.gcs",
]


def _rows():
    for path in SHEETS:
        sheet = gcs.load(path)
        for entry in sheet.by_tid.values():
            yield path.name, entry


@pytest.mark.parametrize("path", SHEETS, ids=lambda p: p.name)
def test_every_row_follows_the_canonical_order(path: Path):
    """Each row's keys must appear in canonical order — a subsequence check."""
    sheet = gcs.load(path)
    for entry in sheet.by_tid.values():
        keys = list(entry.data.keys())
        ranks = [schema.order_key(entry.section, k) for k in keys]
        assert ranks == sorted(ranks), (
            f"{entry.section}/{entry.name}: {keys} is not in canonical order"
        )


def test_no_row_uses_a_key_the_table_has_never_heard_of():
    """An unknown key means the transcription is missing a field."""
    unknown: set[tuple[str, str]] = set()
    for _, entry in _rows():
        known = schema.FIELD_ORDER[entry.section]
        unknown |= {(entry.section, k) for k in entry.data if k not in known}
    assert unknown == set()


def test_sheet_top_level_follows_the_entity_order():
    for path in SHEETS:
        data = jsonio.loads(jsonio.read_text(path))
        ranks = [
            schema.ENTITY_ORDER.index(k) for k in data if k in schema.ENTITY_ORDER
        ]
        assert ranks == sorted(ranks), path.name
        assert not set(data) - set(schema.ENTITY_ORDER), path.name


def test_equipment_quantity_is_written_even_when_zero():
    """`quantity` is the one row field with no omitzero in the Go tag."""
    assert "quantity" in schema.ALWAYS_WRITTEN["equipment"]
    assert "quantity" not in schema.ALWAYS_WRITTEN["traits"]


def test_zero_detection():
    for value in (None, False, "", [], {}, 0, 0.0, jsonio.Num("0")):
        assert schema.is_zero(value), value
    for value in (True, "x", [1], {"a": 1}, 1, jsonio.Num("0.1"), "lb"):
        assert not schema.is_zero(value), value


def test_a_string_holding_zero_is_not_omitted():
    """`omitzero` on a Go string means empty, not "0".

    `base_value` and `base_weight` are strings, so GCS writes `"0"` happily.
    Treating it as zero would silently drop a deliberate free-of-charge item.
    """
    assert not schema.is_zero("0")
    assert not schema.is_zero("0.0")
    assert schema.is_zero("")


def test_unknown_keys_sort_last_rather_than_being_lost():
    known = schema.order_key("traits", "name")
    unknown = schema.order_key("traits", "something_new")
    assert unknown > known
    assert schema.order_key("traits", "calc") < unknown


@pytest.mark.parametrize(
    ("section", "before", "after"),
    [
        # The pairs most likely to be transcribed wrong, each observed in a fixture.
        ("equipment", "tech_level", "legality_class"),
        ("equipment", "base_value", "base_weight"),
        ("equipment", "weapons", "modifiers"),
        ("equipment", "modifiers", "quantity"),
        ("equipment", "quantity", "equipped"),
        ("equipment", "equipped", "children"),
        ("traits", "replacements", "modifiers"),
        ("traits", "modifiers", "points_per_level"),
        ("traits", "can_level", "levels"),
        ("traits", "children", "calc"),
        ("skills", "defaults", "default"),
        ("skills", "difficulty", "points"),
    ],
)
def test_known_orderings(section: str, before: str, after: str):
    assert schema.order_key(section, before) < schema.order_key(section, after)
