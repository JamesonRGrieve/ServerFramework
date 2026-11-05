# SPDX-License-Identifier: AGPL-3.0-or-later
"""Social-graph algorithms over symmetric relationships.

A symmetric relationship (partnership, friendship, sibling_of, …) is a tie
between two people whichever direction its row reads, and is often stored
as one row each way; the graph folds both into one undirected tie. Only
ties in effect at a moment count: ``valid_from`` on or before it and
``valid_to`` after it (either may be open).

``metamours`` are a person's partners' partners who are not their own
partners; ``network`` is everyone reachable through the chosen kinds of
tie, such as a whole polycule through partnerships.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from zephyrex.extensions.genealogy.Relationships import PARTNERSHIP


@dataclass(frozen=True)
class Tie:
    """One symmetric relationship row."""

    person_id: str
    target_person_id: str
    kind: str
    discriminator: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    # A directed tie (mentorship) keeps which end is the subject when drawn.
    directed: bool = False

    def in_effect(self, at: datetime) -> bool:
        moment = _utc(at)
        started = self.valid_from is None or _utc(self.valid_from) <= moment
        ended = self.valid_to is not None and _utc(self.valid_to) <= moment
        return started and not ended

    def in_effect_between(self, start: datetime, end: datetime) -> bool:
        """Whether the tie was in effect at any moment from ``start`` to ``end``."""
        began = self.valid_from is None or _utc(self.valid_from) <= _utc(end)
        lasted = self.valid_to is None or _utc(self.valid_to) > _utc(start)
        return began and lasted


def _utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime; a naive one is read as UTC, as the
    database stores them."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(frozen=True)
class Connection:
    """Someone a person is tied to, by one kind (and subkind) of tie."""

    person_id: str
    kind: str
    discriminator: Optional[str]


    """The undirected graph of ``ties`` of the given ``kinds`` that are in
    effect ``at`` a moment; every moment when ``at`` is None."""

    def __init__(
        self,
        ties: Iterable[Tie],
        kinds: Iterable[str],
        at: Optional[datetime] = None,
    ) -> None:
        wanted: FrozenSet[str] = frozenset(kinds)
        self._ties: Dict[str, Set[Tuple[str, str, Optional[str]]]] = {}
        self._directed: Set[Tuple[str, str, str, Optional[str]]] = set()
        for tie in ties:
            if tie.kind not in wanted or tie.person_id == tie.target_person_id:
                continue
            if at is not None and not tie.in_effect(at):
                continue
            self._link(tie.person_id, tie.target_person_id, tie)
            self._link(tie.target_person_id, tie.person_id, tie)
            if tie.directed:
                self._directed.add(
                    (tie.person_id, tie.target_person_id, tie.kind, tie.discriminator)
                )

    def _link(self, person_id: str, other_id: str, tie: Tie) -> None:
        self._ties.setdefault(person_id, set()).add(
            (other_id, tie.kind, tie.discriminator)
        )

    def neighbours(self, person_id: str, kind: Optional[str] = None) -> Set[str]:
        """Everyone tied to ``person_id``, by ``kind`` when given."""
        return {
            other
            for other, k, _ in self._ties.get(person_id, ())
            if kind is None or k == kind
        }

    def connections(self, person_id: str) -> List[Connection]:
        """Each tie of ``person_id``, ordered by kind then person."""
        return [
            Connection(person_id=other, kind=kind, discriminator=discriminator)
            for other, kind, discriminator in sorted(
                self._ties.get(person_id, ()), key=lambda t: (t[1], t[0], t[2] or "")
            )
        ]

    def metamours(self, person_id: str) -> Dict[str, Set[str]]:
        """Each partner of ``person_id``'s partners who is not ``person_id``
        or their partner, with the partners they share."""
        partners = self.neighbours(person_id, PARTNERSHIP)
        found: Dict[str, Set[str]] = {}
        for partner in partners:
            for metamour in self.neighbours(partner, PARTNERSHIP):
                if metamour != person_id and metamour not in partners:
                    found.setdefault(metamour, set()).add(partner)
        return found

        """Each tie between two of ``person_ids``, once whichever way it was
        stored, ordered by its ends then kind: the edges to draw a network. A
        symmetric tie's ends are sorted; a directed one keeps its subject
        first."""
        within = frozenset(person_ids)
        found: Set[Tuple[str, str, str, Optional[str], bool]] = set()
        for person in within:
            for other, kind, discriminator in self._ties.get(person, ()):
                if other not in within:
                    continue
                if (person, other, kind, discriminator) in self._directed:
                    found.add((person, other, kind, discriminator, True))
                elif (other, person, kind, discriminator) not in self._directed:
                    first, second = sorted((person, other))
                    found.add((first, second, kind, discriminator, False))
        return [
            Tie(
                person_id=a,
                target_person_id=b,
                kind=kind,
                discriminator=discriminator,
                directed=directed,
            )
            for a, b, kind, discriminator, directed in sorted(
                found, key=lambda t: (t[0], t[1], t[2], t[3] or "")
            )
        ]

        """Everyone reachable from ``person_id`` with their distance (1 = a
        direct tie), up to ``depth`` steps away."""
        found: Dict[str, int] = {}
        queue: Deque[Tuple[str, int]] = deque([(person_id, 0)])
        seen = {person_id}
        while queue:
            person, distance = queue.popleft()
            if depth is not None and distance >= depth:
                continue
            for other in sorted(self.neighbours(person)):
                if other in seen:
                    continue
                seen.add(other)
                found[other] = distance + 1
                queue.append((other, distance + 1))
        return found
