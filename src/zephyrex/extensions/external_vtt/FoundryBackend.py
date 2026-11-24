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


COMBAT_FIELDS = ["active", "round", "turn", "scene"]
TOKEN_FIELDS = ["actorId", "x", "y", "hidden", "disposition"]
DISPOSITION_OF = {name: value for value, name in DISPOSITIONS.items()}
SESSIONS_FOLDER = "Sessions"
DOCUMENTS_FOLDERS = ("documents", "campaign documents", "handouts")
DAY_SECONDS = 86400


def _local(uuid: str) -> str:
    """A document's own id: the last part of its UUID."""
    return uuid.rsplit(".", 1)[-1]


def _first(ids: Any) -> str:
    if not isinstance(ids, list) or not ids:
        raise InvalidInputExternalError("the world's server created nothing")
    return str(ids[0])


async def _active_combat(world: str) -> Optional[Doc]:
    combats = await _vtt().vtt_list_documents(world, "Combat", fields=COMBAT_FIELDS)
    active = [c for c in combats if c.get("active")] or combats[:1]
    return active[0] if active else None


async def _combat(world: str, encounter_id: str) -> Doc:
    uuid = parse_fvtt_id(encounter_id)[1]
    if not uuid.startswith("Combat."):
        raise InvalidInputExternalError(f"{encounter_id!r} is not a Foundry combat")
    doc = await _vtt().vtt_get_document(world, uuid)
    return {**doc, "uuid": uuid, "id": doc.get("_id") or _local(uuid)}


async def _order(world: str, combat: Doc, campaign_id: Optional[str]) -> Doc:
    """A combat as the app's fights read: in initiative order, the one whose
    turn it is, who has yet to roll."""
    combat_uuid = _uuid(combat, "Combat")
    combatants = await _vtt().vtt_list_embedded(
        world, combat_uuid, "Combatant", fields=["actorId", "initiative", "defeated"]
    )
    ordered = sorted(combatants, key=lambda c: -(c.get("initiative") or float("-inf")))
    entries = [
        {
            "id": fvtt_id(
                world, f"{combat_uuid}.Combatant.{c.get('id') or c.get('_id')}"
            ),
            "person_id": (
                fvtt_id(world, f"Actor.{c['actorId']}") if c.get("actorId") else None
            ),
            "name": c.get("name") or "",
            "initiative": c.get("initiative"),
            "defeated": bool(c.get("defeated")),
        }
        for c in ordered
    ]
    round_number = int(combat.get("round") or 0)
    turn = combat.get("turn")
    current = (
        entries[turn]["id"]
        if round_number > 0 and isinstance(turn, int) and 0 <= turn < len(entries)
        else None
    )
    return {
        "id": fvtt_id(world, combat_uuid),
        "campaign_id": campaign_id,
        "status": combat.get("status") or ("running" if round_number > 0 else "setup"),
        "round": round_number,
        "turn": turn,
        "current_combatant_id": current,
        "combatants": entries,
        "order": entries,
        "open_choices": [],
        "unrolled": [e["id"] for e in entries if e["initiative"] is None],
    }


async def _tokens(world: str, scene_uuid: str) -> List[Doc]:
    tokens: List[Doc] = await _vtt().vtt_list_embedded(
        world, scene_uuid, "Token", fields=TOKEN_FIELDS
    )
    return tokens


def _disposition(value: Any) -> Optional[str]:
    return DISPOSITIONS.get(value) if isinstance(value, int) else None


def _token(world: str, scene_uuid: str, token: Doc) -> Doc:
    actor = token.get("actorId")
    return {
        "id": fvtt_id(
            world, f"{scene_uuid}.Token.{token.get('id') or token.get('_id')}"
        ),
        "scene_id": fvtt_id(world, scene_uuid),
        "entity_id": fvtt_id(world, f"Actor.{actor}") if actor else None,
        "name": token.get("name") or "",
        "x": token.get("x"),
        "y": token.get("y"),
        "hidden": bool(token.get("hidden")),
        "disposition": _disposition(token.get("disposition")),
    }


async def _scene_of(world: str, scene_id: str) -> str:
    uuid = parse_fvtt_id(scene_id)[1]
    if not uuid.startswith("Scene.") or uuid.count(".") != 1:
        raise InvalidInputExternalError(f"{scene_id!r} is not a Foundry scene")
    return uuid


def _actor_of(person_id: str) -> str:
    uuid = parse_fvtt_id(person_id)[1]
    if not uuid.startswith("Actor.") or uuid.count(".") != 1:
        raise InvalidInputExternalError(f"{person_id!r} is not a Foundry person")
    return uuid


async def _place(
    world: str, scene_uuid: str, actor_uuid: str, near: Optional[Doc] = None
) -> Doc:
    """A token for the actor on the scene: beside ``near``, else at the
    scene's centre."""
    actor = await _vtt().vtt_get_document(world, actor_uuid)
    if near is not None:
        x, y = int(near.get("x") or 0) + 100, int(near.get("y") or 0)
    else:
        scene = await _vtt().vtt_get_document(world, scene_uuid)
        x, y = int(scene.get("width") or 0) // 2, int(scene.get("height") or 0) // 2
    prototype = actor.get("prototypeToken") or {}
    token = {
        "actorId": _local(actor_uuid),
        "name": actor.get("name") or "",
        "x": x,
        "y": y,
        "disposition": prototype.get("disposition", 0),
    }
    created = await _vtt().vtt_create_documents(
        world, "Token", [token], parent_uuid=scene_uuid
    )
    return _token(world, scene_uuid, {**token, "id": _local(_first(created))})


async def _remove(world: str, scene_uuid: str, actor_uuid: str) -> int:
    actor = _local(actor_uuid)
    ids = [
        str(t.get("id") or t.get("_id"))
        for t in await _tokens(world, scene_uuid)
        if t.get("actorId") == actor
    ]
    if ids:
        await _vtt().vtt_delete_documents(world, "Token", ids, parent_uuid=scene_uuid)
    return len(ids)


def _clock(seconds: Any) -> Doc:
    total = int(seconds or 0)
    day, rest = divmod(total, DAY_SECONDS)
    return {
        "day": day + 1,
        "time": f"{rest // 3600:02d}:{rest % 3600 // 60:02d}",
        "seconds": total,
    }


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
        combat = await _active_combat(world)
        return None if combat is None else await _order(world, combat, campaign_id)

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

    # -- writes: like native ones in a campaign-mode world (no approval);
    # an assistant-mode world refuses them, its GM approving vtt_* writes
    # one by one instead.

    @classmethod
    @implements("rpg_core", "world_entities_matching", backend=FOUNDRY)
    async def foundry_world_entities_matching(
        cls,
        model_registry: Any,
        requester_id: str,
        campaign_id: str,
        text: str,
        limit: int = 20,
    ) -> List[Doc]:
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        needle = (text or "").strip().lower()
        found = [
            e
            for e in await _entities(world, KIND_OF_TYPE)
            if needle in e["name"].lower()
        ]
        return found[: max(0, int(limit or 0))]

    @classmethod
    @implements("rpg_core", "campaign_documents", backend=FOUNDRY)
    async def foundry_campaign_documents(
        cls, model_registry: Any, requester_id: str, campaign_id: str
    ) -> List[Doc]:
        """The pages of the journals in a top-level "Documents" (or
        "Handouts") folder."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        folders = await _folders(world)
        wanted = {
            fid
            for fid, name in folders.items()
            if name.strip().lower() in DOCUMENTS_FOLDERS
        }
        documents: List[Doc] = []
        for journal in await _vtt().vtt_list_documents(
            world, "JournalEntry", fields=["folder"]
        ):
            if journal.get("folder") not in wanted:
                continue
            entry = await _vtt().vtt_get_document(
                world, _uuid(journal, "JournalEntry"), embedded=True
            )
            for page in entry.get("pages") or []:
                documents.append(
                    {
                        "title": page.get("name") or journal.get("name") or "",
                        "text": str((page.get("text") or {}).get("content") or ""),
                    }
                )
        return documents

    @classmethod
    @implements("rpg_core", "ensure_person", backend=FOUNDRY)
    async def foundry_ensure_person(
        cls,
        model_registry: Any,
        requester_id: str,
        campaign_id: str,
        name: str,
        summary: Optional[str] = None,
    ) -> Doc:
        """The world's actor of that name, made (of the world's NPC type)
        when there is none."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        if not (name or "").strip():
            raise InvalidInputExternalError("a person has a name")
        for actor in await _vtt().vtt_list_documents(
            world, "Actor", name_filter=name, fields=ENTITY_FIELDS
        ):
            if actor.get("name") == name:
                return _entity(world, "Actor", actor, {}, {})
        status = await _vtt().vtt_status(world)
        actor_type = str(status.get("npc_actor_type") or "npc")
        created = await _vtt().vtt_create_documents(
            world, "Actor", [{"name": name, "type": actor_type}]
        )
        uuid = _first(created)
        uuid = uuid if uuid.startswith("Actor.") else f"Actor.{uuid}"
        return _entity(
            world, "Actor", {"uuid": uuid, "name": name, "type": actor_type}, {}, {}
        )

    @classmethod
    @implements("rpg_core", "update_person", backend=FOUNDRY)
    async def foundry_update_person(
        cls, model_registry: Any, requester_id: str, person_id: str, fields: Doc
    ) -> Doc:
        """A person's name and picture (what Foundry keeps on an actor)."""
        world = await _world_of(requester_id, person_id)
        uuid = _actor_of(person_id)
        change = {k: v for k, v in (fields or {}).items() if k in ("name", "img")}
        if change:
            await _vtt().vtt_update_documents(
                world, "Actor", [{"_id": _local(uuid), **change}]
            )
        doc = await _vtt().vtt_get_document(world, uuid)
        return _entity(world, "Actor", {**doc, "uuid": uuid}, {}, {})

    @classmethod
    @implements("rpg_core", "move_person", backend=FOUNDRY)
    async def foundry_move_person(
        cls,
        model_registry: Any,
        requester_id: str,
        person_id: str,
        location_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """A person goes to a place: their token leaves every other scene and
        stands on that one."""
        world = await _world_of(requester_id, person_id, location_id)
        actor_uuid = _actor_of(person_id)
        target = await _scene_of(world, location_id)
        for scene in await _vtt().vtt_list_documents(world, "Scene"):
            scene_uuid = _uuid(scene, "Scene")
            if scene_uuid != target:
                await _remove(world, scene_uuid, actor_uuid)
        on_target = [
            t
            for t in await _tokens(world, target)
            if t.get("actorId") == _local(actor_uuid)
        ]
        if not on_target:
            await _place(world, target, actor_uuid)
        return True

    @classmethod
    @implements("rpg_scene", "place_person", backend=FOUNDRY)
    async def foundry_place_person(
        cls,
        requester_id: str,
        scene_id: str,
        person_id: str,
        near_person_id: Optional[str] = None,
    ) -> Doc:
        world = await _world_of(requester_id, scene_id, person_id)
        scene_uuid = await _scene_of(world, scene_id)
        actor_uuid = _actor_of(person_id)
        tokens = await _tokens(world, scene_uuid)
        for token in tokens:
            if token.get("actorId") == _local(actor_uuid):
                return _token(world, scene_uuid, token)
        near = None
        if near_person_id:
            near_actor = _local(_actor_of(near_person_id))
            near = next((t for t in tokens if t.get("actorId") == near_actor), None)
        return await _place(world, scene_uuid, actor_uuid, near)

    @classmethod
    @implements("rpg_scene", "remove_person_from_scene", backend=FOUNDRY)
    async def foundry_remove_person_from_scene(
        cls, requester_id: str, scene_id: str, person_id: str
    ) -> Doc:
        world = await _world_of(requester_id, scene_id, person_id)
        removed = await _remove(
            world, await _scene_of(world, scene_id), _actor_of(person_id)
        )
        return {"removed": removed > 0}

    @classmethod
    @implements("rpg_scene", "set_scene_background", backend=FOUNDRY)
    async def foundry_set_scene_background(
        cls, requester_id: str, scene_id: str, link: Optional[str]
    ) -> Doc:
        world = await _world_of(requester_id, scene_id)
        scene_uuid = await _scene_of(world, scene_id)
        await _vtt().vtt_update_documents(
            world, "Scene", [{"_id": _local(scene_uuid), "background.src": link}]
        )
        scene = await _vtt().vtt_get_document(world, scene_uuid)
        return {
            "id": scene_id,
            "name": scene.get("name") or "",
            "location_id": scene_id,
            "background_url": link,
        }

    @classmethod
    @implements("rpg_combat", "start_encounter", backend=FOUNDRY)
    async def foundry_start_encounter(
        cls,
        requester_id: str,
        campaign_id: str,
        scene_id: str,
        person_ids: List[str],
        dispositions: Optional[Dict[str, str]] = None,
    ) -> Doc:
        """A combat on the scene with these people, their tokens' dispositions
        set; it begins (round 1) with start_combat."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        scene_uuid = await _scene_of(world, scene_id)
        if await _world_of(requester_id, scene_id, *person_ids) != world:
            raise InvalidInputExternalError("the scene and people are another world's")
        combat_uuid = _first(
            await _vtt().vtt_create_documents(
                world, "Combat", [{"scene": _local(scene_uuid), "active": True}]
            )
        )
        combat_uuid = (
            combat_uuid
            if combat_uuid.startswith("Combat.")
            else f"Combat.{combat_uuid}"
        )
        tokens = {t.get("actorId"): t for t in await _tokens(world, scene_uuid)}
        combatants: List[Doc] = []
        token_changes: List[Doc] = []
        for person_id in person_ids:
            actor = _local(_actor_of(person_id))
            token = tokens.get(actor)
            if token is None:
                token = await _place(world, scene_uuid, f"Actor.{actor}")
                token = {"id": _local(parse_fvtt_id(token["id"])[1]), "actorId": actor}
            token_id = str(token.get("id") or token.get("_id"))
            combatants.append(
                {"actorId": actor, "tokenId": token_id, "sceneId": _local(scene_uuid)}
            )
            wanted = (dispositions or {}).get(person_id)
            if wanted in DISPOSITION_OF:
                token_changes.append(
                    {"_id": token_id, "disposition": DISPOSITION_OF[wanted]}
                )
        await _vtt().vtt_create_documents(
            world, "Combatant", combatants, parent_uuid=combat_uuid
        )
        if token_changes:
            await _vtt().vtt_update_documents(
                world, "Token", token_changes, parent_uuid=scene_uuid
            )
        return await _order(
            world, await _combat(world, fvtt_id(world, combat_uuid)), campaign_id
        )

    @classmethod
    @implements("rpg_combat", "start_combat", backend=FOUNDRY)
    async def foundry_start_combat(cls, requester_id: str, encounter_id: str) -> Doc:
        world = await _world_of(requester_id, encounter_id)
        combat = await _combat(world, encounter_id)
        await _vtt().vtt_update_documents(
            world, "Combat", [{"_id": combat["id"], "round": 1, "turn": 0}]
        )
        return await _order(world, {**combat, "round": 1, "turn": 0}, None)

    @classmethod
    @implements("rpg_combat", "next_turn", backend=FOUNDRY)
    async def foundry_next_turn(cls, requester_id: str, encounter_id: str) -> Doc:
        """The next combatant's turn; after the last, the next round."""
        world = await _world_of(requester_id, encounter_id)
        combat = await _combat(world, encounter_id)
        count = len(
            await _vtt().vtt_list_embedded(
                world, combat["uuid"], "Combatant", fields=["actorId"]
            )
        )
        round_number = max(1, int(combat.get("round") or 0))
        turn = int(combat.get("turn") or 0) + 1
        if count and turn >= count:
            turn, round_number = 0, round_number + 1
        await _vtt().vtt_update_documents(
            world,
            "Combat",
            [{"_id": combat["id"], "round": round_number, "turn": turn}],
        )
        return await _order(
            world, {**combat, "round": round_number, "turn": turn}, None
        )

    @classmethod
    @implements("rpg_combat", "end_encounter", backend=FOUNDRY)
    async def foundry_end_encounter(cls, requester_id: str, encounter_id: str) -> Doc:
        """The fight as it ended; the combat is deleted (backed up first)."""
        world = await _world_of(requester_id, encounter_id)
        combat = await _combat(world, encounter_id)
        order = await _order(world, combat, None)
        await _vtt().vtt_delete_documents(world, "Combat", [combat["id"]])
        return {**order, "status": "ended", "current_combatant_id": None}

    @classmethod
    @implements("rpg_combat", "set_disposition", backend=FOUNDRY)
    async def foundry_set_disposition(
        cls,
        requester_id: str,
        encounter_id: str,
        person_id: str,
        disposition: str,
        hostile_to: Optional[List[str]] = None,
    ) -> Doc:
        """The person's token's disposition on the fight's scene."""
        world = await _world_of(requester_id, encounter_id, person_id)
        if disposition not in DISPOSITION_OF:
            raise InvalidInputExternalError(
                f"disposition is one of {', '.join(sorted(DISPOSITION_OF))}"
            )
        combat = await _combat(world, encounter_id)
        scene = combat.get("scene")
        if scene:
            scene_uuid = f"Scene.{scene}"
            actor = _local(_actor_of(person_id))
            changes = [
                {
                    "_id": str(t.get("id") or t.get("_id")),
                    "disposition": DISPOSITION_OF[disposition],
                }
                for t in await _tokens(world, scene_uuid)
                if t.get("actorId") == actor
            ]
            if changes:
                await _vtt().vtt_update_documents(
                    world, "Token", changes, parent_uuid=scene_uuid
                )
        return await _order(world, combat, None)

    @classmethod
    @implements("rpg_core", "session_report", backend=FOUNDRY)
    async def foundry_session_report(
        cls,
        model_registry: Any,
        requester_id: str,
        campaign_id: str,
        title: str,
        body: str,
        occurred_at: Optional[str] = None,
        mentions: Optional[List[str]] = None,
    ) -> Doc:
        """A journal entry in the world's "Sessions" folder (when it has one),
        its report one text page."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        folders = await _folders(world)
        folder = next(
            (fid for fid, name in folders.items() if name == SESSIONS_FOLDER), None
        )
        entry: Doc = {"name": title or "Session"}
        if folder:
            entry["folder"] = folder
        uuid = _first(await _vtt().vtt_create_documents(world, "JournalEntry", [entry]))
        uuid = uuid if uuid.startswith("JournalEntry.") else f"JournalEntry.{uuid}"
        await _vtt().vtt_create_documents(
            world,
            "JournalEntryPage",
            [
                {
                    "name": title or "Session",
                    "type": "text",
                    "text": {"content": body or ""},
                }
            ],
            parent_uuid=uuid,
        )
        return {"id": fvtt_id(world, uuid)}

    @classmethod
    @implements("rpg_core", "advance_clock", backend=FOUNDRY)
    async def foundry_advance_clock(
        cls,
        model_registry: Any,
        requester_id: str,
        campaign_id: str,
        minutes: int,
        reason: Optional[str] = None,
    ) -> Doc:
        """The world's clock (core.time, in seconds) moved on."""
        _, world = await campaign_world(requester_id, campaign_id=campaign_id)
        if int(minutes) < 0:
            raise InvalidInputExternalError("the clock only moves forward")
        now = int(await _vtt().vtt_get_setting(world, "core.time") or 0)
        later = now + int(minutes) * 60
        await _vtt().vtt_set_setting(world, "core.time", later)
        return _clock(later)
