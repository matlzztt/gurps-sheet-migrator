"""GCS TIDs — the identity system the whole converter hangs on.

A TID is a one-character kind prefix followed by 16 base64url characters
(docs/02-gcs-format.md 2.2).  GGA copies them verbatim into each Foundry row's
``uuid`` and Foundry's export writes them back, which is what lets us match a
Foundry row to the exact GCS row it came from.

The kind prefix is load-bearing, not decorative: GGA's importer decides whether a
row is a trait or a trait *container* purely from the case of the letter, so a
minted TID with the wrong prefix silently produces a mistyped row.
"""

from __future__ import annotations

import secrets

__all__ = [
    "Kind",
    "is_valid",
    "kind_of",
    "is_container",
    "mint",
    "KIND_NAMES",
    "CONTAINER_OF",
]

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_ALPHABET_SET = frozenset(_ALPHABET)
LENGTH = 17


class Kind:
    """Kind prefixes, mirroring ``gcs/model/kinds/kinds.go``."""

    ENTITY = "A"
    TRAIT = "t"
    TRAIT_CONTAINER = "T"
    TRAIT_MODIFIER = "m"
    TRAIT_MODIFIER_CONTAINER = "M"
    SKILL = "s"
    SKILL_CONTAINER = "S"
    TECHNIQUE = "q"
    SPELL = "p"
    SPELL_CONTAINER = "P"
    RITUAL_MAGIC_SPELL = "r"
    EQUIPMENT = "e"
    EQUIPMENT_CONTAINER = "E"
    EQUIPMENT_MODIFIER = "f"
    EQUIPMENT_MODIFIER_CONTAINER = "F"
    NOTE = "n"
    NOTE_CONTAINER = "N"
    WEAPON_MELEE = "w"
    WEAPON_RANGED = "W"
    CONDITIONAL_MODIFIER = "c"
    CAMPAIGN = "C"
    LOOT = "L"
    TEMPLATE = "B"


KIND_NAMES: dict[str, str] = {
    Kind.ENTITY: "entity",
    Kind.TRAIT: "trait",
    Kind.TRAIT_CONTAINER: "trait_container",
    Kind.TRAIT_MODIFIER: "trait_modifier",
    Kind.TRAIT_MODIFIER_CONTAINER: "trait_modifier_container",
    Kind.SKILL: "skill",
    Kind.SKILL_CONTAINER: "skill_container",
    Kind.TECHNIQUE: "technique",
    Kind.SPELL: "spell",
    Kind.SPELL_CONTAINER: "spell_container",
    Kind.RITUAL_MAGIC_SPELL: "ritual_magic_spell",
    Kind.EQUIPMENT: "equipment",
    Kind.EQUIPMENT_CONTAINER: "equipment_container",
    Kind.EQUIPMENT_MODIFIER: "equipment_modifier",
    Kind.EQUIPMENT_MODIFIER_CONTAINER: "equipment_modifier_container",
    Kind.NOTE: "note",
    Kind.NOTE_CONTAINER: "note_container",
    Kind.WEAPON_MELEE: "melee_weapon",
    Kind.WEAPON_RANGED: "ranged_weapon",
    Kind.CONDITIONAL_MODIFIER: "conditional_modifier",
    Kind.CAMPAIGN: "campaign",
    Kind.LOOT: "loot",
    Kind.TEMPLATE: "template",
}

#: Leaf prefix -> the container prefix of the same kind.
CONTAINER_OF: dict[str, str] = {
    Kind.TRAIT: Kind.TRAIT_CONTAINER,
    Kind.SKILL: Kind.SKILL_CONTAINER,
    Kind.SPELL: Kind.SPELL_CONTAINER,
    Kind.EQUIPMENT: Kind.EQUIPMENT_CONTAINER,
    Kind.NOTE: Kind.NOTE_CONTAINER,
    Kind.TRAIT_MODIFIER: Kind.TRAIT_MODIFIER_CONTAINER,
    Kind.EQUIPMENT_MODIFIER: Kind.EQUIPMENT_MODIFIER_CONTAINER,
}


def is_valid(value: object) -> bool:
    """True if ``value`` is a syntactically valid TID of a known kind."""
    if not isinstance(value, str) or len(value) != LENGTH:
        return False
    if value[0] not in KIND_NAMES:
        return False
    return _ALPHABET_SET.issuperset(value[1:])


def kind_of(value: str) -> str | None:
    """The kind name for a TID, or ``None`` if it is not a valid TID.

    Beware of short ``id`` values elsewhere in a sheet: attribute definitions
    use ``"st"``, hit locations use ``"torso"``.  Those are stable string keys,
    not TIDs, and this returns ``None`` for them.
    """
    return KIND_NAMES.get(value[0]) if is_valid(value) else None


def is_container(value: str) -> bool:
    """True if the TID names a container (an uppercase kind prefix)."""
    return is_valid(value) and value[0] in CONTAINER_OF.values()


def mint(kind: str, *, container: bool = False) -> str:
    """Generate a fresh TID for the given kind prefix.

    Used for rows added inside Foundry, which have no GCS counterpart.  Pass
    ``container=True`` to get the uppercase form of the same kind.
    """
    if container:
        kind = CONTAINER_OF.get(kind, kind)
    if kind not in KIND_NAMES:
        raise ValueError(f"unknown TID kind {kind!r}")
    body = "".join(secrets.choice(_ALPHABET) for _ in range(LENGTH - 1))
    return kind + body
