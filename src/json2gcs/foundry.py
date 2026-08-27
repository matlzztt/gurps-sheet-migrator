"""Reader for Foundry VTT's GURPS actor export.

Turns the ``Export Data`` dump described in docs/03-foundry-format.md into a
flat, TID-indexed view that the reconciler can walk.

Two shapes of the source data need normalizing before anything else can happen:

* Collections are objects keyed by a zero-padded counter (``"00000"``), not
  arrays, and those keys renumber whenever rows move.  Identity is ``uuid``.
* Nesting is recorded twice — a child sits inside its parent's ``contains`` and
  also names its ``parentuuid``.  We rebuild one tree and index every row by TID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import tid as tidmod

__all__ = ["Row", "Actor", "load", "loads", "SUPPORTED_SYSTEM_VERSIONS"]

#: GGA versions this reader has been checked against. The sample was exported by
#: 0.18.13; the pinned clone is 0.18.22. GGA's actor schema does move between
#: minor releases, so anything outside this range gets a warning rather than
#: silent best-effort parsing.
SUPPORTED_SYSTEM_VERSIONS = ("0.18",)

#: Foundry collection -> the GCS TID kind its rows should carry.
_SECTION_KINDS = {
    "ads": tidmod.Kind.TRAIT,
    "skills": tidmod.Kind.SKILL,
    "spells": tidmod.Kind.SPELL,
    "notes": tidmod.Kind.NOTE,
    "equipment": tidmod.Kind.EQUIPMENT,
}


@dataclass
class Row:
    """One trait, skill, equipment item or note from the actor."""

    tid: str | None
    """The GCS TID from ``uuid``, or ``None`` for a row added inside Foundry."""

    section: str
    """Which collection it came from: ``ads``, ``skills``, ``spells``,
    ``equipment`` or ``notes``."""

    data: dict[str, Any]
    """The raw Foundry row, minus ``contains``."""

    parent_tid: str | None = None
    children: list["Row"] = field(default_factory=list)
    carried: bool | None = None
    """Equipment only: ``True`` for the carried list, ``False`` for other."""

    @property
    def name(self) -> str:
        """The row's display name, preferring the un-decorated form.

        ``originalName`` is what GCS had; ``name`` has levels appended for
        traits and the resolved base skill appended for techniques.
        """
        return self.data.get("originalName") or self.data.get("name") or ""

    @property
    def added_in_foundry(self) -> bool:
        """True if this row has no GCS counterpart and needs a minted TID."""
        return not self.tid or bool(self.data.get("save"))

    @property
    def is_container(self) -> bool:
        return bool(self.children)

    @property
    def kind(self) -> str | None:
        """The GCS kind name implied by the TID, if it has a valid one."""
        return tidmod.kind_of(self.tid) if self.tid else None

    def walk(self) -> Iterator["Row"]:
        """Yield this row then every descendant, depth-first."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Actor:
    """A parsed Foundry actor export."""

    name: str
    system: dict[str, Any]
    raw: dict[str, Any]

    traits: list[Row] = field(default_factory=list)
    skills: list[Row] = field(default_factory=list)
    spells: list[Row] = field(default_factory=list)
    notes: list[Row] = field(default_factory=list)
    carried: list[Row] = field(default_factory=list)
    other: list[Row] = field(default_factory=list)

    by_tid: dict[str, Row] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def system_version(self) -> str:
        return str(self.raw.get("_stats", {}).get("systemVersion", ""))

    @property
    def core_version(self) -> str:
        return str(self.raw.get("_stats", {}).get("coreVersion", ""))

    @property
    def import_name(self) -> str:
        """The GCS filename this actor was imported from, if recorded.

        ``system.additionalresources.importname`` is how merge mode proposes a
        base file when ``--base`` is omitted.
        """
        return str(self.system.get("additionalresources", {}).get("importname", ""))

    @property
    def last_import(self) -> str:
        return str(self.system.get("lastImport", ""))

    def rows(self) -> Iterator[Row]:
        """Every row in every section, including nested ones."""
        for top in (
            *self.traits,
            *self.skills,
            *self.spells,
            *self.notes,
            *self.carried,
            *self.other,
        ):
            yield from top.walk()

    def melee(self) -> list[dict[str, Any]]:
        """The flat melee list. Detached from owners — see docs/04-mapping 4.6."""
        return _ordered(self.system.get("melee"))

    def ranged(self) -> list[dict[str, Any]]:
        """The flat ranged list. Detached from owners — see docs/04-mapping 4.6."""
        return _ordered(self.system.get("ranged"))


def _ordered(collection: Any) -> list[dict[str, Any]]:
    """Flatten a ``{"00000": {...}}`` collection into a list, in key order.

    Keys are a zero-padded counter, so they sort correctly as strings — but
    only while every key is the same width, which is why this sorts explicitly
    rather than trusting insertion order.
    """
    if not isinstance(collection, dict):
        return []
    return [collection[k] for k in sorted(collection) if isinstance(collection[k], dict)]


def _build_rows(
    collection: Any,
    section: str,
    index: dict[str, Row],
    warnings: list[str],
    *,
    carried: bool | None = None,
    parent_tid: str | None = None,
) -> list[Row]:
    """Recursively turn one Foundry collection into a tree of :class:`Row`."""
    rows: list[Row] = []
    for entry in _ordered(collection):
        uuid = entry.get("uuid") or None
        expected = _SECTION_KINDS.get(section)

        if uuid and not tidmod.is_valid(uuid):
            warnings.append(
                f"{section}: row {entry.get('name', '?')!r} has uuid {uuid!r}, "
                "which is not a valid GCS TID; treating it as Foundry-only"
            )
            uuid = None
        elif uuid and expected and uuid[0].lower() != expected.lower():
            # A technique lives in the skills collection with a 'q' prefix, so
            # compare case-insensitively against the section's leaf kind and
            # only complain when the letter itself is wrong.
            if not (section == "skills" and uuid[0] == tidmod.Kind.TECHNIQUE):
                warnings.append(
                    f"{section}: row {entry.get('name', '?')!r} has TID kind "
                    f"{uuid[0]!r}, expected {expected!r}"
                )

        data = {k: v for k, v in entry.items() if k != "contains"}
        row = Row(
            tid=uuid,
            section=section,
            data=data,
            parent_tid=parent_tid,
            carried=carried,
        )
        row.children = _build_rows(
            entry.get("contains"),
            section,
            index,
            warnings,
            carried=carried,
            parent_tid=uuid,
        )
        if uuid:
            if uuid in index:
                warnings.append(f"{section}: duplicate TID {uuid!r}")
            index[uuid] = row
        rows.append(row)
    return rows


def loads(text: str) -> Actor:
    """Parse a Foundry actor export from JSON text."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object at the top level")

    system = raw.get("system")
    if not isinstance(system, dict):
        raise ValueError(
            "no 'system' object — this does not look like a Foundry actor export"
        )

    actor = Actor(name=str(raw.get("name", "")), system=system, raw=raw)
    warnings = actor.warnings

    if raw.get("type") != "character":
        warnings.append(f"actor type is {raw.get('type')!r}, expected 'character'")

    version = actor.system_version
    if version and not version.startswith(SUPPORTED_SYSTEM_VERSIONS):
        warnings.append(
            f"exported by GURPS system {version}, which this converter has not "
            f"been validated against (expected {'/'.join(SUPPORTED_SYSTEM_VERSIONS)}.x)"
        )

    if raw.get("items"):
        warnings.append(
            f"actor has {len(raw['items'])} Foundry Item document(s): this world "
            "uses GGA's 'use Foundry items' mode, which is not supported — data "
            "held in those items will not be converted"
        )

    index = actor.by_tid
    actor.traits = _build_rows(system.get("ads"), "ads", index, warnings)
    actor.skills = _build_rows(system.get("skills"), "skills", index, warnings)
    actor.spells = _build_rows(system.get("spells"), "spells", index, warnings)
    actor.notes = _build_rows(system.get("notes"), "notes", index, warnings)

    equipment = system.get("equipment") or {}
    actor.carried = _build_rows(
        equipment.get("carried"), "equipment", index, warnings, carried=True
    )
    actor.other = _build_rows(
        equipment.get("other"), "equipment", index, warnings, carried=False
    )
    return actor


def load(path: str | Path) -> Actor:
    """Read and parse a Foundry actor export.

    Foundry writes UTF-8; a BOM is tolerated because some editors add one.
    """
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return loads(raw.decode("utf-8"))
