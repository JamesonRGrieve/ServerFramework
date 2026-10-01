# SPDX-License-Identifier: AGPL-3.0-or-later
"""Family-tree algorithms over ``ancestry`` relationships.

An ``ancestry`` relationship reads subject → object as parent → child. The
relationship table always reads "subject (kind, discriminator) object" with
the discriminator naming the subject's role, as ``('member_of', 'leader')``
names a person leading a faction; for ancestry the roles are the parent's
(``biological``, ``adopted``, ``step``, ``foster``, ``legal_guardian``).

The graph is many-to-many (a child has any number of parents) and may hold
bad data, including cycles; every walk is breadth-first with a visited set,
so it terminates and reports each relative at their nearest generation.
"""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

ANCESTRY = "ancestry"
PARENT_ROLES = ("biological", "adopted", "step", "foster", "legal_guardian")


@dataclass(frozen=True)
class ParentEdge:
    """One ancestry relationship: ``parent_id`` is ``child_id``'s parent in
    the role ``discriminator`` (None when unrecorded)."""

    parent_id: str
    child_id: str
    discriminator: Optional[str] = None


@dataclass(frozen=True)
class Kinship:
    """How two people are related through their nearest common ancestor.

    ``up_from_a`` and ``up_from_b`` count generations from each person up to
    that ancestor; 0 means the person *is* the ancestor. ``degree`` is their
    sum (the civil-law degree of kinship). For collateral relatives,
    ``cousin`` is the cousin degree (1 for first cousins; 0 for siblings,
    piblings and niblings) and ``removed`` the generation gap."""

    common_ancestors: Tuple[str, ...]
    up_from_a: int
    up_from_b: int

    @property
    def degree(self) -> int:
        return self.up_from_a + self.up_from_b

    @property
    def cousin(self) -> int:
        return max(min(self.up_from_a, self.up_from_b) - 1, 0)

    @property
    def removed(self) -> int:
        return abs(self.up_from_a - self.up_from_b)

    @property
    def lineal(self) -> bool:
        """One is the other's ancestor."""
        return self.up_from_a == 0 or self.up_from_b == 0


class FamilyTree:
    """The parent/child graph of a set of ancestry relationships, optionally
    limited to some parent roles (``{"biological"}`` for blood kinship)."""

    def __init__(
        self, edges: Iterable[ParentEdge], roles: Optional[Iterable[str]] = None
    ) -> None:
        allowed: Optional[FrozenSet[str]] = (
            frozenset(roles) if roles is not None else None
        )
        self._parents: Dict[str, Set[str]] = {}
        self._children: Dict[str, Set[str]] = {}
        for edge in edges:
            if edge.parent_id == edge.child_id:
                continue
            if allowed is not None and edge.discriminator not in allowed:
                continue
            self._parents.setdefault(edge.child_id, set()).add(edge.parent_id)
            self._children.setdefault(edge.parent_id, set()).add(edge.child_id)

    def parents(self, person_id: str) -> Set[str]:
        return set(self._parents.get(person_id, ()))

    def children(self, person_id: str) -> Set[str]:
        return set(self._children.get(person_id, ()))

    @staticmethod
    def _walk(
        start: str, step: Mapping[str, Set[str]], generations: Optional[int]
    ) -> Dict[str, int]:
        found: Dict[str, int] = {}
        queue: Deque[Tuple[str, int]] = deque([(start, 0)])
        seen = {start}
        while queue:
            person, depth = queue.popleft()
            if generations is not None and depth >= generations:
                continue
            for relative in step.get(person, ()):
                if relative in seen:
                    continue
                seen.add(relative)
                found[relative] = depth + 1
                queue.append((relative, depth + 1))
        return found

    def ancestors(
        self, person_id: str, generations: Optional[int] = None
    ) -> Dict[str, int]:
        """Each ancestor of ``person_id`` with their generation (1 = parent),
        up to ``generations`` generations back."""
        return self._walk(person_id, self._parents, generations)

    def descendants(
        self, person_id: str, generations: Optional[int] = None
    ) -> Dict[str, int]:
        """Each descendant of ``person_id`` with their generation (1 = child),
        down to ``generations`` generations."""
        return self._walk(person_id, self._children, generations)

    def kinship(self, a: str, b: str) -> Optional[Kinship]:
        """How ``a`` and ``b`` are related through their most recent common
        ancestors (those nearest in total generations), or None if they
        share no ancestor and neither descends from the other."""
        if a == b:
            return Kinship(common_ancestors=(a,), up_from_a=0, up_from_b=0)
        from_a = {a: 0, **self.ancestors(a)}
        from_b = {b: 0, **self.ancestors(b)}
        shared = from_a.keys() & from_b.keys()
        if not shared:
            return None
        nearest = min(from_a[p] + from_b[p] for p in shared)
        closest = sorted(p for p in shared if from_a[p] + from_b[p] == nearest)
        # Every nearest common ancestor sits at the same pair of distances
        # unless the data is inconsistent; report the pair nearest to a.
        up_a, up_b = min((from_a[p], from_b[p]) for p in closest)
        return Kinship(common_ancestors=tuple(closest), up_from_a=up_a, up_from_b=up_b)

    def most_recent_common_ancestors(self, a: str, b: str) -> List[str]:
        kinship = self.kinship(a, b)
        return list(kinship.common_ancestors) if kinship else []
