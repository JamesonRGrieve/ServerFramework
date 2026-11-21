# SPDX-License-Identifier: AGPL-3.0-or-later
"""The plural a resource carries on the wire: never the word itself, unless declared so."""

import pytest

from zephyrex.lib.Environment import plural_noun


@pytest.mark.parametrize(
    ("word", "plural"),
    [
        # Classical Latin gives these back unchanged, so a list envelope read as a single record.
        ("status", "statuses"),
        ("entity_status", "entity_statuses"),
        ("quest_status", "quest_statuses"),
        ("apparatus", "apparatuses"),
        ("EntityStatus", "EntityStatuses"),
        # Everything else keeps the plural apps already use on the wire.
        ("persona", "personae"),
        ("lorebook_entry", "lorebook_entries"),
        ("index", "indices"),
        ("user", "users"),
        # Declared invariants stay invariant.
        ("metadata", "metadata"),
        ("user_metadata", "user_metadata"),
    ],
)
def test_a_plural_differs_from_its_word_unless_declared(word: str, plural: str) -> None:
    assert plural_noun(word) == plural


def test_a_table_name_follows_the_same_plural() -> None:
    from zephyrex.lib.AbstractPydantic2 import default_name_processor

    assert default_name_processor.generate_resource_name("StatusModel") == "statuses"
    assert (
        default_name_processor.generate_resource_name("EntityStatusModel")
        == "entity_statuses"
    )
    assert default_name_processor.generate_resource_name("MetadataModel") == "metadata"
