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
            "type": "character",
            "prototypeToken.disposition": 1,
        },
        {"id": "a3", "uuid": "Actor.a3", "name": "Dockhand", "type": "npc"},
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
    assert (by_name["Kol"]["person_kind"], by_name["Dockhand"]["person_kind"]) == (
        "pc",
        "npc",
    )
    assert by_name["The Ordo"]["person_kind"] is None
    assert by_name["Vell"]["person_kind"] is None
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


def test_each_implements_the_contracts_ability():
    from zephyrex.extensions.AbstractExtensionProvider import _marked

    implemented = {
        (info["extension"], info["ability"])
        for _, info in _marked(EXT_External_VTT, "_implements_info")
        if info["backend"] == "foundry"
    }
    assert implemented == {
        ("rpg_core", "world_entities"),
        ("rpg_core", "world_entities_by_id"),
        ("rpg_core", "world_entities_matching"),
        ("rpg_core", "campaign_framing"),
        ("rpg_core", "campaign_documents"),
        ("rpg_core", "ensure_person"),
        ("rpg_core", "update_person"),
        ("rpg_core", "move_person"),
        ("rpg_core", "session_report"),
        ("rpg_core", "advance_clock"),
        ("rpg_scene", "current_scene"),
        ("rpg_scene", "scene_tokens"),
        ("rpg_scene", "place_person"),
        ("rpg_scene", "remove_person_from_scene"),
        ("rpg_scene", "set_scene_background"),
        ("rpg_combat", "current_encounter"),
        ("rpg_combat", "start_encounter"),
        ("rpg_combat", "start_combat"),
        ("rpg_combat", "next_turn"),
        ("rpg_combat", "end_encounter"),
        ("rpg_combat", "set_disposition"),
        ("rpg_dice", "character_sheet"),
    }


# -- writes ---------------------------------------------------------------------


@pytest.fixture
def writes(world, monkeypatch) -> List[tuple]:
    """The stand-in world takes writes too, recording each."""
    done: List[tuple] = []
    clock = {"core.time": 3600}

    async def vtt_create_documents(
        cls,
        world_id,
        document_type,
        documents,
        parent_uuid=None,
        as_user=None,
        approved_by=None,
    ):
        done.append(("create", document_type, documents, parent_uuid))
        prefix = {
            "Actor": "a-new",
            "Combat": "k-new",
            "JournalEntry": "j-new",
            "Token": "t-new",
        }
        return [prefix.get(document_type, "x-new")]

    async def vtt_update_documents(
        cls,
        world_id,
        document_type,
        updates,
        parent_uuid=None,
        as_user=None,
        approved_by=None,
    ):
        done.append(("update", document_type, updates, parent_uuid))
        return [u["_id"] for u in updates]

    async def vtt_delete_documents(
        cls,
        world_id,
        document_type,
        ids,
        parent_uuid=None,
        as_user=None,
        approved_by=None,
    ):
        done.append(("delete", document_type, ids, parent_uuid))
        return {"deleted": ids}

    async def vtt_status(cls, world_id, as_user=None):
        return {"npc_actor_type": "npc"}

    async def vtt_get_setting(cls, world_id, key, as_user=None):
        return clock[key]

    async def vtt_set_setting(
        cls, world_id, key, value, as_user=None, approved_by=None
    ):
        done.append(("setting", key, value, None))
        clock[key] = value

    stand_ins: List[Tuple[str, Any]] = [
        ("vtt_create_documents", vtt_create_documents),
        ("vtt_update_documents", vtt_update_documents),
        ("vtt_delete_documents", vtt_delete_documents),
        ("vtt_status", vtt_status),
        ("vtt_get_setting", vtt_get_setting),
        ("vtt_set_setting", vtt_set_setting),
    ]
    for name, fn in stand_ins:
        monkeypatch.setattr(EXT_External_VTT, name, classmethod(fn))
    FULL["Actor.a2"] = {
        "_id": "a2",
        "name": "The Ordo",
        "prototypeToken": {"disposition": -1},
    }
    FULL["Scene.s2"] = {"_id": "s2", "name": "Docks", "width": 2000, "height": 1000}
    FULL["Scene.s1"] = {"_id": "s1", "name": "Vell", "width": 4000, "height": 3000}
    FULL["Combat.k1"] = {"_id": "k1", "round": 2, "turn": 1, "scene": "s1"}
    FULL["Combat.k-new"] = {"_id": "k-new", "round": 0, "turn": None, "scene": "s1"}
    EMBEDDED[("Combat.k-new", "Combatant")] = []
    return done


async def test_ensure_person_finds_or_makes_the_actor(writes):
    kol = await EXT_External_VTT.foundry_ensure_person(None, GM, CAMPAIGN, "Kol")
    assert kol["id"] == "fvtt:w1:Actor.a1" and writes == []
    made = await EXT_External_VTT.foundry_ensure_person(None, GM, CAMPAIGN, "Sefa")
    assert made["id"] == "fvtt:w1:Actor.a-new"
    assert writes == [("create", "Actor", [{"name": "Sefa", "type": "npc"}], None)]


async def test_update_person_changes_what_an_actor_keeps(writes):
    await EXT_External_VTT.foundry_update_person(
        None, GM, "fvtt:w1:Actor.a1", {"name": "Kol the Grey", "summary": "ignored"}
    )
    assert writes == [
        ("update", "Actor", [{"_id": "a1", "name": "Kol the Grey"}], None)
    ]


async def test_placing_and_removing_a_person(writes):
    placed = await EXT_External_VTT.foundry_place_person(
        GM, "fvtt:w1:Scene.s1", "fvtt:w1:Actor.a2", near_person_id="fvtt:w1:Actor.a1"
    )
    assert placed["id"] == "fvtt:w1:Scene.s1.Token.t-new"
    [(_, kind, [token], parent)] = writes
    assert (kind, parent) == ("Token", "Scene.s1")
    assert (token["actorId"], token["x"], token["y"], token["disposition"]) == (
        "a2",
        200,
        50,
        -1,
    )
    already = await EXT_External_VTT.foundry_place_person(
        GM, "fvtt:w1:Scene.s1", "fvtt:w1:Actor.a1"
    )
    assert already["id"] == "fvtt:w1:Scene.s1.Token.t1" and len(writes) == 1
    gone = await EXT_External_VTT.foundry_remove_person_from_scene(
        GM, "fvtt:w1:Scene.s1", "fvtt:w1:Actor.a1"
    )
    assert gone == {"removed": True}
    assert writes[-1] == ("delete", "Token", ["t1"], "Scene.s1")


async def test_moving_a_person_takes_their_token_with_them(writes):
    assert await EXT_External_VTT.foundry_move_person(
        None, GM, "fvtt:w1:Actor.a1", "fvtt:w1:Scene.s2"
    )
    assert writes[0] == ("delete", "Token", ["t1"], "Scene.s1")
    _, kind, [token], parent = writes[1]
    assert (kind, parent, token["actorId"], token["x"], token["y"]) == (
        "Token",
        "Scene.s2",
        "a1",
        1000,
        500,
    )


async def test_a_fight_from_start_to_end(writes):
    order = await EXT_External_VTT.foundry_start_encounter(
        GM,
        CAMPAIGN,
        "fvtt:w1:Scene.s1",
        ["fvtt:w1:Actor.a1"],
        {"fvtt:w1:Actor.a1": "hostile"},
    )
    assert order["id"] == "fvtt:w1:Combat.k-new" and order["status"] == "setup"
    assert writes[0] == ("create", "Combat", [{"scene": "s1", "active": True}], None)
    assert writes[1] == (
        "create",
        "Combatant",
        [{"actorId": "a1", "tokenId": "t1", "sceneId": "s1"}],
        "Combat.k-new",
    )
    assert writes[2] == (
        "update",
        "Token",
        [{"_id": "t1", "disposition": -1}],
        "Scene.s1",
    )
    started = await EXT_External_VTT.foundry_start_combat(GM, "fvtt:w1:Combat.k1")
    assert started["status"] == "running" and started["round"] == 1
    assert started["current_combatant_id"] == "fvtt:w1:Combat.k1.Combatant.m2"
    ended = await EXT_External_VTT.foundry_end_encounter(GM, "fvtt:w1:Combat.k1")
    assert ended["status"] == "ended"
    assert writes[-1] == ("delete", "Combat", ["k1"], None)


async def test_the_turn_after_the_last_is_the_next_round(writes):
    order = await EXT_External_VTT.foundry_next_turn(GM, "fvtt:w1:Combat.k1")
    assert (order["round"], order["turn"]) == (3, 0)
    assert writes == [
        ("update", "Combat", [{"_id": "k1", "round": 3, "turn": 0}], None)
    ]


async def test_a_disposition_is_the_persons_token_on_the_fights_scene(writes):
    await EXT_External_VTT.foundry_set_disposition(
        GM, "fvtt:w1:Combat.k1", "fvtt:w1:Actor.a1", "friendly"
    )
    assert writes == [
        ("update", "Token", [{"_id": "t1", "disposition": 1}], "Scene.s1")
    ]
    with pytest.raises(InvalidInputExternalError):
        await EXT_External_VTT.foundry_set_disposition(
            GM, "fvtt:w1:Combat.k1", "fvtt:w1:Actor.a1", "furious"
        )


async def test_a_session_report_is_a_journal(writes):
    report = await EXT_External_VTT.foundry_session_report(
        None, GM, CAMPAIGN, "Session 3", "<p>They drowned.</p>"
    )
    assert report == {"id": "fvtt:w1:JournalEntry.j-new"}
    assert writes[0] == ("create", "JournalEntry", [{"name": "Session 3"}], None)
    assert writes[1][1:] == (
        "JournalEntryPage",
        [
            {
                "name": "Session 3",
                "type": "text",
                "text": {"content": "<p>They drowned.</p>"},
            }
        ],
        "JournalEntry.j-new",
    )


async def test_the_clock_moves_forward(writes):
    assert await EXT_External_VTT.foundry_advance_clock(None, GM, CAMPAIGN, 90) == {
        "day": 1,
        "time": "02:30",
        "seconds": 9000,
    }
    with pytest.raises(InvalidInputExternalError):
        await EXT_External_VTT.foundry_advance_clock(None, GM, CAMPAIGN, -5)


async def test_matching_names(writes):
    found = await EXT_External_VTT.foundry_world_entities_matching(
        None, GM, CAMPAIGN, "ve", 5
    )
    assert [e["name"] for e in found] == ["Vell"]


async def test_a_stranger_writes_nothing(writes):
    with pytest.raises(InvalidInputExternalError):
        await EXT_External_VTT.foundry_ensure_person(None, "stranger", CAMPAIGN, "Sefa")
    with pytest.raises(InvalidInputExternalError):
        await EXT_External_VTT.foundry_next_turn("stranger", "fvtt:w1:Combat.k1")
    assert writes == []
