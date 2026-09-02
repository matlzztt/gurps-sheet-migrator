"""Match a Foundry export against a base GCS sheet and report what changed.

This is step 3 of docs/06-architecture.md 6.8, and it writes nothing.  Producing
an honest, reviewable account of what a session changed is useful on its own —
and getting it right is a precondition for applying anything.

Matching is by TID, never by name.  Each row lands in one of four states:

``MATCHED``   in both; the field policy decides what actually differs
``ADDED``     in the export only — created inside Foundry, needs a fresh TID
``MISSING``   in the base sheet only — **ambiguous**, see :class:`Status`
``MOVED``     matched, but its parent or its carried/other list changed

Cascades are collapsed: un-equipping a container clears ``equipped`` on
everything inside it (docs/05-fidelity.md 5.7), and reporting that as four
independent edits would be technically true and practically useless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from . import fields as policy
from . import foundry, gcs, schema
from .fields import Compare, Fidelity
from .jsonio import Num

__all__ = ["Status", "Change", "RowDelta", "Reconciliation", "reconcile"]


class Status(Enum):
    MATCHED = "matched"
    ADDED = "added in Foundry"
    MISSING = "missing from the export"
    """Present in the base sheet but not the export.

    Genuinely ambiguous: the row was either deleted in Foundry or added to the
    GCS sheet after the export was taken.  Nothing in either file distinguishes
    them, so this is reported and never acted on by default.
    """


@dataclass
class Change:
    """One field that differs between the base sheet and the export."""

    field: str
    label: str
    old: Any
    new: Any
    fidelity: Fidelity = Fidelity.EXACT
    blocked: str = ""
    """Non-empty if the value must not be applied automatically, and why."""

    note: str = ""

    @property
    def applicable(self) -> bool:
        return not self.blocked and self.fidelity is not Fidelity.LOSSY


@dataclass
class RowDelta:
    """The verdict on one row."""

    tid: str
    section: str
    name: str
    status: Status
    changes: list[Change] = field(default_factory=list)
    superseded: list[Change] = field(default_factory=list)
    """Differences the export has but the sheet has already moved past.

    Only ever populated with an ancestor to compare against: these are the
    fields a two-way merge would have reverted (docs/06-architecture.md 6.9).
    """

    row: foundry.Row | None = None
    entry: gcs.Entry | None = None
    moved: bool = False
    """True if the export puts this row somewhere other than where it is.

    Distinct from ``moved_to``, which is ``None`` both when nothing moved and
    when the row moved out to the top level of its section.
    """

    moved_from: str | None = None
    moved_to: str | None = None
    """Where the row was and where the export puts it: a parent TID when the
    container changed, otherwise a section name.  ``None`` for a parent means
    the top level of the section."""

    moved_from_label: str = ""
    moved_to_label: str = ""
    """The same two places, named for a human to read."""

    move_before: list[str] = field(default_factory=list)
    """TIDs of the rows following this one at its destination in the export,
    nearest first.  :mod:`json2gcs.apply` re-inserts before the first of them
    that is actually there, which reproduces the export's ordering without
    depending on rows the sheet may not have."""

    cascade_from: str | None = None
    """Set when this row's change was a side effect of a change to an ancestor."""

    order: int = -1
    """Where the row sits in the export, depth-first.

    ``deltas`` is sorted for reading; this preserves the order the player
    actually has, and because the walk is depth-first it also guarantees a
    container is seen before anything inside it.  :mod:`json2gcs.apply` needs
    both when it creates rows.
    """

    @property
    def interesting(self) -> bool:
        return self.status is not Status.MATCHED or bool(self.changes) or self.moved


@dataclass
class Reconciliation:
    """The full result. Read-only: nothing here has been written anywhere."""

    deltas: list[RowDelta] = field(default_factory=list)
    profile: list[Change] = field(default_factory=list)
    attributes: list[Change] = field(default_factory=list)
    points: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_status(self, status: Status) -> list[RowDelta]:
        return [d for d in self.deltas if d.status is status]

    @property
    def changed_rows(self) -> list[RowDelta]:
        return [d for d in self.deltas if d.status is Status.MATCHED and d.interesting]

    @property
    def superseded(self) -> list[tuple[RowDelta, Change]]:
        """Every field the sheet has moved past since the export was taken.

        Empty without an ancestor — which is not the same as "there were
        none", only that nothing could tell.
        """
        return [(d, c) for d in self.deltas for c in d.superseded]

    @property
    def conflicts(self) -> list[tuple[RowDelta, Change]]:
        """Fields both sides changed. Reported, never applied."""
        return [
            (d, c)
            for d in self.deltas
            for c in d.changes
            if c.blocked.startswith("changed on both sides")
        ]

    @property
    def blocked(self) -> list[tuple[RowDelta, Change]]:
        """Every change that was found but must not be applied automatically."""
        return [
            (d, c) for d in self.deltas for c in d.changes if c.blocked or not c.applicable
        ]

    def summary(self) -> dict[str, int]:
        return {
            "matched": sum(1 for d in self.deltas if d.status is Status.MATCHED),
            "changed": len(self.changed_rows),
            "added": len(self.by_status(Status.ADDED)),
            "missing": len(self.by_status(Status.MISSING)),
            "moved": sum(1 for d in self.deltas if d.moved),
            "sheet": len(self.profile) + len(self.attributes) + len(self.points),
        }

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.changed_rows,
                self.by_status(Status.ADDED),
                self.by_status(Status.MISSING),
                self.profile,
                self.attributes,
                self.points,
            )
        )


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------


#: The three situations a sheet/export disagreement can turn out to be, once
#: there is an ancestor to consult (docs/06-architecture.md 6.9, phase 3).
_CARRY_BACK = "carry back"
_SUPERSEDED = "superseded"
_CONFLICT = "conflict"


def _unchanged(a: Any, b: Any, how: Compare) -> bool:
    """Whether two values mean the same thing, absence included.

    GCS omits every zero value, so a missing key and a written zero are the
    same statement — and the ancestor comparison sees both constantly.
    """
    if policy.values_equal(a, b, how):
        return True
    return schema.is_zero(a) and schema.is_zero(b)


def _three_way(
    rule: policy.Rule,
    proposed: Any,
    current: Any,
    ancestor: gcs.Entry | None,
) -> str:
    """Classify a disagreement against the sheet as Foundry imported it.

    Two-way merging cannot tell "the player edited this" from "the GM edited
    this in GCS afterwards", and resolves both in Foundry's favour — which
    silently reverts the second (docs/06-architecture.md 6.9).  With the
    ancestor in hand the question is decidable:

    ==========================  ==========================================
    the sheet still has         only Foundry moved   -> carry it back
      the ancestor's value
    the export still has        only the sheet moved -> superseded; the
      the ancestor's value        export is stale here, so leave it alone
    neither matches             both moved           -> a conflict, which
                                                        is for a human
    ==========================  ==========================================

    With no ancestor this returns ``carry back`` for everything, which is
    exactly the old two-way behaviour.
    """
    if ancestor is None:
        return _CARRY_BACK

    was = ancestor.data.get(rule.gcs)
    sheet_moved = not _unchanged(was, current, rule.compare)

    if rule.gcs in ("local_notes", "markdown"):
        # Foundry's notes are a *rendering* of the row (docs/04-mapping.md
        # 4.4), so the question is not whether the strings match but whether
        # GGA would have produced this one from the ancestor.
        rebuilt = policy.expected_notes(ancestor)
        export_moved = rebuilt is None or not policy.values_equal(
            rebuilt, proposed, Compare.TEXT
        )
    else:
        export_moved = not _unchanged(was, proposed, rule.compare)

    if not export_moved:
        return _SUPERSEDED
    if not sheet_moved:
        return _CARRY_BACK
    return _CONFLICT


def _diff_row(
    row: foundry.Row,
    entry: gcs.Entry,
    settings: dict,
    ancestor: gcs.Entry | None = None,
    superseded: list[Change] | None = None,
) -> list[Change]:
    rules = policy.RULES.get(entry.section, ())
    changes: list[Change] = []
    for rule in rules:
        proposed = rule.read(row, entry.data)
        present = rule.gcs in entry.data
        current = entry.data.get(rule.gcs)

        if policy.values_equal(current, proposed, rule.compare):
            continue

        # Notes need comparing against what GGA *would* have built from this
        # row, not against local_notes: GGA appends every enabled modifier's
        # name, so a literal comparison flags every modifier-bearing row on
        # every round trip (docs/04-mapping.md 4.4).
        if rule.gcs in ("local_notes", "markdown"):
            reconstructed = policy.expected_notes(entry)
            if reconstructed is None:
                continue  # a self-control row; we cannot rebuild GGA's string
            if policy.values_equal(reconstructed, proposed, Compare.TEXT):
                continue

        # GGA fills in defaults where GCS omitted the field; writing those back
        # would add fields the sheet never had (docs/05-fidelity.md 5.3).
        if not present and rule.gga_default is not policy._MISSING:
            if policy.values_equal(proposed, rule.gga_default, rule.compare):
                continue
        if not present and proposed in (None, "", [], 0):
            continue

        # Everything above removes differences GGA invented on the way in.
        # What is left is a real disagreement, and only now is it worth asking
        # which side actually moved.
        verdict = _three_way(rule, proposed, current, ancestor)
        if verdict is _SUPERSEDED:
            if superseded is not None:
                superseded.append(
                    Change(
                        field=rule.gcs,
                        label=rule.label,
                        old=current,
                        new=proposed,
                        fidelity=rule.fidelity,
                        note="the sheet was edited here after the export was taken",
                    )
                )
            continue

        blocked = ""
        if verdict is _CONFLICT:
            was = ancestor.data.get(rule.gcs) if ancestor is not None else None
            blocked = (
                f"changed on both sides since the import: the sheet now has "
                f"{_short(current)} and the export {_short(proposed)}, "
                f"both from {_short(was)}"
            )
        elif rule.blocks_on_modifiers and entry.has_modifiers:
            blocked = "row has modifiers, so Foundry's value is post-modifier"
        elif rule.compare is Compare.QUANTITY and _unit_ambiguous(current, settings):
            blocked = (
                f"base value is unitless, so GCS reads it as "
                f"{settings.get('default_weight_units')}; Foundry stores a converted "
                "number and the two are not comparable"
            )
        elif rule.gcs in ("name", "description", "local_notes") and entry.is_nameable:
            blocked = "nameable template — writing the resolved text would orphan replacements"

        changes.append(
            Change(
                field=rule.gcs,
                label=rule.label,
                old=current,
                new=proposed,
                fidelity=rule.fidelity,
                blocked=blocked,
                note=rule.note,
            )
        )
    return changes


def _short(value: Any, limit: int = 40) -> str:
    """A value as a reader can take it in at a glance."""
    if value is None or value == "":
        return "nothing"
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f'"{text}"'


def _unit_ambiguous(current: Any, settings: dict) -> bool:
    """True if a unitless base quantity cannot be compared against Foundry's.

    GCS reads a bare ``base_weight`` in the sheet's ``default_weight_units`` and
    Foundry stores the value already converted for display, so on a metric sheet
    ``"0.1"`` (kg) arrives as ``0.2`` (lb) with nothing having changed.  Where an
    explicit unit is present both sides agree and the comparison is sound.
    """
    if current in (None, ""):
        return False
    if any(ch.isalpha() for ch in str(current)):
        return False
    return str(settings.get("default_weight_units") or "lb").lower() != "lb"


def _collapse_cascades(deltas: dict[str, RowDelta], actor: foundry.Actor) -> None:
    """Attribute a child's ``equipped`` flip to the ancestor that caused it.

    Un-equipping a container clears the flag on every descendant, so a per-row
    diff turns one action into a fan-out.  Marking the descendants keeps the
    report honest about intent without hiding anything.
    """
    for tid, delta in deltas.items():
        if not any(c.field == "equipped" for c in delta.changes):
            continue
        row = delta.row
        if row is None:
            continue
        parent_tid = row.parent_tid
        while parent_tid:
            parent = deltas.get(parent_tid)
            if parent and any(c.field == "equipped" for c in parent.changes):
                delta.cascade_from = parent_tid
                break
            parent_row = actor.by_tid.get(parent_tid)
            parent_tid = parent_row.parent_tid if parent_row else None


def _collapse_move_cascades(
    deltas: dict[str, RowDelta], actor: foundry.Actor
) -> None:
    """Drop the move flag from rows that only moved because a container did.

    Carrying a container across to *other equipment* changes the section of
    everything inside it, but the player made one move, not eleven — and the
    children need no re-attaching, since they travel inside their parent's own
    dict.  Only a section-only change can cascade: a row whose parent TID
    changed was genuinely picked up and put somewhere else.
    """
    for delta in deltas.values():
        row = delta.row
        if not delta.moved or row is None or row.parent_tid != (
            delta.entry.parent_tid if delta.entry else None
        ):
            continue
        parent_tid = row.parent_tid
        while parent_tid:
            parent = deltas.get(parent_tid)
            if parent is not None and parent.moved:
                delta.moved = False
                delta.moved_from = delta.moved_to = None
                delta.moved_from_label = delta.moved_to_label = ""
                delta.move_before = []
                delta.cascade_from = parent_tid
                break
            parent_row = actor.by_tid.get(parent_tid)
            parent_tid = parent_row.parent_tid if parent_row else None


# --------------------------------------------------------------------------
# sheet-level
# --------------------------------------------------------------------------

_PROFILE_MAP = {
    "age": "age",
    "birthday": "birthday",
    "eyes": "eyes",
    "hair": "hair",
    "skin": "skin",
    "gender": "gender",
    "handedness": "hand",
    "height": "height",
    "weight": "weight",
    "player_name": "player",
    "title": "title",
    "religion": "religion",
    "tech_level": "techlevel",
}

#: GCS attr_id -> where its point cost lives in the Foundry export.
_ATTR_POINTS = {
    "st": ("attributes", "ST"),
    "dx": ("attributes", "DX"),
    "iq": ("attributes", "IQ"),
    "ht": ("attributes", "HT"),
    "will": ("attributes", "WILL"),
    "per": ("attributes", "PER"),
    "qn": ("attributes", "QN"),
    "hp": ("HP", None),
    "fp": ("FP", None),
    "qp": ("QP", None),
    "basic_speed": ("basicspeed", None),
    "basic_move": ("basicmove", None),
}


def _diff_profile(
    actor: foundry.Actor, sheet: gcs.Sheet, *, rename: bool = False
) -> list[Change]:
    changes: list[Change] = []
    traits = actor.system.get("traits") or {}
    profile = sheet.profile

    if rename and actor.name and actor.name != profile.get("name"):
        changes.append(
            Change("name", "name", profile.get("name"), actor.name, Fidelity.EXACT)
        )

    for gcs_key, foundry_key in _PROFILE_MAP.items():
        proposed = traits.get(foundry_key)
        if proposed is None:
            continue
        current = profile.get(gcs_key)
        if policy.values_equal(current, proposed, Compare.EXACT):
            continue
        if gcs_key not in profile and proposed == "":
            continue
        changes.append(Change(gcs_key, gcs_key.replace("_", " "), current, proposed))

    sizemod = traits.get("sizemod")
    if sizemod not in (None, ""):
        try:
            proposed_sm = int(str(sizemod).replace("+", "").strip() or 0)
        except ValueError:
            proposed_sm = None
        if proposed_sm is not None:
            current_sm = profile.get("SM")
            if not policy.values_equal(current_sm or 0, proposed_sm, Compare.NUMBER):
                changes.append(Change("SM", "size modifier", current_sm, proposed_sm))
    return changes


def _attr_points(system: dict, attr_id: str) -> Num | None:
    location = _ATTR_POINTS.get(attr_id)
    if location is None:
        return None
    top, key = location
    block = system.get(top) or {}
    if key is not None:
        block = block.get(key) or {}
    raw = block.get("points")
    if raw in (None, ""):
        return None
    try:
        return Num(str(raw).strip())
    except Exception:
        return None


def _diff_attributes(actor: foundry.Actor, sheet: gcs.Sheet) -> list[Change]:
    """Invert Foundry's point totals back into GCS ``adj`` values.

    ``adj = points / cost_per_point`` (docs/04-mapping.md 4.2).  Preferred over
    working back from the displayed value because it is unaffected by
    trait-granted attribute bonuses, which move the value but not the cost.
    """
    changes: list[Change] = []
    defs = sheet.attribute_defs()
    attrs = sheet.attributes()

    for attr_id, current in attrs.items():
        points = _attr_points(actor.system, attr_id)
        definition = defs.get(attr_id)
        if points is None or definition is None:
            continue
        cost = definition.get("cost_per_point")
        if not cost or Decimal(str(cost)) == 0:
            continue
        if int(definition.get("cost_adj_percent_per_sm") or 0) and int(
            sheet.profile.get("SM") or 0
        ) > 0:
            # The SM discount makes the division inexact; leave it alone.
            continue
        proposed = Num(points.value / Decimal(str(cost)))
        existing = current.get("adj", 0)
        if policy.values_equal(existing, proposed, Compare.NUMBER):
            continue
        changes.append(
            Change(
                f"attributes[{attr_id}].adj",
                f"{definition.get('name', attr_id)} adjustment",
                existing,
                proposed,
                Fidelity.DERIVED,
                note=f"{points} points / {cost} per point",
            )
        )

    # Current HP/FP loss, which GCS stores as per-attribute damage.
    for attr_id, block in (("hp", "HP"), ("fp", "FP")):
        pool = actor.system.get(block) or {}
        value, maximum = pool.get("value"), pool.get("max")
        if value is None or maximum is None:
            continue
        try:
            damage = Decimal(str(maximum)) - Decimal(str(value))
        except Exception:
            continue
        current = attrs.get(attr_id, {})
        existing = current.get("damage", 0)
        if policy.values_equal(existing, damage, Compare.NUMBER):
            continue
        changes.append(
            Change(
                f"attributes[{attr_id}].damage",
                f"{block} damage",
                existing,
                Num(damage),
                Fidelity.DERIVED,
                note=f"current {value} of {maximum}",
            )
        )
    return changes


def _import_is_stale(actor: foundry.Actor, sheet: gcs.Sheet) -> str:
    """Warn when the base sheet was edited after Foundry imported from it.

    Merging with ``--base`` is a two-way comparison, and it quietly assumes the
    sheet is still what Foundry imported.  When it is not, every field the
    player did not touch still reads as "unchanged" against a *stale* export,
    so edits made in GCS since the import can be reverted by values nobody
    typed — a lost update with nothing to show for it in the report.

    This cannot be proved from the two timestamps: ``lastImport`` carries no
    zone and ``modified_date`` does, so the comparison is exact only when both
    were written on the same machine.  It therefore reports both times and
    lets the reader judge, rather than asserting a verdict.
    """
    if not sheet.by_tid:
        return ""  # synthesize mode merges against an empty sheet; nothing to lose
    stamp = (actor.last_import or "").strip()
    modified = str(sheet.data.get("modified_date") or "").strip()
    imported = foundry.parse_last_import(stamp)
    if imported is None or not modified:
        return ""
    try:
        edited = datetime.fromisoformat(modified).replace(tzinfo=None)
    except ValueError:
        return ""  # an unexpected format is not evidence of anything
    if edited <= imported:
        return ""
    return (
        f"the base sheet was last written {modified}, after Foundry imported it "
        f"({stamp}). The export is older than the sheet, so anything changed in "
        "GCS since the import can be reverted by values the player never "
        "touched. Read the report before writing, or merge against the sheet as "
        "it was at import time."
    )


def _diff_points(actor: foundry.Actor, sheet: gcs.Sheet) -> list[Change]:
    total = (actor.system.get("totalpoints") or {}).get("total")
    if total is None:
        return []
    if policy.values_equal(sheet.total_points, total, Compare.NUMBER):
        return []
    return [
        Change(
            "total_points",
            "total points",
            sheet.total_points,
            Num(str(total)),
            Fidelity.DERIVED,
            note="GCS also keeps a points_record log the export knows nothing about",
        )
    ]


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


_SECTION_LABEL = {
    "equipment": "carried equipment",
    "other_equipment": "other equipment",
}


def _place_label(
    parent_tid: str | None,
    section: str,
    sheet: gcs.Sheet,
    actor: foundry.Actor | None = None,
) -> str:
    """Name a location a row can sit in, for the report to print."""
    if not parent_tid:
        return _SECTION_LABEL.get(section, section)
    entry = sheet.by_tid.get(parent_tid)
    if entry is not None:
        return entry.name or parent_tid
    row = actor.by_tid.get(parent_tid) if actor is not None else None
    if row is not None:
        return row.display_name or parent_tid
    return parent_tid


def _export_siblings(actor: foundry.Actor, row: foundry.Row) -> list[foundry.Row]:
    """The rows sharing ``row``'s destination in the export, in order."""
    if row.parent_tid:
        parent = actor.by_tid.get(row.parent_tid)
        return parent.children if parent is not None else []
    return {
        "traits": actor.traits,
        "skills": actor.skills,
        "spells": actor.spells,
        "notes": actor.notes,
        "equipment": actor.carried,
        "other_equipment": actor.other,
    }.get(row.gcs_section, [])


def _following_siblings(actor: foundry.Actor, row: foundry.Row) -> list[str]:
    siblings = _export_siblings(actor, row)
    # By identity: two rows with the same content are still two rows.
    position = next((i for i, s in enumerate(siblings) if s is row), None)
    if position is None:
        return []
    return [s.tid for s in siblings[position + 1 :] if s.tid]


def reconcile(
    actor: foundry.Actor,
    sheet: gcs.Sheet,
    *,
    rename: bool = False,
    ancestor: gcs.Sheet | None = None,
) -> Reconciliation:
    """Compare an export against a base sheet. Writes nothing.

    The sheet's own name is left alone unless *rename* is set.  A Foundry actor
    is often named for its folder or token rather than for the character, so
    carrying its name back renames the sheet to something unwanted.

    Pass *ancestor* — the sheet as it was when Foundry imported from it — to
    make this a **three-way** comparison, which is the only way to tell an edit
    made in play from one made in GCS afterwards (docs/06-architecture.md 6.9).
    Without it every disagreement is resolved in the export's favour, which
    silently reverts the second kind.  A row the ancestor does not have simply
    falls back to the two-way answer.
    """
    result = Reconciliation()
    result.warnings.extend(actor.warnings)
    result.warnings.extend(sheet.warnings)
    stale = _import_is_stale(actor, sheet)
    if stale:
        result.warnings.append(stale)

    deltas: dict[str, RowDelta] = {}

    for position, row in enumerate(actor.rows()):
        if row.tid is None or row.tid not in sheet.by_tid:
            deltas[row.tid or f"?{id(row)}"] = RowDelta(
                tid=row.tid or "(none)",
                section=row.gcs_section,
                name=row.display_name,
                status=Status.ADDED,
                row=row,
                order=position,
            )
            continue

        entry = sheet.by_tid[row.tid]
        delta = RowDelta(
            tid=row.tid,
            section=entry.section,
            name=entry.name or row.display_name,
            status=Status.MATCHED,
            row=row,
            entry=entry,
            order=position,
        )
        if row.parent_tid != entry.parent_tid:
            delta.moved = True
            delta.moved_from = entry.parent_tid
            delta.moved_to = row.parent_tid
        elif row.gcs_section != entry.section:
            delta.moved = True
            delta.moved_from = entry.section
            delta.moved_to = row.gcs_section
        if delta.moved:
            delta.moved_from_label = _place_label(entry.parent_tid, entry.section, sheet)
            delta.moved_to_label = _place_label(
                row.parent_tid, row.gcs_section, sheet, actor
            )
            delta.move_before = _following_siblings(actor, row)
        was = ancestor.by_tid.get(row.tid) if ancestor is not None else None
        delta.changes = _diff_row(row, entry, sheet.settings, was, delta.superseded)
        deltas[row.tid] = delta

    for tid, entry in sheet.by_tid.items():
        if tid in deltas:
            continue
        deltas[tid] = RowDelta(
            tid=tid,
            section=entry.section,
            name=entry.name,
            status=Status.MISSING,
            entry=entry,
        )

    _collapse_cascades(deltas, actor)
    _collapse_move_cascades(deltas, actor)

    result.deltas = sorted(
        deltas.values(), key=lambda d: (_SECTION_ORDER.get(d.section, 9), d.name.lower())
    )
    result.profile = _diff_profile(actor, sheet, rename=rename)
    result.attributes = _diff_attributes(actor, sheet)
    result.points = _diff_points(actor, sheet)
    return result


_SECTION_ORDER = {name: i for i, name in enumerate(gcs.SECTIONS)}
