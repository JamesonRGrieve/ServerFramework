"""Structural tests for the rpg_state extension's BLL surface.

These cover model wiring (Manager↔_model linkage), the unified-trait
calculation contract, and the ItemInstance ownership semantics. They do
not exercise the database; integration coverage will be added once the
extension is wired into the test app fixture.
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_state_test")

from serverframework.extensions.rpg_state.BLL_RPGState import (
    ALL_MODELS,
    CampaignManager,
    CampaignModel,
    CharacterFactionManager,
    CharacterFactionModel,
    CharacterManager,
    CharacterModel,
    CharacterObjectiveManager,
    CharacterObjectiveModel,
    CharacterQuestManager,
    CharacterQuestModel,
    CharacterStatusEffectManager,
    CharacterStatusEffectModel,
    CharacterTraitManager,
    CharacterTraitModel,
    FactionManager,
    FactionModel,
    FactionQuestManager,
    FactionQuestModel,
    GameSystemManager,
    GameSystemModel,
    ItemInstanceManager,
    ItemInstanceModel,
    ItemManager,
    ItemModel,
    LocationManager,
    LocationModel,
    ObjectiveManager,
    ObjectiveModel,
    QuestManager,
    QuestModel,
    StatusEffectManager,
    StatusEffectModel,
    StatusEffectTraitManager,
    StatusEffectTraitModel,
    TraitManager,
    TraitModel,
)


class TestModelManagerWiring:
    """Every model must point to its Manager and vice versa."""

    PAIRS = [
        (GameSystemModel, GameSystemManager),
        (CampaignModel, CampaignManager),
        (TraitModel, TraitManager),
        (StatusEffectModel, StatusEffectManager),
        (StatusEffectTraitModel, StatusEffectTraitManager),
        (CharacterModel, CharacterManager),
        (CharacterTraitModel, CharacterTraitManager),
        (CharacterStatusEffectModel, CharacterStatusEffectManager),
        (FactionModel, FactionManager),
        (CharacterFactionModel, CharacterFactionManager),
        (LocationModel, LocationManager),
        (ItemModel, ItemManager),
        (ItemInstanceModel, ItemInstanceManager),
        (QuestModel, QuestManager),
        (ObjectiveModel, ObjectiveManager),
        (CharacterQuestModel, CharacterQuestManager),
        (FactionQuestModel, FactionQuestManager),
        (CharacterObjectiveModel, CharacterObjectiveManager),
    ]

    def test_each_model_has_its_manager(self):
        for model, manager in self.PAIRS:
            assert model.Manager is manager, f"{model.__name__} → wrong Manager"
            assert manager._model is model, f"{manager.__name__} → wrong _model"

    def test_all_models_listed(self):
        listed = set(ALL_MODELS)
        expected = {model for model, _ in self.PAIRS}
        assert listed == expected


class TestCreateUpdateSearchPresent:
    """Every model exposes Create / Update / Search nested classes."""

    def test_nested_classes_exist(self):
        for model in ALL_MODELS:
            assert hasattr(model, "Create"), f"{model.__name__} missing Create"
            assert hasattr(model, "Update"), f"{model.__name__} missing Update"
            assert hasattr(model, "Search"), f"{model.__name__} missing Search"


class TestUnifiedTraitContract:
    """STR=16 lives on CharacterTrait.value. +2 from Bull's Strength lives
    on StatusEffectTrait(operation='additive', value=2.0)."""

    def test_character_trait_carries_value(self):
        # Pydantic accepts the field; type is float-compatible.
        ct = CharacterTraitModel.Create(value=16.0)
        assert ct.value == 16.0

    def test_status_effect_trait_carries_modifier(self):
        set_row = StatusEffectTraitModel.Create(
            operation="additive",
            value=2.0,
        )
        assert set_row.operation == "additive"
        assert set_row.value == 2.0

    def test_character_status_effect_links_subject_to_template(self):
        # The character's instance row references both the affected
        # character and the StatusEffect template; modifiers come from
        # StatusEffectTrait, not duplicated here.
        cse = CharacterStatusEffectModel.Create(
            character_id="char-1",
            status_effect_id="effect-1",
            source_character_id="char-2",  # caster
        )
        assert cse.character_id == "char-1"
        assert cse.status_effect_id == "effect-1"
        assert cse.source_character_id == "char-2"


class TestItemOwnershipSemantics:
    """De jure (faction) and de facto (character) ownership are
    independent; both nullable, no XOR."""

    def test_neither_owner_set(self):
        # Wild loot: lying on a dungeon floor, no claim.
        ii = ItemInstanceModel.Create(quantity=1)
        assert ii.owner_character_id is None
        assert ii.owner_faction_id is None

    def test_both_owners_set(self):
        # Legal: Guild owns it (faction), Bob carries it (character).
        ii = ItemInstanceModel.Create(
            owner_character_id="bob",
            owner_faction_id="guild",
            quantity=1,
        )
        assert ii.owner_character_id == "bob"
        assert ii.owner_faction_id == "guild"

    def test_character_only_set(self):
        ii = ItemInstanceModel.Create(owner_character_id="bob", quantity=1)
        assert ii.owner_faction_id is None

    def test_faction_only_set(self):
        ii = ItemInstanceModel.Create(owner_faction_id="guild", quantity=1)
        assert ii.owner_character_id is None


class TestCharacterPlayerLink:
    """user_id on Character is the controlling player; null for NPCs."""

    def test_pc_has_user_id(self):
        c = CharacterModel.Create(name="Pip", kind="pc", user_id="user-1")
        assert c.user_id == "user-1"

    def test_npc_leaves_user_id_null(self):
        c = CharacterModel.Create(name="Innkeeper", kind="npc")
        assert c.user_id is None


class TestSpatialContainers:
    """Locations are recursive and can represent the inside of a
    container item or a character's equipment slot."""

    def test_nested_locations(self):
        outer = LocationModel.Create(name="Tavern")
        inner = LocationModel.Create(name="Tavern → Cellar", parent_id="outer-id")
        assert inner.parent_id == "outer-id"
        assert outer.parent_id is None

    def test_container_item_instance(self):
        # The interior of a satchel.
        loc = LocationModel.Create(
            name="Inside the Satchel",
            container_item_instance_id="ii-satchel",
            kind="container",
        )
        assert loc.container_item_instance_id == "ii-satchel"

    def test_equipment_slot_bound_to_character(self):
        loc = LocationModel.Create(
            name="Bob's Left Hand",
            associated_character_id="bob",
            kind="equipment_slot",
        )
        assert loc.associated_character_id == "bob"


class TestQuestObjectiveChaining:
    def test_objective_prerequisite(self):
        obj = ObjectiveModel.Create(
            name="Slay the dragon",
            quest_id="q1",
            prerequisite_objective_id="o-find-dragon",
            order_index=2,
        )
        assert obj.prerequisite_objective_id == "o-find-dragon"
        assert obj.order_index == 2

    def test_quest_dual_assignment(self):
        cq = CharacterQuestModel.Create(
            character_id="c1", quest_id="q1", status="active"
        )
        fq = FactionQuestModel.Create(
            faction_id="f1", quest_id="q1", status="active"
        )
        assert cq.status == "active"
        assert fq.status == "active"


class TestFactionHierarchy:
    def test_subfactions_via_parent_id(self):
        # A party = sub-faction of a guild.
        party = FactionModel.Create(name="Tavern Crew", parent_id="guild-id")
        assert party.parent_id == "guild-id"
