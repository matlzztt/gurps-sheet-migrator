"""Canonical GCS key order, and which keys survive a zero value.

GCS writes JSON in Go **struct declaration order**, not alphabetically, and
omits almost every zero value (docs/02-gcs-format.md 2.1).  Reading a file and
writing it back preserves both for free — but the moment we *add* a key that was
not there, we have to know where it goes and whether it belongs at all.

The orders below are transcribed from the struct definitions in
``gcs/model/gurps/``, flattening embedded structs at the position of the
embedded field the way ``encoding/json`` does.  ``tests/test_schema.py`` checks
every row in every fixture against them, so the transcription is verified
against real GCS output rather than trusted.
"""

from __future__ import annotations

__all__ = ["FIELD_ORDER", "ALWAYS_WRITTEN", "order_key", "is_zero"]

# trait.go: TraitData{SourcedID, TraitEditData, ThirdParty, Children}
_TRAIT = (
    "id", "source",
    # TraitSyncData
    "name", "reference", "reference_highlight", "local_notes", "tags", "prereqs", "cr_adj",
    # rest of TraitEditData
    "vtt_notes", "userdesc", "replacements", "modifiers", "cr", "frequency", "disabled",
    "switched_on", "preconfigured",
    # TraitNonContainerSyncData, then TraitNonContainerOnlyEditData
    "base_points", "points_per_level", "max_levels", "weapons", "features",
    "round_down", "can_level", "levels", "study", "study_hours_needed",
    # TraitContainerSyncData
    "ancestry", "template_picker", "container_type",
    "third_party", "children",
    "calc",  # appended by MarshalJSONTo, discarded on load
)

# skill.go: SkillData{SourcedID, SkillEditData, ThirdParty, Children}
_SKILL = (
    "id", "source",
    "name", "reference", "reference_highlight", "local_notes", "tags",
    "vtt_notes", "replacements", "switched_on", "preconfigured",
    # SkillNonContainerOnlySyncData
    "specialization", "difficulty", "encumbrance_penalty_multiplier",
    "defaults", "default", "limit", "prereqs", "weapons", "features",
    # rest of SkillNonContainerOnlyEditData
    "optional_specialization", "tech_level", "points", "defaulted_from",
    "study", "study_hours_needed",
    "template_picker",
    "third_party", "children",
    "calc",
)

# spell.go: SpellData{SourcedID, SpellEditData, ThirdParty, Children}
_SPELL = (
    "id", "source",
    "name", "reference", "reference_highlight", "local_notes", "tags",
    "vtt_notes", "replacements", "switched_on", "preconfigured",
    "difficulty", "college", "power_source", "spell_class", "resist",
    "casting_cost", "maintenance_cost", "casting_time", "duration", "item",
    "base_skill", "prereq_count", "prereqs", "weapons", "features",
    "tech_level", "points", "study", "study_hours_needed",
    "template_picker",
    "third_party", "children",
    "calc",
)

# equipment.go: EquipmentData{SourcedID, EquipmentEditData, ThirdParty, Children}
_EQUIPMENT = (
    "id", "source",
    # EquipmentSyncData
    "description", "reference", "reference_highlight", "local_notes",
    "tech_level", "legality_class", "tags", "base_value", "base_weight",
    "max_uses", "prereqs", "weapons", "features", "ignore_weight_for_skills",
    # rest of EquipmentEditData
    "vtt_notes", "replacements", "modifiers", "rated_strength",
    "quantity", "level", "uses", "equipped", "switched_on", "preconfigured",
    "third_party", "children",
    "calc",
)

# note.go: NoteData{SourcedID, NoteEditData, ThirdParty, Children}
_NOTE = (
    "id", "source",
    "markdown", "reference", "reference_highlight", "tags",
    "replacements", "preconfigured",
    "third_party", "children",
    "calc",
)

FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "traits": _TRAIT,
    "skills": _SKILL,
    "spells": _SPELL,
    "equipment": _EQUIPMENT,
    "other_equipment": _EQUIPMENT,
    "notes": _NOTE,
}

#: Fields whose Go tag has no ``omitzero``, so GCS writes them even when zero.
#: Everything else disappears from the file when it holds a zero value — which
#: makes "set this to false" the same operation as "remove this key".
ALWAYS_WRITTEN: dict[str, frozenset[str]] = {
    "traits": frozenset({"id"}),
    "skills": frozenset({"id"}),
    "spells": frozenset({"id"}),
    "equipment": frozenset({"id", "quantity"}),
    "other_equipment": frozenset({"id", "quantity"}),
    "notes": frozenset({"id"}),
}

#: The sheet's own top-level keys, from EntityData in entity.go.
ENTITY_ORDER = (
    "version", "id", "total_points", "points_record", "profile", "settings",
    "attributes", "traits", "skills", "spells", "equipment", "other_equipment",
    "notes", "created_date", "modified_date", "third_party", "calc",
)


def order_key(section: str, key: str) -> tuple[int, str]:
    """Sort key placing ``key`` where GCS would write it.

    Unknown keys sort to the end, in name order, rather than being dropped —
    a newer GCS may add fields this table has never heard of, and losing them
    would be worse than putting them in the wrong place.
    """
    order = FIELD_ORDER.get(section)
    if order is None:
        return (0, key)
    try:
        return (order.index(key), "")
    except ValueError:
        return (len(order), key)


def is_zero(value: object) -> bool:
    """True if GCS would omit this value rather than write it.

    Note that ``0`` and ``False`` count: setting a field to its zero value means
    deleting the key, not writing a falsy one.
    """
    if value is None or value is False:
        return True
    if isinstance(value, (str, list, dict, tuple)):
        return len(value) == 0
    try:
        return float(str(value)) == 0
    except (TypeError, ValueError):
        return False
