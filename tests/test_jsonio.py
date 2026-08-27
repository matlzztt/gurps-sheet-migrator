"""Byte-exact round-trip tests for the GCS reader/writer.

The headline test is :func:`test_roundtrip_is_byte_identical`: read a file GCS
itself wrote, write it back, and require the bytes to match exactly.  Passing it
across every available fixture is what makes the writer trustworthy enough to
build the converter on top of (docs/06-architecture.md 6.8, step 2).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from json2gcs import jsonio
from json2gcs.jsonio import Num, dumps, format_number, loads

REPO = Path(__file__).resolve().parent.parent

# Files GCS wrote itself. The two upstream ones are vendored into
# samples/upstream/ rather than read from the gcs/ clone: that clone is checked
# out with core.autocrlf, so its working tree has CRLF and would fail the
# byte-exactness test for reasons that have nothing to do with our writer.
FIXTURES = [
    REPO / "samples" / "sturm" / "sturm.gcs",
    REPO / "samples" / "upstream" / "issue767.gcs",
    REPO / "samples" / "upstream" / "container_with_own_data.eqp",
]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_roundtrip_is_byte_identical(path: Path):
    original = path.read_bytes()
    assert not original.startswith(b"\xef\xbb\xbf"), "GCS never writes a BOM"

    text = jsonio.read_text(path)
    rewritten = dumps(loads(text)).encode("utf-8")

    assert rewritten == original


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_format_contract(path: Path):
    """The properties docs/02-gcs-format.md 2.1 pins down."""
    raw = path.read_bytes()
    assert b"\r\n" not in raw, "line endings must be LF"
    assert raw.endswith(b"\n"), "exactly one trailing newline"
    assert not raw.endswith(b"\n\n")
    text = raw.decode("utf-8")
    assert "\n\t" in text, "tab indent"
    assert "\n  " not in text, "no space indent"


def test_sturm_is_the_documented_shape():
    """Guards the claims docs/05-fidelity.md makes about the fixture."""
    sheet = loads(jsonio.read_text(REPO / "samples" / "sturm" / "sturm.gcs"))
    assert sheet["version"] == 5
    assert sheet["id"].startswith("A")
    assert len(sheet["traits"]) == 22
    assert len(sheet["skills"]) == 24
    assert len(sheet["equipment"]) == 23
    assert len(sheet["other_equipment"]) == 3
    # Completely flat: no containers anywhere (docs/05-fidelity.md 5.6).
    for section in ("traits", "skills", "equipment", "other_equipment", "notes"):
        for row in sheet[section]:
            assert "children" not in row
            assert row["id"][0].islower()


def test_container_fixture_has_real_nesting():
    """The upstream fixture that covers the gap our own sample leaves."""
    path = REPO / "samples" / "upstream" / "container_with_own_data.eqp"
    rows = loads(jsonio.read_text(path))["rows"]
    parent = rows[0]
    assert parent["id"].startswith("E"), "container TIDs are uppercase"
    assert parent["children"][0]["id"].startswith("e"), "leaf TIDs are lowercase"
    # A container carrying its own weapons and modifiers, not just children.
    assert parent["weapons"] and parent["modifiers"]


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    ["0", "-2", "6", "0.25", "2.25", "1.5", "100", "-0.5", "209", "0.05"],
)
def test_number_literals_survive(literal: str):
    assert dumps({"n": Num(literal)}) == '{\n\t"n": ' + literal + "\n}\n"


def test_num_compares_by_value():
    assert Num("6") == Num("6.0") == 6
    assert Num("0.25") == Decimal("0.25")
    assert not Num("0")
    assert Num("1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.25"), "0.25"),
        (Decimal("6.0"), "6"),
        (Decimal("100"), "100"),
        (Decimal("-2"), "-2"),
        (Decimal("0.1000"), "0.1"),
        (6, "6"),
        (0.25, "0.25"),
    ],
)
def test_format_number(value, expected):
    assert format_number(value) == expected


def test_no_float_drift():
    """0.1 + 0.2 must not leak binary float noise into a sheet."""
    assert format_number(Num("0.1").value + Num("0.2").value) == "0.3"


# --------------------------------------------------------------------------
# strings
# --------------------------------------------------------------------------


def test_only_required_escapes():
    """GCS does not HTML-escape; '<', '>' and '&' appear raw in real files."""
    assert dumps("Parry & Block <x>") == '"Parry & Block <x>"\n'


def test_quote_and_newline_escaped():
    assert dumps('say "hi"\nbye') == '"say \\"hi\\"\\nbye"\n'


def test_non_ascii_is_raw():
    assert dumps({"st": "10\u2020", "name": "St\u00fcrm"}) == (
        '{\n\t"st": "10\u2020",\n\t"name": "St\u00fcrm"\n}\n'
    )


def test_control_characters_use_short_forms():
    assert dumps("a\tb\rc\x00d") == '"a\\tb\\rc\\u0000d"\n'


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------


def test_key_order_is_preserved_not_sorted():
    text = '{\n\t"version": 5,\n\t"id": "A1",\n\t"age": "19"\n}\n'
    assert dumps(loads(text)) == text


def test_empty_collections_are_inline():
    assert dumps({"a": {}, "b": []}) == '{\n\t"a": {},\n\t"b": []\n}\n'


def test_nesting_indents_with_tabs():
    assert dumps({"a": [{"b": 1}]}) == (
        '{\n\t"a": [\n\t\t{\n\t\t\t"b": 1\n\t\t}\n\t]\n}\n'
    )


def test_write_uses_lf_on_every_platform(tmp_path: Path):
    target = tmp_path / "out.gcs"
    jsonio.dump(target, {"a": "x\ny"})
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw == b'{\n\t"a": "x\\ny"\n}\n'


def test_bom_is_stripped_on_read(tmp_path: Path):
    target = tmp_path / "bom.gcs"
    target.write_bytes(b"\xef\xbb\xbf" + b'{\n\t"a": 1\n}\n')
    assert loads(jsonio.read_text(target)) == {"a": Num("1")}


def test_rejects_unserializable():
    with pytest.raises(TypeError):
        dumps({"a": object()})
