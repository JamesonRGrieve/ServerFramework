# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Foundry-backed campaign read through the app's own ability names:
its people, places and lore from the world's actors, scenes and journals,
its current scene and tokens, its fight, a character's sheet. Every id
names its world; someone outside the campaign reads nothing. The world is
a stand-in here (a real one needs a real Foundry)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from zephyrex.extensions.external_vtt import FoundryBackend as backend
from zephyrex.extensions.external_vtt.EXT_External_VTT import EXT_External_VTT
from zephyrex.extensions.external_vtt.FoundryBackend import (
    fvtt_id,
    is_fvtt_id,
    parse_fvtt_id,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError

WORLD = "w1"
CAMPAIGN = "c1"
GM = "gm"

DOCUMENTS: Dict[str, List[Dict[str, Any]]] = {
    "Folder": [{"id": "f-org", "name": "Organisations"}],
    "Actor": [
        {
            "id": "a1",
            "uuid": "Actor.a1",
            "name": "Kol",
            "prototypeToken.disposition": 1,
        },
        {"id": "a2", "uuid": "Actor.a2", "name": "The Ordo", "folder": "f-org"},
    ],
    "Scene": [
        {"id": "s1", "uuid": "Scene.s1", "name": "Vell", "active": True},
        {"id": "s2", "uuid": "Scene.s2", "name": "Docks", "active": False},
    ],
    "JournalEntry": [{"id": "j1", "uuid": "JournalEntry.j1", "name": "Campaign"}],
    "Item": [],
    "Combat": [
        {"id": "k1", "uuid": "Combat.k1", "active": True, "round": 2, "turn": 0}
    ],
}
EMBEDDED = {
    ("Scene.s1", "Token"): [
        {
            "id": "t1",
            "actorId": "a1",
            "name": "Kol",
            "x": 100,
            "y": 50,
            "disposition": 1,
        }
    ],
    ("Scene.s2", "Token"): [],
    ("Combat.k1", "Combatant"): [
        {"id": "m1", "actorId": "a1", "name": "Kol", "initiative": 7},
        {"id": "m2", "actorId": "a2", "name": "Ordo", "initiative": 12},
    ],
}
FULL = {
    "JournalEntry.j1": {
        "name": "Campaign",
        "pages": [
            {"name": "Premise", "text": {"content": "<p>A city drowns.</p>"}},
            {"name": "Opening", "text": {"content": "<p>Rain.</p>"}},
        ],
    },
    "Actor.a1": {
        "_id": "a1",
        "name": "Kol",
        "type": "acolyte",
        "system": {"wounds": {"value": 9}},
        "items": [{"_id": "i1", "name": "Laspistol", "type": "weapon", "system": {}}],
    },
}


@pytest.fixture
def world(monkeypatch) -> List[tuple]:
    calls: List[tuple] = []

    async def vtt_list_documents(
        cls, world_id, document_type, name_filter="", fields=None, as_user=None
    ):
        calls.append(("list", world_id, document_type))
        docs = DOCUMENTS[document_type]
        return [d for d in docs if name_filter in d.get("name", "")]

    async def vtt_list_embedded(
        cls, world_id, parent_uuid, embedded_name, fields=None, as_user=None
    ):
        calls.append(("embedded", world_id, parent_uuid, embedded_name))
        return EMBEDDED[(parent_uuid, embedded_name)]

    async def vtt_get_document(cls, world_id, uuid, embedded=False, as_user=None):
        calls.append(("get", world_id, uuid))
        return FULL[uuid]

    stand_ins: List[Tuple[str, Any]] = [
        ("vtt_list_documents", vtt_list_documents),
        ("vtt_list_embedded", vtt_list_embedded),
        ("vtt_get_document", vtt_get_document),
    ]
    for name, fn in stand_ins:
        monkeypatch.setattr(EXT_External_VTT, name, classmethod(fn))

    async def campaign_backend(
        model_registry: Any,
        requester_id: str,
        campaign_id: Optional[str] = None,
        person_id: Optional[str] = None,
        world_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The wiki's answer (e715c6b): a campaign resolves for routing
        whoever asks; a world answers the requester's own campaign there."""
        if campaign_id is not None:
            if campaign_id != CAMPAIGN:
                return {"backend": "native", "world_id": None, "campaign_id": None}
            return {"backend": "foundry", "world_id": WORLD, "campaign_id": CAMPAIGN}
        if world_id == WORLD and requester_id == GM:
            return {"backend": "foundry", "world_id": WORLD, "campaign_id": CAMPAIGN}
        return {"backend": "native", "world_id": None, "campaign_id": None}

    monkeypatch.setattr(backend, "_registry", lambda: None)
    monkeypatch.setattr(
        backend,
        "loaded_ability",
        lambda registry, ext, name: (
            campaign_backend if name == "campaign_backend" else None
        ),
    )
    return calls


def test_an_id_names_its_world():
    value = fvtt_id("w1", "Scene.s1.Token.t1")
    assert value == "fvtt:w1:Scene.s1.Token.t1"
    assert parse_fvtt_id(value) == ("w1", "Scene.s1.Token.t1")
    assert is_fvtt_id(value) and not is_fvtt_id("3f2a-uuid")
    for bad in ("3f2a-uuid", "fvtt:", "fvtt:w1", "other:w1:Actor.a"):
        with pytest.raises(InvalidInputExternalError):
            parse_fvtt_id(bad)


async def test_the_world_is_the_campaigns_cast_and_places(world):
    entities = await EXT_External_VTT.foundry_world_entities(None, GM, CAMPAIGN)
    by_name = {e["name"]: e for e in entities}
    assert by_name["Kol"]["kind"] == "person"
    assert by_name["Kol"]["disposition"] == "friendly"
    assert by_name["Kol"]["location_id"] == "fvtt:w1:Scene.s1"
    assert by_name["The Ordo"]["kind"] == "organisation"  # its folder
    assert by_name["Vell"]["kind"] == "place"
    assert by_name["Campaign"]["kind"] == "lore"
    assert all(e["id"].startswith("fvtt:w1:") for e in entities)


async def test_by_id(world):
    [kol] = await EXT_External_VTT.foundry_world_entities_by_id(
        None, GM, ["fvtt:w1:Actor.a1"]
    )
    assert (kol["id"], kol["name"], kol["location_id"]) == (
        "fvtt:w1:Actor.a1",
        "Kol",
        "fvtt:w1:Scene.s1",
    )
    assert await EXT_External_VTT.foundry_world_entities_by_id(None, GM, []) == []


async def test_the_framing_is_the_campaign_journal(world):
    framing = await EXT_External_VTT.foundry_campaign_framing(None, GM, CAMPAIGN)
    assert framing == {
        "premise": "<p>A city drowns.</p>",
        "opening": "<p>Rain.</p>",
        "default_location_id": "fvtt:w1:Scene.s1",
    }


async def test_the_table_scene_and_who_is_on_it(world):
    scene = await EXT_External_VTT.foundry_current_scene(GM, CAMPAIGN)
    assert scene == {
        "id": "fvtt:w1:Scene.s1",
        "name": "Vell",
        "campaign_id": CAMPAIGN,
        "location_id": "fvtt:w1:Scene.s1",
    }
    [token] = await EXT_External_VTT.foundry_scene_tokens(GM, scene["id"])
    assert token["entity_id"] == "fvtt:w1:Actor.a1"
    assert token["id"] == "fvtt:w1:Scene.s1.Token.t1"
    assert (token["x"], token["y"], token["disposition"]) == (100, 50, "friendly")


async def test_the_fight_in_initiative_order(world):
    fight = await EXT_External_VTT.foundry_current_encounter(GM, CAMPAIGN)
    assert fight is not None
    assert (fight["id"], fight["status"], fight["round"]) == (
        "fvtt:w1:Combat.k1",
        "running",
        2,
    )
    assert [c["name"] for c in fight["order"]] == ["Ordo", "Kol"]
    assert fight["order"][1]["person_id"] == "fvtt:w1:Actor.a1"


async def test_a_sheet_is_the_actors_own(world):
    sheet = await EXT_External_VTT.foundry_character_sheet(GM, "fvtt:w1:Actor.a1")
    assert sheet["system"] == {"wounds": {"value": 9}}
    assert sheet["items"][0]["id"] == "fvtt:w1:Actor.a1.Item.i1"


@pytest.mark.parametrize(
    "call",
    [
        lambda: EXT_External_VTT.foundry_world_entities(None, "stranger", CAMPAIGN),
        lambda: EXT_External_VTT.foundry_current_scene("stranger", CAMPAIGN),
        lambda: EXT_External_VTT.foundry_scene_tokens("stranger", "fvtt:w1:Scene.s1"),
        lambda: EXT_External_VTT.foundry_character_sheet(
            "stranger", "fvtt:w1:Actor.a1"
        ),
        lambda: EXT_External_VTT.foundry_scene_tokens(GM, "fvtt:w9:Scene.s1"),
    ],
    ids=["entities", "scene", "tokens", "sheet", "another-world"],
)
async def test_someone_outside_the_campaign_reads_nothing(world, call):
    with pytest.raises(InvalidInputExternalError):
        await call()
    assert world == []


async def test_without_the_wikis_campaign_backend_nothing_is_read(world, monkeypatch):
    monkeypatch.setattr(backend, "loaded_ability", lambda registry, ext, name: None)
    with pytest.raises(InvalidInputExternalError, match="campaign_backend"):
        await EXT_External_VTT.foundry_current_scene(GM, CAMPAIGN)
    assert world == []


def test_each_read_implements_the_contracts_ability():
    from zephyrex.extensions.AbstractExtensionProvider import _marked

    implemented = {
        (info["extension"], info["ability"], info["backend"])
        for _, info in _marked(EXT_External_VTT, "_implements_info")
    }
    assert implemented == {
        ("rpg_core", "world_entities", "foundry"),
        ("rpg_core", "world_entities_by_id", "foundry"),
        ("rpg_core", "campaign_framing", "foundry"),
        ("rpg_scene", "current_scene", "foundry"),
        ("rpg_scene", "scene_tokens", "foundry"),
        ("rpg_combat", "current_encounter", "foundry"),
        ("rpg_dice", "character_sheet", "foundry"),
    }


async def test_a_member_answer_decides_when_the_wiki_gives_one(world, monkeypatch):
    async def answering_member(
        model_registry: Any,
        requester_id: str,
        campaign_id: Optional[str] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        return {
            "backend": "foundry",
            "world_id": WORLD,
            "campaign_id": CAMPAIGN,
            "member": requester_id == GM,
        }

    monkeypatch.setattr(
        backend, "loaded_ability", lambda registry, ext, name: answering_member
    )
    assert (await EXT_External_VTT.foundry_current_scene(GM, CAMPAIGN)) is not None
    with pytest.raises(InvalidInputExternalError):
        await EXT_External_VTT.foundry_current_scene("stranger", CAMPAIGN)
