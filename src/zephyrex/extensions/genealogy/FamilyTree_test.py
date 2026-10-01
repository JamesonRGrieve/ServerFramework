# SPDX-License-Identifier: AGPL-3.0-or-later
"""Family-tree algorithms over a known family.

  grandma ─┬─ grandpa
           │
   ┌───────┴───────┐
  mum             aunt ── (partner)
   │ (with dad)    │
 ┌─┴──┐          cousin
ann   ben            │
                 cousin_kid
ben was adopted by step_dad too.
"""

import pytest

from zephyrex.extensions.genealogy.FamilyTree import FamilyTree, ParentEdge


def _family(roles=None) -> FamilyTree:
    edges = [
        ParentEdge("grandma", "mum", "biological"),
        ParentEdge("grandpa", "mum", "biological"),
        ParentEdge("grandma", "aunt", "biological"),
        ParentEdge("grandpa", "aunt", "biological"),
        ParentEdge("mum", "ann", "biological"),
        ParentEdge("dad", "ann", "biological"),
        ParentEdge("mum", "ben", "biological"),
        ParentEdge("dad", "ben", "biological"),
        ParentEdge("step_dad", "ben", "adopted"),
        ParentEdge("aunt", "cousin", "biological"),
        ParentEdge("cousin", "cousin_kid", "biological"),
    ]
    return FamilyTree(edges, roles)


class TestWalks:
    def test_ancestors_with_generations(self):
        assert _family().ancestors("ann") == {
            "mum": 1,
            "dad": 1,
            "grandma": 2,
            "grandpa": 2,
        }

    def test_descendants_with_generations(self):
        assert _family().descendants("grandma") == {
            "mum": 1,
            "aunt": 1,
            "ann": 2,
            "ben": 2,
            "cousin": 2,
            "cousin_kid": 3,
        }

    def test_generation_limit(self):
        assert _family().ancestors("ann", generations=1) == {"mum": 1, "dad": 1}

    def test_many_parents(self):
        assert _family().parents("ben") == {"mum", "dad", "step_dad"}

    def test_roles_limit_the_tree(self):
        assert _family(roles={"biological"}).parents("ben") == {"mum", "dad"}

    def test_a_cycle_terminates(self):
        tree = FamilyTree([ParentEdge("a", "b"), ParentEdge("b", "a")])
        assert tree.ancestors("a") == {"b": 1}

    def test_self_parentage_is_ignored(self):
        assert FamilyTree([ParentEdge("a", "a")]).ancestors("a") == {}


class TestKinship:
    @pytest.mark.parametrize(
        "a, b, common, up_a, up_b, degree, cousin, removed",
        [
            ("ann", "ben", ("dad", "mum"), 1, 1, 2, 0, 0),  # siblings
            ("ann", "cousin", ("grandma", "grandpa"), 2, 2, 4, 1, 0),  # first cousins
            (
                "ann",
                "cousin_kid",
                ("grandma", "grandpa"),
                2,
                3,
                5,
                1,
                1,
            ),  # once removed
            ("ann", "aunt", ("grandma", "grandpa"), 2, 1, 3, 0, 1),  # niece and aunt
            ("grandma", "ann", ("grandma",), 0, 2, 2, 0, 2),  # lineal
        ],
    )
    def test_relationships(self, a, b, common, up_a, up_b, degree, cousin, removed):
        kinship = _family().kinship(a, b)
        assert kinship is not None
        assert kinship.common_ancestors == common
        assert (kinship.up_from_a, kinship.up_from_b) == (up_a, up_b)
        assert (kinship.degree, kinship.cousin, kinship.removed) == (
            degree,
            cousin,
            removed,
        )

    def test_lineal(self):
        assert _family().kinship("grandma", "ann").lineal
        assert not _family().kinship("ann", "ben").lineal

    def test_unrelated(self):
        assert _family().kinship("ann", "step_dad") is None

    def test_adoption_relates_only_when_counted(self):
        assert _family().kinship("step_dad", "ben") is not None
        assert _family(roles={"biological"}).kinship("step_dad", "ben") is None

    def test_most_recent_common_ancestors(self):
        assert _family().most_recent_common_ancestors("ann", "cousin") == [
            "grandma",
            "grandpa",
        ]
        assert _family().most_recent_common_ancestors("ann", "step_dad") == []
