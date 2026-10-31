# SPDX-License-Identifier: AGPL-3.0-or-later
"""Merging linked trees over a small polycule.

Sam's tree:   sam ══ alex (linked)        sam ══ jordan (ghost)
Alex's tree:  alex ══ sam (linked)        alex ══ priya (ghost, named)
              priya ══ riley (ghost)      alex ══ uncle (ghost, hidden)
Mallory's tree links Sam, but Sam never linked Mallory.
"""

from typing import Dict

import pytest

from zephyrex.extensions.genealogy.MergedTrees import Tree, TreePerson, merge
from zephyrex.extensions.genealogy.Relationships import PARTNERSHIP
from zephyrex.extensions.genealogy.SocialGraph import Tie

KINDS = [PARTNERSHIP]


def _tree(owner: str, people, ties) -> Tree:
    persons = {p.id: p for p in people}
    return Tree(
        owner=owner,
        persons=persons,
        ties=[Tie(a, b, PARTNERSHIP, d) for a, b, d in ties],
    )


def _trees() -> Dict[str, Tree]:
    return {
        "sam": _tree(
            "sam",
            [
                TreePerson("s-sam", "sam", "Sam", linked_user_id="sam"),
                TreePerson("s-alex", "sam", "My Alex", linked_user_id="alex"),
                TreePerson("s-jordan", "sam", "Jordan"),
            ],
            [("s-sam", "s-alex", "nesting"), ("s-sam", "s-jordan", "dating")],
        ),
        "alex": _tree(
            "alex",
            [
                TreePerson(
                    "a-alex", "alex", "Alex", linked_user_id="alex", visibility="named"
                ),
                TreePerson("a-sam", "alex", "Sam R", linked_user_id="sam"),
                TreePerson("a-priya", "alex", "Priya", visibility="named"),
                TreePerson("a-riley", "alex", "Riley"),
                TreePerson("a-uncle", "alex", "Uncle", visibility="hidden"),
            ],
            [
                ("a-alex", "a-sam", "nesting"),
                ("a-alex", "a-priya", "anchor"),
                ("a-priya", "a-riley", "casual"),
                ("a-alex", "a-uncle", "liaison"),
            ],
        ),
        "mallory": _tree(
            "mallory",
            [
                TreePerson("m-mallory", "mallory", "Mallory", linked_user_id="mallory"),
                TreePerson("m-sam", "mallory", "Sam", linked_user_id="sam"),
            ],
            [("m-mallory", "m-sam", "dating")],
        ),
    }


def _merged(viewer: str):
    trees = _trees()
    return merge(
        trees[viewer], lambda owner: trees.get(owner, Tree(owner=owner)), KINDS
    )


def _by_key(graph):
    return {node.key: node for node in graph.nodes}


def test_mutually_linked_trees_merge_into_one_polycule():
    graph = _merged("sam")
    nodes = _by_key(graph)
    assert graph.center == "user:sam"
    assert set(nodes) == {
        "user:sam",
        "user:alex",
        "person:s-jordan",
        "person:a-priya",
        "person:a-riley",
    }
    assert nodes["person:a-priya"].distance == 2
    assert nodes["person:a-riley"].distance == 3


def test_the_viewer_sees_their_own_records_as_they_wrote_them():
    nodes = _by_key(_merged("sam"))
    alex = nodes["user:alex"]
    assert (alex.name, alex.mine, alex.person_id, alex.user_id) == (
        "My Alex",
        True,
        "s-alex",
        "alex",
    )
    assert nodes["person:s-jordan"].name == "Jordan"


def test_strangers_show_by_their_visibility_and_hidden_ones_not_at_all():
    nodes = _by_key(_merged("sam"))
    assert (nodes["person:a-priya"].name, nodes["person:a-priya"].mine) == (
        "Priya",
        False,
    )
    assert (nodes["person:a-riley"].name, nodes["person:a-riley"].visibility) == (
        None,
        "anonymous",
    )
    assert "person:a-uncle" not in nodes


def test_an_account_named_in_two_trees_is_one_node():
    graph = _merged("alex")
    sams = [node for node in graph.nodes if node.user_id == "sam"]
    assert len(sams) == 1 and sams[0].name == "Sam R"


def test_a_one_sided_link_does_not_merge():
    nodes = _by_key(_merged("mallory"))
    assert set(nodes) == {"user:mallory", "person:m-sam"}
    assert "person:s-jordan" not in nodes


def test_each_polycule_branching_off_the_viewer_is_its_own_group():
    nodes = _by_key(_merged("sam"))
    alex_side = {
        nodes[k].group for k in ("user:alex", "person:a-priya", "person:a-riley")
    }
    assert len(alex_side) == 1
    assert nodes["person:s-jordan"].group not in alex_side
    assert nodes["user:sam"].group is None


def test_ties_join_the_merged_nodes_once():
    """Sam and Alex both record their nesting partnership; merged, it is one tie."""
    graph = _merged("sam")
    between = [
        t
        for t in graph.ties
        if {t.person_id, t.target_person_id} == {"user:sam", "user:alex"}
    ]
    assert [(t.kind, t.discriminator) for t in between] == [(PARTNERSHIP, "nesting")]


def test_a_viewer_without_a_self_person_cannot_merge():
    with pytest.raises(ValueError, match="no self person"):
        merge(Tree(owner="nobody"), lambda owner: Tree(owner=owner), KINDS)
