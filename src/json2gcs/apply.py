"""Apply a reconciliation to a base sheet and produce the merged GCS data.

Merge mode's whole premise is that the base sheet is authoritative for
everything Foundry never knew about (docs/01-problem.md).  So this edits the
base structure in place rather than rebuilding it: modifiers, features,
prereqs, library ``source`` links, attribute definitions and the body plan
survive because nothing here touches them.

Three rules keep the output faithful:

* **Only applicable changes are written.** Anything blocked or lossy is left
  for a human, and reported.
* **Setting a field to its zero value deletes the key**, because that is what
  GCS's ``omitzero`` tags mean.  Un-equipping an item removes ``equipped``
  rather than writing ``false``.
* **New keys are inserted in canonical order** (:mod:`json2gcs.schema`), so the
  output stays diffable against what GCS itself would write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from . import fields as policy
from . import gcs, schema
from . import tid as tidmod
from .jsonio import Num
from .reconcile import Reconciliation, RowDelta, Status

__all__ = ["Plan", "DeletionPolicy", "plan", "apply"]


class DeletionPolicy:
    """What to do with rows in the sheet but not the export."""

    KEEP = "keep"
    """Leave them alone. The safe default: the row may simply have been added
    to the sheet after the export was taken."""

    DROP = "drop"
    """Remove them, trusting that the export is the newer truth."""

    ALL = (KEEP, DROP)


@dataclass
class Plan:
    """What :func:`apply` did, or would do."""

    applied: list[tuple[RowDelta, str]] = field(default_factory=list)
    """(row, field) pairs written."""

    skipped: list[tuple[RowDelta, str, str]] = field(default_factory=list)
    """(row, field, reason) for changes deliberately not written."""

    dropped: list[RowDelta] = field(default_factory=list)
    kept: list[RowDelta] = field(default_factory=list)
    moved: list[RowDelta] = field(default_factory=list)
    """Rows re-attached somewhere else in the sheet."""

    added: list[tuple[RowDelta, str]] = field(default_factory=list)
    """(row, minted TID) for rows created inside Foundry."""

    sheet_fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.applied)
            + len(self.dropped)
            + len(self.added)
            + len(self.moved)
            + len(self.sheet_fields)
        )


def _set(row: dict, section: str, key: str, value: Any) -> None:
    """Write one field, honouring omitzero and canonical key order."""
    always = schema.ALWAYS_WRITTEN.get(section, frozenset())
    if schema.is_zero(value) and key not in always:
        row.pop(key, None)
        return
    if key in row:
        row[key] = value
        return
    # Rebuild in canonical order so a newly added key lands where GCS puts it.
    row[key] = value
    reordered = sorted(row.items(), key=lambda kv: schema.order_key(section, kv[0]))
    row.clear()
    row.update(reordered)


def _detach(sheet_data: dict, entry, section: str) -> bool:
    """Remove a row from its parent's children or its section list."""

    def strip(rows: list | None) -> bool:
        if not rows:
            return False
        for i, candidate in enumerate(rows):
            if candidate is entry.data:
                rows.pop(i)
                return True
            if strip(candidate.get("children")):
                if not candidate.get("children"):
                    candidate.pop("children", None)
                return True
        return False

    return strip(sheet_data.get(section))


def _section_list(data: dict, section: str) -> list:
    """The sheet's list for ``section``, created in canonical order if absent."""
    existing = data.get(section)
    if isinstance(existing, list):
        return existing
    fresh: list = []
    data[section] = fresh
    order = schema.ENTITY_ORDER
    reordered = sorted(
        data.items(),
        key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order),
    )
    data.clear()
    data.update(reordered)
    return fresh


def _prune_section(data: dict, section: str) -> None:
    """Drop an emptied section: GCS's ``omitzero`` means it would not write it."""
    if section in data and not data[section]:
        data.pop(section)


def _move_blocked(by_tid: dict, entry, section: str, parent_tid: str | None) -> str:
    """Why this move must not be made, or ``""`` if it can be."""
    if _KIND_FOR_SECTION.get(section) != _KIND_FOR_SECTION.get(entry.section):
        return f"a {entry.section} row cannot become a {section} row"
    if not parent_tid:
        return ""
    if parent_tid == entry.tid:
        return "the export puts this row inside itself"
    parent = by_tid.get(parent_tid)
    if parent is None:
        return f"its new container {parent_tid} is not in the sheet"
    if any(descendant.tid == parent_tid for descendant in entry.walk()):
        return "the export puts this row inside one of its own children"
    if not parent.is_container:
        # Containers are a distinct TID kind in GCS; giving a leaf row children
        # would produce a row whose id contradicts its shape.
        return f"its new container {parent.name!r} is not a container in the sheet"
    return ""


def _move_row(data: dict, sheet, delta: RowDelta) -> str:
    """Detach a row from where it is and re-attach it where the export has it.

    Returns a description of what was done, or ``""`` if nothing was.  A
    container carries its children with it: they are nested inside its own
    dict, so moving that one dict moves the whole subtree.
    """
    entry, row = delta.entry, delta.row
    if entry is None or row is None:
        return ""
    section, parent_tid = row.gcs_section, row.parent_tid

    old_parent = sheet.by_tid.get(entry.parent_tid) if entry.parent_tid else None
    if not _detach(data, entry, entry.section):
        return ""
    _prune_section(data, entry.section)
    if old_parent is not None:
        old_parent.children = [c for c in old_parent.children if c is not entry]

    new_parent = sheet.by_tid.get(parent_tid) if parent_tid else None
    if new_parent is not None:
        siblings = new_parent.data.setdefault("children", [])
    else:
        siblings = _section_list(data, section)

    # Re-insert where the export has it, using the nearest following sibling
    # that the sheet actually has as the anchor.
    positions = {
        candidate.get("id"): i
        for i, candidate in enumerate(siblings)
        if isinstance(candidate, dict)
    }
    at = len(siblings)
    for tid in delta.move_before:
        if tid in positions:
            at = positions[tid]
            break
    siblings.insert(at, entry.data)

    entry.parent_tid = parent_tid
    if new_parent is not None:
        new_parent.children.insert(min(at, len(new_parent.children)), entry)
    for descendant in entry.walk():
        descendant.section = section
    return f"{delta.name}: {delta.moved_from_label} → {delta.moved_to_label}"


def plan(
    result: Reconciliation,
    *,
    deletions: str = DeletionPolicy.KEEP,
    include_lossy: bool = False,
) -> Plan:
    """Decide what would be written, without writing it."""
    outcome = Plan()
    # The sheet's rows, as seen through the reconciliation — plan() is given no
    # sheet, and every sheet row it could move something into is in a delta.
    by_tid = {d.tid: d.entry for d in result.deltas if d.entry is not None}

    for delta in result.deltas:
        if delta.status is Status.MATCHED:
            if delta.moved and delta.entry is not None and delta.row is not None:
                reason = _move_blocked(
                    by_tid, delta.entry, delta.row.gcs_section, delta.row.parent_tid
                )
                if reason:
                    outcome.skipped.append((delta, "position", reason))
                else:
                    outcome.moved.append(delta)
            for change in delta.changes:
                if change.applicable or (include_lossy and not change.blocked):
                    outcome.applied.append((delta, change.field))
                else:
                    reason = change.blocked or "lossy; pass --include-lossy to write it"
                    outcome.skipped.append((delta, change.field, reason))
        elif delta.status is Status.MISSING:
            (outcome.dropped if deletions == DeletionPolicy.DROP else outcome.kept).append(
                delta
            )
        elif delta.status is Status.ADDED:
            outcome.added.append((delta, ""))

    outcome.sheet_fields = [c.field for c in result.profile + result.attributes + result.points]
    return outcome


def apply(
    result: Reconciliation,
    sheet,
    *,
    deletions: str = DeletionPolicy.KEEP,
    include_lossy: bool = False,
    now: datetime | None = None,
) -> Plan:
    """Write the reconciliation into ``sheet``. Mutates the sheet's data."""
    if deletions not in DeletionPolicy.ALL:
        raise ValueError(f"unknown deletion policy {deletions!r}")

    outcome = Plan()
    data = sheet.data

    # ---- field edits on matched rows --------------------------------------
    for delta in result.deltas:
        if delta.status is not Status.MATCHED or delta.entry is None:
            continue
        for change in delta.changes:
            if not (change.applicable or (include_lossy and not change.blocked)):
                reason = change.blocked or "lossy; pass --include-lossy to write it"
                outcome.skipped.append((delta, change.field, reason))
                continue
            _set(delta.entry.data, delta.entry.section, change.field, change.new)
            outcome.applied.append((delta, change.field))

    # ---- rows that changed container or list -------------------------------
    # After the field edits, so a moved row's own edits land regardless of
    # whether the move itself turns out to be possible.
    for delta in result.deltas:
        if not (delta.moved and delta.status is Status.MATCHED):
            continue
        if delta.entry is None or delta.row is None:
            continue
        reason = _move_blocked(
            sheet.by_tid, delta.entry, delta.row.gcs_section, delta.row.parent_tid
        )
        if reason:
            outcome.skipped.append((delta, "position", reason))
            continue
        note = _move_row(data, sheet, delta)
        if note:
            outcome.moved.append(delta)
            outcome.notes.append(f"moved {note}")

    # ---- rows the export no longer has ------------------------------------
    for delta in result.by_status(Status.MISSING):
        if deletions == DeletionPolicy.DROP and delta.entry is not None:
            if _detach(data, delta.entry, delta.entry.section):
                _prune_section(data, delta.entry.section)
                sheet.by_tid.pop(delta.tid, None)
                outcome.dropped.append(delta)
        else:
            outcome.kept.append(delta)

    # ---- rows the base sheet does not have --------------------------------
    # In export order, which is depth-first: a container is therefore created
    # before anything that needs to nest inside it, and the rows land in the
    # order the player has them rather than in the report's reading order.
    for delta in sorted(result.by_status(Status.ADDED), key=lambda d: d.order):
        minted = _add_row(data, sheet, delta)
        if minted:
            outcome.added.append((delta, minted))

    # ---- sheet-level ------------------------------------------------------
    for change in result.profile:
        profile = data.setdefault("profile", {})
        if schema.is_zero(change.new):
            profile.pop(change.field, None)
        else:
            profile[change.field] = change.new
            # Profile has its own key order, and it is not the one the field
            # policy iterates in: GCS writes handedness before gender.
            reordered = sorted(profile.items(), key=lambda kv: schema.profile_key(kv[0]))
            profile.clear()
            profile.update(reordered)
        outcome.sheet_fields.append(change.field)

    for change in result.attributes:
        if _apply_attribute(data, change):
            outcome.sheet_fields.append(change.field)

    for change in result.points:
        data[change.field] = change.new
        outcome.sheet_fields.append(change.field)

    if outcome.total:
        stamp = (now or datetime.now().astimezone()).replace(microsecond=0)
        data["modified_date"] = stamp.isoformat()
        outcome.notes.append(f"modified_date set to {data['modified_date']}")

    outcome.notes.append(
        "calc blocks are left as the base sheet had them; GCS ignores calc on "
        "load and recomputes everything on open"
    )
    return outcome


def _apply_attribute(data: dict, change) -> bool:
    """Write ``attributes[<id>].<field>`` from a reconciler change."""
    target, _, name = change.field.partition("].")
    attr_id = target.split("[", 1)[-1]
    for attribute in data.get("attributes") or ():
        if attribute.get("attr_id") != attr_id:
            continue
        if schema.is_zero(change.new):
            attribute.pop(name, None)
        else:
            attribute[name] = change.new
            ordered = ("attr_id", "adj", "damage", "calc")
            reordered = sorted(
                attribute.items(),
                key=lambda kv: ordered.index(kv[0]) if kv[0] in ordered else len(ordered),
            )
            attribute.clear()
            attribute.update(reordered)
        return True
    return False


_KIND_FOR_SECTION = {
    "traits": tidmod.Kind.TRAIT,
    "skills": tidmod.Kind.SKILL,
    "spells": tidmod.Kind.SPELL,
    "equipment": tidmod.Kind.EQUIPMENT,
    "other_equipment": tidmod.Kind.EQUIPMENT,
    "notes": tidmod.Kind.NOTE,
}


def _row_id(row, section: str, sheet) -> str | None:
    """The TID a new row should carry: its own if usable, otherwise a fresh one.

    Foundry-created rows have a GGA-generated id rather than a TID, so those
    need minting with the kind prefix the section requires — GGA reads the row
    type back off that letter, and the wrong prefix produces a mistyped row.

    A row that *does* carry a real TID keeps it.  In synthesize mode that is
    the common case: the actor came from a GCS sheet we simply do not have, and
    reusing the original id means a later merge against the recovered sheet
    still lines up.
    """
    kind = _KIND_FOR_SECTION.get(section)
    if kind is None:
        return None
    container = bool(row.children)
    existing = row.tid
    if (
        existing
        and tidmod.is_valid(existing)
        and existing not in sheet.by_tid
        and tidmod.is_container(existing) == container
        and (
            existing[0].lower() == kind.lower()
            # A technique lives in the skills section under its own kind
            # letter, the same exception foundry.py makes when reading.
            or (section == "skills" and existing[0] == tidmod.Kind.TECHNIQUE)
        )
    ):
        return existing
    return tidmod.mint(kind, container=container)


#: What GCS uses when a skill has no difficulty at all. Not a guess of ours:
#: it is the zero value of ``skill.Difficulty``, and it is what GCS writes into
#: a row that arrives without one.
DEFAULT_DIFFICULTY = "e"


#: What GCS's own ``NewTechnique`` sets. A technique is relative to a base
#: skill, so it has no controlling attribute to name.
TECHNIQUE_DIFFICULTY = "a"


def _is_technique(row) -> bool:
    return row.data.get("type") == "TECHNIQUE" or (
        bool(row.tid) and row.tid[0] == tidmod.Kind.TECHNIQUE
    )


def _skill_difficulty(row) -> str:
    """``"iq/e"`` — the controlling attribute from the export, GCS's default letter.

    Foundry keeps only ``relativelevel`` (``"IQ+1"``), which names the attribute
    a skill is based on but not whether it is Easy, Average, Hard or Very Hard
    (docs/04-mapping.md).  The attribute half is real information and is
    otherwise thrown away; the letter is not recoverable and has to be fixed in
    GCS.  ``difficulty`` is written either way — the field is not omitzero — so
    the choice is between GCS's default with the attribute and GCS's default
    without it.
    """
    if _is_technique(row):
        return TECHNIQUE_DIFFICULTY
    relative = str(row.data.get("relativelevel") or "").strip()
    attribute = ""
    for char in relative:
        if not char.isalpha():
            break
        attribute += char
    if not attribute:
        return ""
    return f"{attribute.lower()}/{DEFAULT_DIFFICULTY}"


def _skill_name_fields(row) -> tuple[str, str, str]:
    """``(name, specialization, tech_level)`` for a skill row.

    Decomposes Foundry's composed name (docs/08-improvements.md 8.2) when the
    row was not renamed in Foundry — ``originalName`` is the same composed
    string GGA wrote at import, so a match means nothing changed and it is
    safe to split. A technique's name is mangled by a separate GGA bug (8.3)
    and is not decomposed; nor is a genuine rename, which need not follow the
    composition pattern at all.
    """
    if _is_technique(row) or row.display_name != row.gcs_name:
        return row.display_name, "", ""
    return policy.decompose_skill_name(row.gcs_name)


def _trait_name(row) -> str:
    """The name to write, with any level decoration stripped back out.

    ``originalName`` already lacks the level suffix GGA appends to ``name``
    (docs/04-mapping.md 4.4), so reuse the field policy's own rename check —
    :func:`fields._read_name` — rather than re-deriving it: a fabricated base
    row carrying the real level and the undecorated name gets the same
    rename-vs-decoration answer merge mode would give.
    """
    return policy._read_name(row, {"levels": row.data.get("level"), "name": row.gcs_name})


def _add_row(data: dict, sheet, delta: RowDelta) -> str | None:
    """Create a GCS row for something the base sheet does not have.

    Every field :data:`fields.RULES` knows how to read for this section is
    written from it, the same policy merge mode uses.  Only what the policy
    does not cover is handled directly here: the minted id, the name (its
    policy rule needs a base row to detect a rename against, and there isn't
    one), the difficulty guess, and traits' ``points`` -> ``base_points``
    rename.
    """
    row = delta.row
    if row is None:
        return None
    section = delta.section
    minted = _row_id(row, section, sheet)
    if minted is None:
        return None

    fresh: dict[str, Any] = {"id": minted}
    is_container = bool(row.children)

    if section == "notes":
        fresh["markdown"] = row.data.get("notes") or ""
        if row.data.get("pageref"):
            fresh["reference"] = row.data["pageref"]
    else:
        if section in ("equipment", "other_equipment"):
            fresh["description"] = row.display_name
            quantity = row.data.get("count")
            fresh["quantity"] = (
                Num(str(quantity)) if quantity not in (None, "") else Num("1")
            )
        elif section == "traits":
            fresh["name"] = _trait_name(row)
        elif section == "skills" and not is_container:
            name, specialization, tech_level = _skill_name_fields(row)
            fresh["name"] = name
            if specialization:
                fresh["specialization"] = specialization
            if tech_level:
                fresh["tech_level"] = tech_level
            difficulty = _skill_difficulty(row)
            if difficulty:
                fresh["difficulty"] = difficulty
        else:
            fresh["name"] = row.display_name

        for rule in policy.RULES.get(section, ()):
            if rule.gcs in ("name", "description", "quantity"):
                continue  # written above: no base row to rename or default against
            if rule.gcs == "points" and is_container:
                continue  # a container's total comes from its children
            value = rule.read(row, {})
            if rule.gga_default is not policy._MISSING and policy.values_equal(
                value, rule.gga_default, rule.compare
            ):
                continue  # equals what GGA fabricates, not necessarily real data
            if schema.is_zero(value):
                continue
            fresh[rule.gcs] = value

        # Traits spell it 'base_points'; the policy's table doesn't cover this
        # rename, and GCS silently discards the wrong key rather than erroring.
        if section == "traits" and not is_container:
            points = row.data.get("points")
            if points not in (None, ""):
                value = Num(str(points))
                if not schema.is_zero(value):
                    fresh["base_points"] = value
            if not schema.is_zero(fresh.get("levels")):
                # trait.go forces this on load whenever a non-container trait
                # has nonzero levels; writing levels without it just means GCS
                # adds it back and disagrees with our output.
                fresh["can_level"] = True

    reordered = sorted(fresh.items(), key=lambda kv: schema.order_key(section, kv[0]))
    fresh = dict(reordered)

    # 'children' is not created here: it is last in every section's key order,
    # so the setdefault a child does lands it in the right place, and a
    # container that ends up with none must not carry an empty omitzero list.
    parent = sheet.by_tid.get(row.parent_tid) if row.parent_tid else None
    if parent is not None:
        parent.data.setdefault("children", []).append(fresh)
    else:
        _section_list(data, section).append(fresh)

    # Register it, so rows created later in this same run can nest inside it.
    entry = gcs.Entry(
        tid=minted,
        section=section,
        data=fresh,
        parent_tid=parent.tid if parent is not None else None,
    )
    sheet.by_tid[minted] = entry
    if parent is not None:
        parent.children.append(entry)
    return minted
