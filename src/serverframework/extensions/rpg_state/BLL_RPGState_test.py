"""Structural tests for the rpg_state extension's BLL surface.

Cover model wiring (Manager↔_model linkage), the unified-trait
calculation contract, ItemInstance ownership semantics, and the
genealogy injection points (RPG_PersonModel, RPG_RelationshipModel).
"""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_state_test")

from serverframework.extensions.rpg_state.BLL_RPGState import (
    ALL_MODELS,
    CampaignManager,
    CampaignModel,
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
    PersonObjectiveManager,
    PersonObjectiveModel,
    PersonQuestManager,
    PersonQuestModel,
    PersonStatusEffectManager,
    PersonStatusEffectModel,
    PersonTraitManager,
    PersonTraitModel,
    QuestManager,
    QuestModel,
    RPG_PersonModel,
    RPG_RelationshipModel,
    StatusEffectManager,
    StatusEffectModel,
    StatusEffectTraitManager,
    StatusEffectTraitModel,
    TraitManager,
    TraitModel,
)


class TestModelManagerWiring:
    """Every owned model must point to its Manager and vice versa."""

    PAIRS = [
        (GameSystemModel, GameSystemManager),
        (CampaignModel, CampaignManager),
        (TraitModel, TraitManager),
        (StatusEffectModel, StatusEffectManager),
        (StatusEffectTraitModel, StatusEffectTraitManager),
        (PersonTraitModel, PersonTraitManager),
        (PersonStatusEffectModel, PersonStatusEffectManager),
        (FactionModel, FactionManager),
        (LocationModel, LocationManager),
        (ItemModel, ItemManager),
        (ItemInstanceModel, ItemInstanceManager),
        (QuestModel, QuestManager),
        (ObjectiveModel, ObjectiveManager),
        (PersonQuestModel, PersonQuestManager),
        (FactionQuestModel, FactionQuestManager),
        (PersonObjectiveModel, PersonObjectiveManager),
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


class TestPersonInjectionContract:
    """RPG_PersonModel injects character-specific fields onto
    genealogy.PersonModel. No CharacterModel exists in rpg_state."""

    def test_rpg_person_carries_character_fields(self):
        rp = RPG_PersonModel(
            kind="pc",
            campaign_id="c1",
            user_id="user-1",
        )
        assert rp.kind == "pc"
        assert rp.campaign_id == "c1"
        assert rp.user_id == "user-1"

    def test_npc_leaves_user_id_null(self):
        rp = RPG_PersonModel(kind="npc", campaign_id="c1")
        assert rp.user_id is None

    def test_no_character_model_in_rpg_state(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "CharacterModel")
        assert not hasattr(BLL_RPGState, "CharacterTraitModel")
        assert not hasattr(BLL_RPGState, "CharacterFactionModel")


class TestRelationshipFactionInjection:
    """RPG_RelationshipModel adds faction_id and target_faction_id
    so the relationships table holds Person↔Faction and Faction↔Faction
    edges. The endpoint-XOR CHECK is enforced by the migration."""

    def test_subject_faction_endpoint(self):
        rr = RPG_RelationshipModel(faction_id="f-guild")
        assert rr.faction_id == "f-guild"
        assert rr.target_faction_id is None

    def test_target_faction_endpoint(self):
        rr = RPG_RelationshipModel(target_faction_id="f-thieves")
        assert rr.target_faction_id == "f-thieves"

    def test_both_faction_endpoints(self):
        rr = RPG_RelationshipModel(
            faction_id="f-guild",
            target_faction_id="f-thieves",
        )
        assert rr.faction_id == "f-guild"
        assert rr.target_faction_id == "f-thieves"


class TestUnifiedTraitContract:
    """STR=16 lives on PersonTrait.value. +2 from Bull's Strength lives
    on StatusEffectTrait(operation='additive', value=2.0). Damage uses
    negative values (HealthDamage = StatusEffectTrait(value=-8) against
    a HealthMax resource Trait)."""

    def test_person_trait_carries_value(self):
        pt = PersonTraitModel.Create(value=16.0)
        assert pt.value == 16.0

    def test_status_effect_trait_carries_modifier(self):
        set_row = StatusEffectTraitModel.Create(
            operation="additive",
            value=2.0,
        )
        assert set_row.operation == "additive"
        assert set_row.value == 2.0

    def test_status_effect_trait_supports_damage(self):
        # HealthDamage convention: negative value against resource Trait.
        damage = StatusEffectTraitModel.Create(
            operation="additive",
            value=-8.0,
        )
        assert damage.value == -8.0

    def test_qualifier_field_present(self):
        # qualifier is conditional context: NULL = always; non-NULL = toggleable.
        cond = StatusEffectTraitModel.Create(
            operation="additive",
            value=2.0,
            qualifier="vs orcs",
        )
        assert cond.qualifier == "vs orcs"
        unconditional = StatusEffectTraitModel.Create(
            operation="additive", value=1.0
        )
        assert unconditional.qualifier is None

    def test_person_status_effect_links_subject_to_template(self):
        pse = PersonStatusEffectModel.Create(
            person_id="char-1",
            status_effect_id="effect-1",
            source_person_id="char-2",  # caster
        )
        assert pse.person_id == "char-1"
        assert pse.status_effect_id == "effect-1"
        assert pse.source_person_id == "char-2"

    def test_person_status_effect_fully_editable(self):
        # GMs can retcon any aspect of an applied effect.
        upd = PersonStatusEffectModel.Update(
            source_person_id="caster-X",
            source_item_instance_id="ii-Y",
        )
        assert upd.source_person_id == "caster-X"
        assert upd.source_item_instance_id == "ii-Y"


class TestItemOwnershipSemantics:
    """De jure (faction) and assignment (person) ownership are
    independent; both nullable, no XOR. Ownership ≠ equipped."""

    def test_neither_owner_set(self):
        ii = ItemInstanceModel.Create(quantity=1)
        assert ii.owner_person_id is None
        assert ii.owner_faction_id is None

    def test_both_owners_set(self):
        # Legal: Guild owns it (faction), Bob is responsible (person).
        ii = ItemInstanceModel.Create(
            owner_person_id="bob",
            owner_faction_id="guild",
            quantity=1,
        )
        assert ii.owner_person_id == "bob"
        assert ii.owner_faction_id == "guild"

    def test_person_only_set(self):
        ii = ItemInstanceModel.Create(owner_person_id="bob", quantity=1)
        assert ii.owner_faction_id is None

    def test_faction_only_set(self):
        ii = ItemInstanceModel.Create(owner_faction_id="guild", quantity=1)
        assert ii.owner_person_id is None


class TestSpatialContainers:
    """Locations are recursive and can represent the inside of a
    container item or a person's equipment slot."""

    def test_nested_locations(self):
        outer = LocationModel.Create(name="Tavern")
        inner = LocationModel.Create(name="Tavern → Cellar", parent_id="outer-id")
        assert inner.parent_id == "outer-id"
        assert outer.parent_id is None

    def test_container_item_instance(self):
        loc = LocationModel.Create(
            name="Inside the Satchel",
            container_item_instance_id="ii-satchel",
            kind="container",
        )
        assert loc.container_item_instance_id == "ii-satchel"

    def test_equipment_slot_bound_to_person(self):
        loc = LocationModel.Create(
            name="Bob's Left Hand",
            associated_person_id="bob",
            kind="equipment_slot",
        )
        assert loc.associated_person_id == "bob"


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
        pq = PersonQuestModel.Create(
            person_id="c1", quest_id="q1", status="active"
        )
        fq = FactionQuestModel.Create(
            faction_id="f1", quest_id="q1", status="active"
        )
        assert pq.status == "active"
        assert fq.status == "active"


class TestFactionHierarchy:
    def test_subfactions_via_parent_id(self):
        party = FactionModel.Create(name="Tavern Crew", parent_id="guild-id")
        assert party.parent_id == "guild-id"

    def test_no_character_factions_table(self):
        # Membership is in relationships(kind='member_of'), not its own table.
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "CharacterFactionModel")
        assert not hasattr(BLL_RPGState, "PersonFactionModel")
