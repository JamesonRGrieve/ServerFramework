# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merging linked trees: one graph from a viewer's tree and the trees of the
accounts it reaches through mutual links.

Everyone keeps their own tree. A person record may be linked to an account
(``linked_user_id``); two accounts' trees merge only when each has linked
the other, so nobody can pull a tree into view by linking a ghost to its
account. Merging spreads from the viewer: an account's tree joins when that
account is mutually linked to an account already joined, through a person
in the joining-side tree's own network (the walked kinds of tie), so a
linked relative outside it does not bring their whole family along.

In the merged graph a mutually linked account is one node, however many
trees record it; everyone else (ghosts) stays the record it is in. The
viewer sees their own records as they wrote them. Anyone else shows by
their visibility: an account by the setting on its self person, a ghost by
the setting its recorder gave it, ``anonymous`` when unset. A ``hidden``
person is left out, with their ties.

The viewer's polycules are the groups the network falls into once the
viewer is taken out of it: each group branches off the viewer on its own.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, FrozenSet, Iterable, List, Optional, Set

from zephyrex.extensions.genealogy.SocialGraph import SocialGraph, Tie

ANONYMOUS = "anonymous"
HIDDEN = "hidden"
SHOWN_BY_NAME = frozenset({"named", "profile"})


@dataclass(frozen=True)
class TreePerson:
    id: str
    owner: str
    name: Optional[str]
    linked_user_id: Optional[str] = None
    visibility: Optional[str] = None


@dataclass
class Tree:
    """One account's tree: the people it recorded and the ties among them
    (already limited to the kinds walked and in effect)."""

    owner: str
    persons: Dict[str, TreePerson] = field(default_factory=dict)
    ties: List[Tie] = field(default_factory=list)

    def self_person(self) -> Optional[TreePerson]:
        for person in self.persons.values():
            if person.linked_user_id == self.owner:
                return person
        return None

    def linked_to(self, user_id: str) -> List[TreePerson]:
        return [p for p in self.persons.values() if p.linked_user_id == user_id]


@dataclass(frozen=True)
class MergedNode:
    key: str
    name: Optional[str]
    visibility: str
    mine: bool
    person_id: Optional[str] = None
    user_id: Optional[str] = None
    distance: int = 0
    group: Optional[int] = None


@dataclass(frozen=True)
class MergedGraph:
    center: str
    nodes: List[MergedNode]
    ties: List[Tie]


def _local_network(tree: Tree, kinds: Iterable[str]) -> Set[str]:
    """The ids of the people in ``tree`` its owner's self person reaches."""
    me = tree.self_person()
    if me is None:
        return set()
    return {me.id, *SocialGraph(tree.ties, kinds).network(me.id)}


def _join(
    viewer: Tree, load: Callable[[str], Tree], kinds: FrozenSet[str]
) -> Dict[str, Tree]:
    """The trees that merge with ``viewer``'s, by owner."""
    joined: Dict[str, Tree] = {viewer.owner: viewer}
    loaded: Dict[str, Tree] = {viewer.owner: viewer}
    queue: Deque[str] = deque([viewer.owner])
    while queue:
        tree = joined[queue.popleft()]
        reached = _local_network(tree, kinds)
        for person_id in sorted(reached):
            account = tree.persons[person_id].linked_user_id
            if account is None or account in joined:
                continue
            other = loaded.setdefault(account, load(account))
            if other.linked_to(tree.owner):
                joined[account] = other
                queue.append(account)
    return joined


    """The merged graph seen by ``viewer``'s owner, centred on their self
    person. ``load`` reads another account's tree."""
    walked = frozenset(kinds)
    me = viewer.self_person()
    if me is None:
        raise ValueError("The viewer has no self person")
    trees = _join(viewer, load, walked)
    accounts = set(trees)

    def key_of(person: TreePerson) -> str:
        linked = person.linked_user_id
        return f"user:{linked}" if linked in accounts else f"person:{person.id}"

    # What the viewer recorded about each node, which they always see as written.
    mine: Dict[str, TreePerson] = {}
    for person in viewer.persons.values():
        mine.setdefault(key_of(person), person)

        if key in mine:
            record = mine[key]
            return MergedNode(
                key=key,
                name=record.name,
                visibility=record.visibility or ANONYMOUS,
                mine=True,
                person_id=record.id,
                user_id=record.linked_user_id if key.startswith("user:") else None,
            )
        if key.startswith("user:"):
            account = key.removeprefix("user:")
            own = trees[account].self_person()
            visibility = (own.visibility if own else None) or ANONYMOUS
            name = own.name if own and visibility in SHOWN_BY_NAME else None
            return MergedNode(
                key=key, name=name, visibility=visibility, mine=False, user_id=account
            )
        ghost = next(
            p
            for t in trees.values()
            for p in t.persons.values()
            if f"person:{p.id}" == key
        )
        visibility = ghost.visibility or ANONYMOUS
        name = ghost.name if visibility in SHOWN_BY_NAME else None
        return MergedNode(key=key, name=name, visibility=visibility, mine=False)

    candidates: Dict[str, MergedNode] = {}
    ties: List[Tie] = []
    for tree in trees.values():
        for tie in tree.ties:
            ends = [
                tree.persons.get(tie.person_id),
                tree.persons.get(tie.target_person_id),
            ]
            if ends[0] is None or ends[1] is None:
                continue
            a, b = key_of(ends[0]), key_of(ends[1])
            if a == b:
                continue
            for key in (a, b):
                if key not in candidates:
                    candidates[key] = shown(key)
            ties.append(
                Tie(
                    person_id=a,
                    target_person_id=b,
                    kind=tie.kind,
                    discriminator=tie.discriminator,
                    directed=tie.directed,
                )
            )
    center = key_of(me)
    candidates.setdefault(center, shown(center))
    visible = {
        k for k, node in candidates.items() if node.mine or node.visibility != HIDDEN
    }
    kept = [t for t in ties if t.person_id in visible and t.target_person_id in visible]
    graph = SocialGraph(kept, walked)
    distances = graph.network(center)
    groups = _groups(graph, center, distances)
    nodes = [
        _placed(candidates[center], 0, None),
        *(
            _placed(candidates[k], d, groups[k])
            for k, d in sorted(distances.items(), key=lambda item: (item[1], item[0]))
        ),
    ]
    return MergedGraph(
        center=center, nodes=nodes, ties=graph.ties_among([center, *distances])
    )


def _placed(node: MergedNode, distance: int, group: Optional[int]) -> MergedNode:
    return MergedNode(
        key=node.key,
        name=node.name,
        visibility=node.visibility,
        mine=node.mine,
        person_id=node.person_id,
        user_id=node.user_id,
        distance=distance,
        group=group,
    )


    """Each member's polycule: the group it falls in once ``center`` is taken
    out, numbered in the order their nearest members are reached."""
    groups: Dict[str, int] = {}
    for start in sorted(members, key=lambda k: (members[k], k)):
        if start in groups:
            continue
        number = len(set(groups.values()))
        queue: Deque[str] = deque([start])
        groups[start] = number
        while queue:
            for other in sorted(graph.neighbours(queue.popleft())):
                if other != center and other in members and other not in groups:
                    groups[other] = number
                    queue.append(other)
    return groups
