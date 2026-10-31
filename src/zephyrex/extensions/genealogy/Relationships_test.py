# SPDX-License-Identifier: AGPL-3.0-or-later
"""The registered relationship kinds and the checks they impose."""

import pytest

from zephyrex.extensions.genealogy.Relationships import (
    ACQUAINTANCE,
    FRIENDSHIP,
    PARTNERSHIP,
    RELATIONSHIP_KINDS,
    SIBLING_OF,
    RelationshipError,
    RelationshipKind,
    check_relationship,
    describe_discriminators,
    register_relationship_kind,
    symmetric_kinds,
)


def test_core_social_kinds_are_symmetric_and_ancestry_is_not():
    assert symmetric_kinds() == tuple(
        sorted((ACQUAINTANCE, FRIENDSHIP, PARTNERSHIP, SIBLING_OF))
    )
    assert RELATIONSHIP_KINDS["ancestry"].symmetric is False
    assert RELATIONSHIP_KINDS["mentorship"].symmetric is False


@pytest.mark.parametrize(
    "kind, discriminator",
    [
        (PARTNERSHIP, "nesting"),
        (PARTNERSHIP, "marriage"),
        (FRIENDSHIP, "queerplatonic"),
        (ACQUAINTANCE, "colleague"),
        (SIBLING_OF, "half"),
        ("ancestry", "adopted"),
        (FRIENDSHIP, None),
        (PARTNERSHIP, "lover"),
        ("mentorship", "teacher"),
    ],
)
def test_a_known_discriminator_passes(kind, discriminator):
    check_relationship(kind, discriminator, "a", "b")


@pytest.mark.parametrize(
    "kind, discriminator",
    [(PARTNERSHIP, "biological"), (FRIENDSHIP, "nesting"), ("ancestry", "best_friend")],
)
def test_a_discriminator_of_another_kind_is_refused(kind, discriminator):
    with pytest.raises(RelationshipError, match=discriminator):
        check_relationship(kind, discriminator, "a", "b")


def test_a_registered_kind_cannot_relate_someone_to_themselves():
    with pytest.raises(RelationshipError, match="two different people"):
        check_relationship(PARTNERSHIP, None, "a", "a")


def test_an_unregistered_kind_is_stored_unchecked():
    """Downstream kinds (rpg_state's member_of) keep their own taxonomies."""
    check_relationship("member_of", "treasurer", "a", "a")


def test_registering_a_kind_twice_differently_raises():
    with pytest.raises(ValueError, match="already registered"):
        register_relationship_kind(RelationshipKind(FRIENDSHIP, symmetric=False))


def test_registering_the_same_kind_again_is_harmless():
    register_relationship_kind(RELATIONSHIP_KINDS[FRIENDSHIP])
    assert RELATIONSHIP_KINDS[FRIENDSHIP].symmetric is True


def test_the_description_lists_every_kinds_discriminators():
    described = describe_discriminators()
    assert "nesting" in described and "for partnership" in described
    assert "queerplatonic" in described and "for friendship" in described
