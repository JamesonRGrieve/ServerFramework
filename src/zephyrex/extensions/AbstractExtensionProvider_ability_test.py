# SPDX-License-Identifier: AGPL-3.0-or-later
"""loaded_ability: one extension reaching another's ability by name, without importing it, and
only when that extension is loaded in the app."""

from types import SimpleNamespace

from zephyrex.extensions.AbstractExtensionProvider import loaded_ability
from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations


def _app_with(*extensions: type) -> SimpleNamespace:
    """A model registry whose app loaded exactly ``extensions``."""
    return SimpleNamespace(
        extension_registry=SimpleNamespace(extensions=list(extensions))
    )


def test_a_loaded_extensions_ability_is_found_by_name() -> None:
    found = loaded_ability(
        _app_with(EXT_Conversations), "conversations", "list_conversations"
    )
    assert found is not None
    assert found == EXT_Conversations.list_conversations


def test_none_when_the_extension_or_the_ability_is_not_there() -> None:
    assert loaded_ability(_app_with(), "conversations", "list_conversations") is None
    assert (
        loaded_ability(_app_with(EXT_Conversations), "conversations", "no_such_ability")
        is None
    )
    assert (
        loaded_ability(_app_with(EXT_Conversations), "rpg_core", "list_conversations")
        is None
    )
    assert (
        loaded_ability(SimpleNamespace(), "conversations", "list_conversations") is None
    )
