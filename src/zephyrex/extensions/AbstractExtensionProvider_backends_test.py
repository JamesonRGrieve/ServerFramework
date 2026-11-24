# SPDX-License-Identifier: AGPL-3.0-or-later
"""One ability name, several backends: an extension's ability is served
from elsewhere for the calls its backend selector assigns there (a campaign
kept in Foundry rather than the app's own tables), and the caller, reaching
it through ``loaded_ability``, never branches."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional

from zephyrex.extensions.AbstractExtensionProvider import (
    ability,
    ability_backends,
    backend_selector,
    implements,
    loaded_ability,
)

FOUNDRY_CAMPAIGNS = {"c-foundry"}


class Native:
    name = "rpg_core"

    @classmethod
    @ability("world_entities")
    async def world_entities(
        cls, campaign_id: str, kind: str = "person"
    ) -> Dict[str, Any]:
        return {"from": "native", "campaign": campaign_id, "kind": kind}

    @classmethod
    @ability("lore_search")
    def lore_search(cls, campaign_id: str, query: str) -> Dict[str, Any]:
        return {"from": "native", "query": query}

    @classmethod
    @backend_selector
    def backend(cls, ability_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        if arguments.get("campaign_id") in FOUNDRY_CAMPAIGNS:
            return "foundry"
        return None


class Foundry:
    name = "external_vtt"

    @classmethod
    @implements("rpg_core", "world_entities", backend="foundry")
    async def world_entities(
        cls, campaign_id: str, kind: str = "person"
    ) -> Dict[str, Any]:
        return {"from": "foundry", "campaign": campaign_id, "kind": kind}


class Selectorless:
    name = "plain"

    @classmethod
    @ability("world_entities")
    async def world_entities(cls, campaign_id: str) -> str:
        return "plain"


def _registry(*extensions: type) -> Any:
    return SimpleNamespace(
        extension_registry=SimpleNamespace(extensions=list(extensions))
    )


async def test_a_campaigns_backend_serves_its_calls():
    world_entities = loaded_ability(
        _registry(Native, Foundry), "rpg_core", "world_entities"
    )
    assert world_entities is not None
    assert await world_entities(campaign_id="c-foundry", kind="place") == {
        "from": "foundry",
        "campaign": "c-foundry",
        "kind": "place",
    }
    assert (await world_entities(campaign_id="c-native"))["from"] == "native"


async def test_an_ability_no_backend_implements_is_the_extensions_own():
    lore_search = loaded_ability(_registry(Native, Foundry), "rpg_core", "lore_search")
    assert lore_search is not None
    # Not a dispatcher: the plain classmethod, sync as it was.
    assert lore_search(campaign_id="c-foundry", query="Vell") == {
        "from": "native",
        "query": "Vell",
    }


async def test_without_the_backend_loaded_it_is_the_extensions_own():
    world_entities = loaded_ability(_registry(Native), "rpg_core", "world_entities")
    assert world_entities is not None
    assert (await world_entities(campaign_id="c-foundry"))["from"] == "native"


async def test_without_a_selector_a_backend_is_never_chosen():
    class Elsewhere:
        name = "elsewhere"

        @classmethod
        @implements("plain", "world_entities", backend="x")
        async def world_entities(cls, campaign_id: str) -> str:
            return "elsewhere"

    world_entities = loaded_ability(
        _registry(Selectorless, Elsewhere), "plain", "world_entities"
    )
    assert world_entities is not None
    assert await world_entities(campaign_id="c") == "plain"


def test_the_backends_of_an_ability():
    assert set(
        ability_backends(_registry(Native, Foundry), "rpg_core", "world_entities")
    ) == {"foundry"}
    assert ability_backends(_registry(Native, Foundry), "rpg_core", "lore_search") == {}


def test_an_extension_not_loaded_has_no_ability():
    assert loaded_ability(_registry(Foundry), "rpg_core", "world_entities") is None


def test_a_dispatcher_reads_as_the_native_ability():
    import inspect

    from zephyrex.extensions.ai_agents.AbilityInvoker import ability_to_tool_schema

    world_entities = loaded_ability(
        _registry(Native, Foundry), "rpg_core", "world_entities"
    )
    assert world_entities is not None
    assert list(inspect.signature(world_entities).parameters) == ["campaign_id", "kind"]
    native = loaded_ability(_registry(Native), "rpg_core", "world_entities")
    assert ability_to_tool_schema(
        "world_entities", world_entities
    ) == ability_to_tool_schema("world_entities", native)


class Positional:
    """A native extension called positionally, with a sync ability."""

    name = "rpg_core"

    @classmethod
    @ability("world_entities")
    async def world_entities(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> str:
        return "native"

    @classmethod
    @ability("current_attitudes")
    def current_attitudes(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> str:
        return "native"

    @classmethod
    @backend_selector
    def backend(cls, ability_name: str, arguments: Dict[str, Any]) -> Optional[str]:
        return "foundry" if arguments.get("campaign_id") in FOUNDRY_CAMPAIGNS else None


class PositionalFoundry:
    name = "external_vtt"

    @classmethod
    @implements("rpg_core", "world_entities", backend="foundry")
    async def world_entities(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> str:
        return "foundry"

    @classmethod
    @implements("rpg_core", "current_attitudes", backend="foundry")
    async def current_attitudes(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> str:
        return "foundry, awaited for a sync caller"


async def test_a_positional_call_reaches_its_backend():
    world_entities = loaded_ability(
        _registry(Positional, PositionalFoundry), "rpg_core", "world_entities"
    )
    assert world_entities is not None
    assert await world_entities(None, "gm", "c-foundry") == "foundry"
    assert await world_entities(None, "gm", "c-native") == "native"


def test_a_sync_ability_stays_sync_for_its_callers():
    import inspect

    attitudes = loaded_ability(
        _registry(Positional, PositionalFoundry), "rpg_core", "current_attitudes"
    )
    assert attitudes is not None and not inspect.iscoroutinefunction(attitudes)
    assert attitudes(None, "gm", "c-native") == "native"
    assert attitudes(None, "gm", "c-foundry") == "foundry, awaited for a sync caller"


async def test_a_sync_ability_stays_sync_inside_a_running_loop():
    attitudes = loaded_ability(
        _registry(Positional, PositionalFoundry), "rpg_core", "current_attitudes"
    )
    assert attitudes is not None
    assert attitudes(None, "gm", "c-foundry") == "foundry, awaited for a sync caller"
