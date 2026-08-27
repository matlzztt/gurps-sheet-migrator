"""A TID-indexed view of a GCS sheet.

Thin wrapper over the parsed JSON: it does not model GCS's schema, it just makes
the sheet walkable and addressable by TID so the reconciler can line rows up
against a Foundry export.  The underlying dicts stay live, so anything written
back lands in the structure :mod:`json2gcs.jsonio` will serialize.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import jsonio
from . import tid as tidmod

__all__ = ["Entry", "Sheet", "load", "loads", "SECTIONS"]

#: The row-bearing top-level keys, in the order GCS writes them.
SECTIONS = ("traits", "skills", "spells", "equipment", "other_equipment", "notes")

#: Where each section's display name lives. Equipment is the odd one out.
NAME_KEY = {
    "traits": "name",
    "skills": "name",
    "spells": "name",
    "equipment": "description",
    "other_equipment": "description",
    "notes": "markdown",
}


@dataclass
class Entry:
    """One row in a GCS sheet."""

    tid: str
    section: str
    data: dict[str, Any]
    parent_tid: str | None = None
    children: list["Entry"] = field(default_factory=list)

    @property
    def name(self) -> str:
        value = self.data.get(NAME_KEY.get(self.section, "name")) or ""
        if self.section == "notes":
            return value.strip().splitlines()[0][:60] if value.strip() else "(note)"
        return value

    @property
    def kind(self) -> str | None:
        return tidmod.kind_of(self.tid)

    @property
    def is_container(self) -> bool:
        return "children" in self.data

    @property
    def has_modifiers(self) -> bool:
        """True if modifiers make this row's computed values untrustworthy.

        Foundry stores post-modifier results in fields GCS treats as inputs
        (docs/05-fidelity.md 5.4), so a row with modifiers must not have its
        cost or weight written back.
        """
        return bool(self.data.get("modifiers"))

    @property
    def is_nameable(self) -> bool:
        """True if the row is a nameable template with ``@…@`` placeholders.

        Writing a resolved name over one of these destroys the template and
        orphans ``replacements`` (docs/05-fidelity.md 5.2).
        """
        if self.data.get("replacements"):
            return True
        return "@" in str(self.data.get(NAME_KEY.get(self.section, "name")) or "")

    def walk(self) -> Iterator["Entry"]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Sheet:
    """A parsed GCS character sheet."""

    data: dict[str, Any]
    by_tid: dict[str, Entry] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def version(self) -> Any:
        return self.data.get("version")

    @property
    def total_points(self) -> Any:
        return self.data.get("total_points")

    @property
    def settings(self) -> dict[str, Any]:
        return self.data.get("settings") or {}

    @property
    def profile(self) -> dict[str, Any]:
        return self.data.get("profile") or {}

    def attribute_defs(self) -> dict[str, dict[str, Any]]:
        """Attribute definitions keyed by their short id (``"st"``, ``"hp"``)."""
        return {
            d["id"]: d
            for d in self.settings.get("attributes") or ()
            if isinstance(d, dict) and isinstance(d.get("id"), str)
        }

    def attributes(self) -> dict[str, dict[str, Any]]:
        """The character's attribute rows keyed by ``attr_id``."""
        return {
            a["attr_id"]: a
            for a in self.data.get("attributes") or ()
            if isinstance(a, dict) and isinstance(a.get("attr_id"), str)
        }

    def rows(self) -> Iterator[Entry]:
        for entry in list(self.by_tid.values()):
            yield entry


def _index(sheet: Sheet) -> None:
    def walk(rows, section: str, parent: str | None) -> list[Entry]:
        built: list[Entry] = []
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            row_tid = row.get("id")
            if not isinstance(row_tid, str) or not tidmod.is_valid(row_tid):
                sheet.warnings.append(
                    f"{section}: row {row.get('name') or row.get('description')!r} "
                    f"has id {row_tid!r}, which is not a valid TID"
                )
                continue
            entry = Entry(tid=row_tid, section=section, data=row, parent_tid=parent)
            entry.children = walk(row.get("children"), section, row_tid)
            if row_tid in sheet.by_tid:
                sheet.warnings.append(f"{section}: duplicate TID {row_tid!r}")
            sheet.by_tid[row_tid] = entry
            built.append(entry)
        return built

    for section in SECTIONS:
        walk(sheet.data.get(section), section, None)


def loads(text: str) -> Sheet:
    data = jsonio.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object at the top level")
    if "profile" not in data and "attributes" not in data:
        raise ValueError("no 'profile' or 'attributes' — this is not a GCS sheet")
    sheet = Sheet(data=data)
    _index(sheet)
    return sheet


def load(path: str | Path) -> Sheet:
    return loads(jsonio.read_text(path))
