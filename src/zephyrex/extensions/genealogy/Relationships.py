# SPDX-License-Identifier: AGPL-3.0-or-later
"""The relationship kinds genealogy knows, and the checks a relationship of
one of them must pass.

A kind is either directed (``ancestry`` reads parent → child) or symmetric
(``partnership``, ``friendship``): a symmetric relationship holds both ways,
whichever direction its row was written in. Each kind lists the
discriminators (subkinds) it accepts; a discriminator is optional.

``kind`` stays free text: a kind not registered here (``member_of`` from
rpg_state) is stored unchecked. An extension that wants genealogy to check
its kind, or to walk it as a social tie, registers it with
:func:`register_relationship_kind`.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from zephyrex.extensions.genealogy.FamilyTree import ANCESTRY, PARENT_ROLES

PARTNERSHIP = "partnership"
SIBLING_OF = "sibling_of"
FRIENDSHIP = "friendship"
ACQUAINTANCE = "acquaintance"
# Directed: the subject teaches, mentors or coaches the object.
MENTORSHIP = "mentorship"

# Romantic and sexual partnerships, from the most to the least entangled.
# ``nesting`` partners share a home; an ``anchor`` partner is a central,
# long-term partner who may not; a ``comet`` is seen rarely but resumed.
PARTNERSHIP_KINDS = (
    "marriage",
    "civil_union",
    "engagement",
    "partnership",
    "nesting",
    "anchor",
    "dating",
    "comet",
    "casual",
    "lover",
    "liaison",
)
FRIENDSHIP_KINDS = (
    "best_friend",
    "close_friend",
    "friend",
    "queerplatonic",
    "friends_with_benefits",
)
ACQUAINTANCE_KINDS = ("colleague", "classmate", "neighbour", "online")
SIBLING_KINDS = ("full", "half", "step", "adopted", "foster")
MENTORSHIP_ROLES = ("teacher", "mentor", "coach")


@dataclass(frozen=True)
class RelationshipKind:
    """A relationship kind: whether it holds both ways, and the
    discriminators it accepts (any when ``discriminators`` is None)."""

    name: str
    symmetric: bool
    discriminators: Optional[Tuple[str, ...]] = None


RELATIONSHIP_KINDS: Dict[str, RelationshipKind] = {}


def register_relationship_kind(kind: RelationshipKind) -> None:
    """Make genealogy check ``kind`` and, when symmetric, walk it as a tie.
    Registering a name twice with a different definition is a programming
    error, so it raises rather than silently replacing the first."""
    existing = RELATIONSHIP_KINDS.get(kind.name)
    if existing is not None and existing != kind:
        raise ValueError(
            f"Relationship kind {kind.name!r} is already registered differently"
        )
    RELATIONSHIP_KINDS[kind.name] = kind


for _kind in (
    RelationshipKind(ANCESTRY, symmetric=False, discriminators=PARENT_ROLES),
    RelationshipKind(PARTNERSHIP, symmetric=True, discriminators=PARTNERSHIP_KINDS),
    RelationshipKind(SIBLING_OF, symmetric=True, discriminators=SIBLING_KINDS),
    RelationshipKind(FRIENDSHIP, symmetric=True, discriminators=FRIENDSHIP_KINDS),
    RelationshipKind(ACQUAINTANCE, symmetric=True, discriminators=ACQUAINTANCE_KINDS),
    RelationshipKind(MENTORSHIP, symmetric=False, discriminators=MENTORSHIP_ROLES),
):
    register_relationship_kind(_kind)


def symmetric_kinds() -> Tuple[str, ...]:
    """Every registered kind that holds both ways."""
    return tuple(sorted(k.name for k in RELATIONSHIP_KINDS.values() if k.symmetric))


def directed_kinds() -> Tuple[str, ...]:
    """Every registered kind that reads one way (subject → object)."""
    return tuple(sorted(k.name for k in RELATIONSHIP_KINDS.values() if not k.symmetric))


def describe_discriminators() -> str:
    """The registered kinds' discriminators, for API descriptions:
    ``biological|adopted|… for ancestry; marriage|… for partnership``."""
    return "; ".join(
        f"{'|'.join(k.discriminators)} for {k.name}"
        for k in RELATIONSHIP_KINDS.values()
        if k.discriminators
    )


class RelationshipError(ValueError):
    """A relationship a registered kind does not allow."""


def check_relationship(
    kind: Optional[str],
    discriminator: Optional[str],
    person_id: Optional[str],
    target_person_id: Optional[str],
) -> None:
    """Raise :class:`RelationshipError` when a relationship of a registered
    kind relates a person to themselves or names a discriminator the kind
    does not accept. Unregistered kinds pass unchecked."""
    registered = RELATIONSHIP_KINDS.get(kind) if kind is not None else None
    if registered is None:
        return
    if person_id is not None and person_id == target_person_id:
        raise RelationshipError(f"A {kind} relationship needs two different people")
    allowed = registered.discriminators
    if (
        discriminator is not None
        and allowed is not None
        and discriminator not in allowed
    ):
        raise RelationshipError(
            f"{discriminator!r} is not a kind of {kind}; expected one of {', '.join(allowed)}"
        )
