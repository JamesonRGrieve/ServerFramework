# SPDX-License-Identifier: AGPL-3.0-or-later
"""A campaign kept in a Foundry world, behind the same abilities the app's
own wiki and tabletop offer (rpg_core, rpg_scene, rpg_combat, rpg_dice):
each is marked ``@implements(..., backend="foundry")``, and the owning
extension's backend selector sends a Foundry campaign's calls here
(``AbstractExtensionProvider.loaded_ability``).

The world's documents are the campaign: actors are its people, scenes its
places (and the table's current scene), journal entries its lore and
records, world items its items, the active combat its fight. Every id this
backend hands out names its world, ``fvtt:<world>:<Foundry UUID>``, so an
ability given only an id (a person, a scene) still knows where it lives.

Who may read: the wiki's ``rpg_core.campaign_backend`` answers, for the
requester, the campaign a world serves (or the world a campaign is on), and
nothing for someone who isn't in it. Every call asks it first.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from zephyrex.extensions.AbstractExtensionProvider import (
    implements,
    loaded_ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError

Doc = Dict[str, Any]
ID_PREFIX = "fvtt"
FOUNDRY = "foundry"
# Foundry's token dispositions, as the app names them.
DISPOSITIONS = {-1: "hostile", 0: "neutral", 1: "friendly", -2: "secret"}
# A world document's kind, by its type; a top-level folder named for a kind
# overrides it (until the companion module's flags name kinds).
KIND_OF_TYPE = {
    "Actor": "person",
    "Scene": "place",
    "JournalEntry": "lore",
    "Item": "item",
}
KIND_OF_FOLDER = {
    "people": "person",
    "persons": "person",
    "characters": "person",
    "places": "place",
    "locations": "place",
    "organisations": "organisation",
    "organizations": "organisation",
    "factions": "organisation",
    "items": "item",
    "lore": "lore",
    "records": "record",
}
CAMPAIGN_JOURNAL = "Campaign"


def fvtt_id(world_id: str, uuid: str) -> str:
    return f"{ID_PREFIX}:{world_id}:{uuid}"


def parse_fvtt_id(value: str) -> Tuple[str, str]:
    """``(world_id, Foundry UUID)`` of an id this backend handed out."""
    prefix, _, rest = (value or "").partition(":")
    world, _, uuid = rest.partition(":")
    if prefix != ID_PREFIX or not world or not uuid:
        raise InvalidInputExternalError(f"{value!r} is not a Foundry-backed id")
    return world, uuid


def is_fvtt_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(f"{ID_PREFIX}:")


def _registry() -> Any:
    from zephyrex.pydantic2.registry import ModelRegistry

    return ModelRegistry.attached()


async def _call(ability: Any, **arguments: Any) -> Any:
    """``ability`` with the arguments it takes (the wiki's may take fewer)."""
    import inspect

    accepted = inspect.signature(ability).parameters
    if not any(p.kind is p.VAR_KEYWORD for p in accepted.values()):
        arguments = {k: v for k, v in arguments.items() if k in accepted}
    result = ability(**arguments)
    return await result if inspect.isawaitable(result) else result


async def _ask(
    backend: Any,
    requester_id: str,
    campaign_id: Optional[str] = None,
    world_id: Optional[str] = None,
) -> Doc:
    found = await _call(
        backend,
        model_registry=_registry(),
        requester_id=requester_id,
        campaign_id=campaign_id,
        world_id=world_id,
    )
    return found if isinstance(found, dict) else {}


async def campaign_world(
    requester_id: str,
    campaign_id: Optional[str] = None,
    world_id: Optional[str] = None,
) -> Tuple[str, str]:
    """``(campaign_id, world_id)`` of a Foundry campaign the requester is
    in; invalid input for anyone else, or a campaign kept natively.

    The wiki resolves a campaign for routing whoever asks, so membership is
    its own question: an answer's ``member`` when it gives one, else the
    wiki's answer for the requester on that world (which is the requester's
    own campaign there, or none) must be this campaign."""
    backend = loaded_ability(_registry(), "rpg_core", "campaign_backend")
    if backend is None:
        raise InvalidInputExternalError(
            "Foundry campaigns need the wiki's rpg_core.campaign_backend"
        )
    refused = InvalidInputExternalError("No such Foundry campaign for this requester")
    if campaign_id:
        found = await _ask(backend, requester_id, campaign_id=campaign_id)
        if found.get("backend") != FOUNDRY or not found.get("world_id"):
            raise refused
        world = str(found["world_id"])
        if found.get("member") is True:
            return str(campaign_id), world
        if found.get("member") is False:
            raise refused
        mine = await _ask(backend, requester_id, world_id=world)
        if str(mine.get("campaign_id") or "") != str(campaign_id):
            raise refused
        return str(campaign_id), world
    mine = await _ask(backend, requester_id, world_id=world_id)
    if (
        mine.get("backend") != FOUNDRY
        or not mine.get("campaign_id")
        or str(mine.get("world_id") or world_id) != str(world_id)
    ):
        raise refused
    return str(mine["campaign_id"]), str(world_id)


async def _world_of(requester_id: str, *ids: str) -> str:
    """The one world these ids name, once the requester is checked into it."""
    worlds = {parse_fvtt_id(one)[0] for one in ids}
    if len(worlds) != 1:
        raise InvalidInputExternalError("ids from more than one Foundry world")
    (world,) = worlds
    await campaign_world(requester_id, world_id=world)
    return world


def _vtt() -> Any:
    from zephyrex.extensions.external_vtt.EXT_External_VTT import EXT_External_VTT

    return EXT_External_VTT


def _uuid(doc: Doc, document_type: str) -> str:
    return str(doc.get("uuid") or f"{document_type}.{doc.get('id') or doc.get('_id')}")


def _kind(document_type: str, folder_id: Optional[str], folders: Dict[str, str]) -> str:
    folder = folders.get(folder_id or "", "").strip().lower()
    return KIND_OF_FOLDER.get(folder, KIND_OF_TYPE.get(document_type, "lore"))


async def _folders(world: str) -> Dict[str, str]:
    rows = await _vtt().vtt_list_documents(world, "Folder", fields=["name"])
    return {
        str(row.get("id") or row.get("_id")): str(row.get("name") or "") for row in rows
    }


async def _token_places(world: str) -> Dict[str, str]:
    """Each actor's place: the scene its token stands in (fvtt ids)."""
    places: Dict[str, str] = {}
    for scene in await _vtt().vtt_list_documents(world, "Scene"):
        scene_uuid = _uuid(scene, "Scene")
        for token in await _vtt().vtt_list_embedded(
            world, scene_uuid, "Token", fields=["actorId"]
        ):
            actor = token.get("actorId")
            if actor:
                places.setdefault(f"Actor.{actor}", fvtt_id(world, scene_uuid))
    return places


def _person_kind(
    document_type: str, doc: Doc, folders: Dict[str, str]
) -> Optional[str]:
    """A person's own kind ("pc" or "npc"); None for anything else."""
    if _kind(document_type, doc.get("folder"), folders) != "person":
        return None
    return "pc" if doc.get("type") in PC_ACTOR_TYPES else "npc"


def _entity(
    world: str,
    document_type: str,
    doc: Doc,
    folders: Dict[str, str],
    places: Dict[str, str],
) -> Doc:
    uuid = _uuid(doc, document_type)
    disposition = (doc.get("prototypeToken") or {}).get("disposition")
    if disposition is None:
        disposition = doc.get("prototypeToken.disposition")
    return {
        "id": fvtt_id(world, uuid),
        "name": doc.get("name") or "",
        "kind": _kind(document_type, doc.get("folder"), folders),
        "aliases": [],
        "deleted": False,
        "canon": True,
        "location_id": places.get(uuid),
        "parent_id": None,
        "summary": None,
        "entry": None,
        "voice": None,
        "tts_voice": None,
        "person_kind": _person_kind(document_type, doc, folders),
        "disposition": (
            DISPOSITIONS.get(disposition) if disposition is not None else None
        ),
        "document_type": document_type,
        "uuid": uuid,
    }


ENTITY_FIELDS = ["folder", "type", "prototypeToken.disposition"]
# A player's actor, as the app's people name it; every other actor an NPC.
PC_ACTOR_TYPES = frozenset({"character"})


async def _entities(world: str, types: Iterable[str]) -> List[Doc]:
    folders = await _folders(world)
    places = await _token_places(world)
    entities: List[Doc] = []
    for document_type in types:
        fields = ENTITY_FIELDS if document_type == "Actor" else ["folder"]
        for doc in await _vtt().vtt_list_documents(world, document_type, fields=fields):
            entities.append(_entity(world, document_type, doc, folders, places))
    return entities


def _page_text(entry: Doc, name: str) -> Optional[str]:
    for page in entry.get("pages") or []:
        if str(page.get("name") or "").strip().lower() == name:
            text = page.get("text") or {}
            return str(text.get("content") or "") or None
    return None


def _active_scene(scenes: List[Doc]) -> Optional[Doc]:
    for scene in scenes:
        if scene.get("active"):
            return scene
    return None


class FoundryBackend:
    """The Foundry backend's implementations, mixed into EXT_External_VTT so
    they belong to a loaded extension."""

    @classmethod
    @implements("rpg_core", "world_entities", backend=FOUNDRY)
    async def foundry_world_entities(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> List[Doc]:
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        return await _entities(world, KIND_OF_TYPE)

    @classmethod
    @implements("rpg_core", "world_entities_by_id", backend=FOUNDRY)
    async def foundry_world_entities_by_id(
        cls, model_registry: Any, requester_id: str, ids: List[str]
    ) -> List[Doc]:
        if not ids:
            return []
        world = await _world_of(requester_id, *ids)
        folders = await _folders(world)
        places = await _token_places(world)
        found: List[Doc] = []
        for one in ids:
            uuid = parse_fvtt_id(one)[1]
            document_type = uuid.split(".", 1)[0]
            if document_type not in KIND_OF_TYPE:
                continue
            doc = await _vtt().vtt_get_document(world, uuid)
            if doc:
                found.append(
                    _entity(
                        world, document_type, {**doc, "uuid": uuid}, folders, places
                    )
                )
        return found

    @classmethod
    @implements("rpg_core", "campaign_framing", backend=FOUNDRY)
    async def foundry_campaign_framing(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> Doc:
        """The world's "Campaign" journal: its Premise and Opening pages; the
        default place is the active scene."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        framing: Doc = {"premise": None, "opening": None, "default_location_id": None}
        journals = await _vtt().vtt_list_documents(
            world, "JournalEntry", name_filter=CAMPAIGN_JOURNAL
        )
        named = [j for j in journals if j.get("name") == CAMPAIGN_JOURNAL]
        if named:
            entry = await _vtt().vtt_get_document(
                world, _uuid(named[0], "JournalEntry"), embedded=True
            )
            framing["premise"] = _page_text(entry, "premise")
            framing["opening"] = _page_text(entry, "opening")
        scene = _active_scene(
            await _vtt().vtt_list_documents(world, "Scene", fields=["active"])
        )
        if scene is not None:
            framing["default_location_id"] = fvtt_id(world, _uuid(scene, "Scene"))
        return framing

    @classmethod
    @implements("rpg_scene", "current_scene", backend=FOUNDRY)
    async def foundry_current_scene(
        cls, requester_id: str, campaign_id: str
    ) -> Optional[Doc]:
        """The world's active scene; it is its own place."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        scene = _active_scene(
            await _vtt().vtt_list_documents(world, "Scene", fields=["active"])
        )
        if scene is None:
            return None
        scene_id = fvtt_id(world, _uuid(scene, "Scene"))
        return {
            "id": scene_id,
            "name": scene.get("name") or "",
            "campaign_id": campaign_id,
            "location_id": scene_id,
        }

    @classmethod
    @implements("rpg_scene", "scene_tokens", backend=FOUNDRY)
    async def foundry_scene_tokens(cls, requester_id: str, scene_id: str) -> List[Doc]:
        world = await _world_of(requester_id, scene_id)
        scene_uuid = parse_fvtt_id(scene_id)[1]
        tokens = await _vtt().vtt_list_embedded(
            world,
            scene_uuid,
            "Token",
            fields=["actorId", "x", "y", "hidden", "disposition"],
        )
        return [
            {
                "id": fvtt_id(
                    world, f"{scene_uuid}.Token.{token.get('id') or token.get('_id')}"
                ),
                "entity_id": (
                    fvtt_id(world, f"Actor.{token['actorId']}")
                    if token.get("actorId")
                    else None
                ),
                "name": token.get("name") or "",
                "x": token.get("x"),
                "y": token.get("y"),
                "hidden": bool(token.get("hidden")),
                "disposition": DISPOSITIONS.get(token.get("disposition")),
            }
            for token in tokens
        ]

    @classmethod
    @implements("rpg_combat", "current_encounter", backend=FOUNDRY)
    async def foundry_current_encounter(
        cls, requester_id: str, campaign_id: str
    ) -> Optional[Doc]:
        """The world's active combat: running once its first round began."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        combats = await _vtt().vtt_list_documents(
            world, "Combat", fields=["active", "round", "turn"]
        )
        active = [c for c in combats if c.get("active")] or combats[:1]
        if not active:
            return None
        combat = active[0]
        combat_uuid = _uuid(combat, "Combat")
        combatants = await _vtt().vtt_list_embedded(
            world,
            combat_uuid,
            "Combatant",
            fields=["actorId", "initiative", "defeated"],
        )
        round_number = int(combat.get("round") or 0)
        return {
            "id": fvtt_id(world, combat_uuid),
            "campaign_id": campaign_id,
            "status": "running" if round_number > 0 else "setup",
            "round": round_number,
            "turn": combat.get("turn"),
            "order": [
                {
                    "id": fvtt_id(
                        world, f"{combat_uuid}.Combatant.{c.get('id') or c.get('_id')}"
                    ),
                    "person_id": (
                        fvtt_id(world, f"Actor.{c['actorId']}")
                        if c.get("actorId")
                        else None
                    ),
                    "name": c.get("name") or "",
                    "initiative": c.get("initiative"),
                    "defeated": bool(c.get("defeated")),
                }
                for c in sorted(
                    combatants,
                    key=lambda c: -(c.get("initiative") or float("-inf")),
                )
            ],
        }

    @classmethod
    @implements("rpg_dice", "character_sheet", backend=FOUNDRY)
    async def foundry_character_sheet(cls, requester_id: str, person_id: str) -> Doc:
        """The actor's sheet as its system keeps it, with its items."""
        world = await _world_of(requester_id, person_id)
        uuid = parse_fvtt_id(person_id)[1]
        actor = await _vtt().vtt_get_document(world, uuid, embedded=True)
        return {
            "id": person_id,
            "name": actor.get("name") or "",
            "type": actor.get("type"),
            "img": actor.get("img"),
            "system": actor.get("system") or {},
            "items": [
                {
                    "id": fvtt_id(world, f"{uuid}.Item.{item.get('_id')}"),
                    "name": item.get("name") or "",
                    "type": item.get("type"),
                    "system": item.get("system") or {},
                }
                for item in actor.get("items") or []
            ],
        }
