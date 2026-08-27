"""The field policy — which Foundry values may be written back, and how.

This is the table from docs/04-mapping.md expressed as data rather than as
branches inside the transform (docs/06-architecture.md 6.2).  Keeping it
declarative means the policy can be reviewed against the documentation without
reading the reconciler, and `--explain` can say why any field was or was not
written.

Fields that are ❌ (unrecoverable) or 🗑️ (derived) in the documentation simply do
not appear here.  Absence is the policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable

from .jsonio import Num

__all__ = ["Fidelity", "Compare", "Rule", "RULES", "expected_display_name", "values_equal"]


class Fidelity(Enum):
    """How much to trust a value coming back from Foundry."""

    EXACT = "exact"
    """Round-trips unchanged; safe to write."""

    DERIVED = "derivable"
    """Needs a deterministic computation, but is exact once computed."""

    LOSSY = "lossy"
    """A best-effort reconstruction. Reported, but never applied silently."""


class Compare(Enum):
    """How to decide whether a field actually changed."""

    EXACT = "exact"
    NUMBER = "number"
    """Compare numerically, so "10" and 10 and Num("10.0") all match."""

    TEXT = "text"
    """Collapse runs of whitespace first.

    Essential for notes: GGA re-indents them on every save cycle without the
    text changing (docs/05-fidelity.md 5.7), and a literal comparison would
    report an edit every single round trip.
    """

    BOOL = "bool"

    QUANTITY = "quantity"
    """Compare magnitudes, ignoring a trailing unit.

    GCS writes ``"2.25 lb"``; GGA stores the same weight as ``"2.25"`` because
    it divides ``calc.extended_weight`` by the quantity and keeps the number.
    Comparing literally would report every single item as re-weighed.
    """


_MISSING = object()


@dataclass(frozen=True)
class Rule:
    """One writable GCS field and where its value comes from."""

    gcs: str
    """The GCS field name."""

    label: str
    """A human-readable name for reports."""

    read: Callable[[Any, dict], Any]
    """``(foundry_row, base_data) -> proposed value``."""

    fidelity: Fidelity = Fidelity.EXACT
    compare: Compare = Compare.EXACT

    gga_default: Any = _MISSING
    """A value GGA injects where GCS omitted the field.

    When the proposal equals this *and* the base row has no such field, writing
    it would add a field the sheet never had (docs/05-fidelity.md 5.3).
    """

    blocks_on_modifiers: bool = False
    """If set, the value is modifier-contaminated and must not be applied to a
    row that has modifiers (docs/05-fidelity.md 5.4)."""

    note: str = ""


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------


def _text(key: str) -> Callable[[Any, dict], Any]:
    def read(row, _base):
        value = row.data.get(key)
        return "" if value is None else str(value)

    return read


def _number(key: str) -> Callable[[Any, dict], Any]:
    def read(row, _base):
        return _to_num(row.data.get(key))

    return read


def _flag(key: str) -> Callable[[Any, dict], Any]:
    def read(row, _base):
        return bool(row.data.get(key))

    return read


def _to_num(value: Any) -> Num | None:
    if value is None or value == "":
        return None
    if isinstance(value, Num):
        return value
    try:
        return Num(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _read_name(row, base: dict) -> str:
    """The name to write back, or the base name when nothing was renamed."""
    expected = expected_display_name(row, base)
    if row.display_name == expected:
        return base.get("description" if row.gcs_section.endswith("equipment") else "name", "")
    return row.display_name


def _read_carried(row, _base) -> bool:
    return bool(row.carried)


def apply_replacements(text: str, replacements: dict | None) -> str:
    """Substitute ``@Key@`` placeholders the way GGA does on import."""
    if not text or not replacements:
        return text or ""
    for key, value in replacements.items():
        text = text.replace(f"@{key}@", str(value))
    return text


def expected_notes(entry) -> str | None:
    """The ``notes`` string GGA would have produced from this base row.

    ``importAd`` and ``importEq`` glue the local notes, every *enabled*
    modifier's name, and the user description into one field.  Comparing
    Foundry's notes against this reconstruction rather than against
    ``local_notes`` alone is what stops every modifier-bearing row reporting a
    phantom edit on every round trip (docs/04-mapping.md 4.4).

    Returns ``None`` when the reconstruction cannot be trusted — currently only
    for self-control rows, where GGA substitutes a localized ``[CR: name]``
    string we cannot reproduce.
    """
    if entry.data.get("cr"):
        return None

    replacements = entry.data.get("replacements")
    notes = apply_replacements(entry.data.get("local_notes") or "", replacements)

    for modifier in entry.data.get("modifiers") or ():
        if not isinstance(modifier, dict) or modifier.get("disabled"):
            continue
        name = apply_replacements(modifier.get("name") or "", replacements)
        if not name:
            continue
        # GGA reads modifier.notes, but GCS v5 writes local_notes, so the
        # parenthetical it would add is always absent in practice.
        notes += ("; " if notes else "") + name

    userdesc = apply_replacements(entry.data.get("userdesc") or "", replacements)
    if userdesc:
        notes += ("\n" if notes else "") + userdesc
    return notes


def expected_display_name(row, base: dict) -> str:
    """What Foundry's ``name`` would read if nobody had renamed the row.

    ``originalName`` is written once at import and never updated, so it already
    carries every decoration GGA applies — the composed specialization for
    skills, the resolved-and-sometimes-mangled suffix for techniques.  The one
    thing it lacks is a trait's appended level.

    Deriving it this way rather than replaying GGA's own composition keeps the
    check version-independent: GGA 0.18.13 renders a technique's base skill as
    ``([object Object])`` and 0.18.22 renders it correctly, and neither affects
    this comparison.
    """
    original = row.data.get("originalName") or ""
    if row.gcs_section == "traits":
        levels = base.get("levels")
        if levels:
            return f"{original} {int(Decimal(str(levels)))}"
    return original


# --------------------------------------------------------------------------
# the tables
# --------------------------------------------------------------------------

_NOTES = Rule(
    gcs="local_notes",
    label="notes",
    read=_text("notes"),
    fidelity=Fidelity.LOSSY,
    compare=Compare.TEXT,
    note="GGA concatenates modifier names and re-indents on every save",
)

_REFERENCE = Rule(gcs="reference", label="page ref", read=_text("pageref"))

RULES: dict[str, tuple[Rule, ...]] = {
    "traits": (
        Rule(gcs="name", label="name", read=_read_name),
        Rule(
            gcs="levels",
            label="levels",
            read=_number("level"),
            compare=Compare.NUMBER,
        ),
        _REFERENCE,
        _NOTES,
    ),
    "skills": (
        Rule(gcs="name", label="name", read=_read_name),
        Rule(
            gcs="points",
            label="points",
            read=_number("points"),
            compare=Compare.NUMBER,
            note="the raw GCS input, not a computed value",
        ),
        _REFERENCE,
        _NOTES,
    ),
    "spells": (
        Rule(gcs="name", label="name", read=_read_name),
        Rule(
            gcs="points", label="points", read=_number("points"), compare=Compare.NUMBER
        ),
        _REFERENCE,
        _NOTES,
    ),
    "equipment": (
        Rule(gcs="description", label="name", read=_read_name),
        Rule(
            gcs="quantity",
            label="quantity",
            read=_number("count"),
            compare=Compare.NUMBER,
        ),
        Rule(
            gcs="equipped",
            label="equipped",
            read=_flag("equipped"),
            compare=Compare.BOOL,
        ),
        _REFERENCE,
        Rule(gcs="tech_level", label="TL", read=_text("techlevel"), gga_default=""),
        Rule(
            gcs="legality_class",
            label="LC",
            read=_text("legalityclass"),
            gga_default="4",
            note="GGA defaults absent LC to '4'",
        ),
        Rule(
            gcs="uses",
            label="uses",
            read=_number("uses"),
            compare=Compare.NUMBER,
            gga_default=0,
        ),
        Rule(
            gcs="max_uses",
            label="max uses",
            read=_number("maxuses"),
            compare=Compare.NUMBER,
            gga_default=0,
        ),
        Rule(
            gcs="base_value",
            label="value",
            read=_text("cost"),
            fidelity=Fidelity.LOSSY,
            compare=Compare.QUANTITY,
            gga_default="0",
            blocks_on_modifiers=True,
            note="Foundry stores the post-modifier value, GCS wants the base",
        ),
        Rule(
            gcs="base_weight",
            label="weight",
            read=_text("weight"),
            fidelity=Fidelity.LOSSY,
            compare=Compare.QUANTITY,
            gga_default="0",
            blocks_on_modifiers=True,
            note="post-modifier, and the unit suffix is dropped",
        ),
        _NOTES,
    ),
    "notes": (
        Rule(
            gcs="markdown",
            label="text",
            read=_text("notes"),
            fidelity=Fidelity.LOSSY,
            compare=Compare.TEXT,
            note="GGA re-indents on every save",
        ),
        _REFERENCE,
    ),
}
RULES["other_equipment"] = RULES["equipment"]

#: Fields that decide which list an equipment row lives in rather than its
#: contents. Handled separately because a move between lists is a relocation,
#: not a field edit.
CARRIED_READER = _read_carried


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


_QUANTITY = re.compile(r"^\s*([+-]?[\d,]*\.?\d+)\s*([a-zA-Z]*)\s*$")


def _magnitude(value: Any):
    """The numeric part of a quantity like ``"2.25 lb"``, or None."""
    if value is None:
        return None
    match = _QUANTITY.match(str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def values_equal(a: Any, b: Any, how: Compare) -> bool:
    """Compare a base value against a proposal under the rule's semantics."""
    if how is Compare.QUANTITY:
        ma, mb = _magnitude(a), _magnitude(b)
        if ma is None or mb is None:
            return _normalize(a) == _normalize(b)
        return ma == mb
    if how is Compare.NUMBER:
        na, nb = _to_num(a), _to_num(b)
        if na is None or nb is None:
            return na is nb or (na is None and nb is None)
        return na.value == nb.value
    if how is Compare.BOOL:
        return bool(a) == bool(b)
    if how is Compare.TEXT:
        return _normalize(a) == _normalize(b)
    if isinstance(a, Num) or isinstance(b, Num):
        na, nb = _to_num(a), _to_num(b)
        if na is not None and nb is not None:
            return na.value == nb.value
    return a == b


def _normalize(value: Any) -> str:
    """Collapse whitespace so re-indentation does not read as an edit."""
    if value is None:
        return ""
    return _WHITESPACE.sub(" ", str(value)).strip()
