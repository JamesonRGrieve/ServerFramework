"""Structural tests for the rpg_state extension's BLL surface.

Covers the unified Trait model (StatusEffect folded into Trait;
DerivativeTrait edges replace StatusEffectTrait), the Hook DAG
(Quest+Objective folded into Hook; HookDependency carries the edges),
ItemInstance ownership semantics, and the genealogy injection points
(RPG_PersonModel, RPG_RelationshipModel).
"""

import os

import pytest

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_state_test")

from serverframework.extensions.rpg_state.BLL_RPGState import (
    ALL_MODELS,
    CampaignManager,
    CampaignModel,
    DerivativeTraitManager,
    DerivativeTraitModel,
    FactionHookManager,
    FactionHookModel,
    FactionManager,
    FactionModel,
    GameSystemManager,
    GameSystemModel,
    HookDependencyManager,
    HookDependencyModel,
    HookManager,
    HookModel,
    ItemInstanceManager,
    ItemInstanceModel,
    ItemManager,
    ItemModel,
    LocationManager,
    LocationModel,
    PersonHookManager,
    PersonHookModel,
    PersonTraitManager,
    PersonTraitModel,
    RPG_PersonModel,
    RPG_RelationshipModel,
    TraitManager,
    TraitModel,
)


class TestModelManagerWiring:
    """Every owned model must point to its Manager and vice versa."""

    PAIRS = [
        (GameSystemModel, GameSystemManager),
        (CampaignModel, CampaignManager),
        (TraitModel, TraitManager),
        (DerivativeTraitModel, DerivativeTraitManager),
        (PersonTraitModel, PersonTraitManager),
        (FactionModel, FactionManager),
        (LocationModel, LocationManager),
        (ItemModel, ItemManager),
        (ItemInstanceModel, ItemInstanceManager),
        (HookModel, HookManager),
        (HookDependencyModel, HookDependencyManager),
        (PersonHookModel, PersonHookManager),
        (FactionHookModel, FactionHookManager),
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
    def test_nested_classes_exist(self):
        for model in ALL_MODELS:
            assert hasattr(model, "Create"), f"{model.__name__} missing Create"
            assert hasattr(model, "Update"), f"{model.__name__} missing Update"
            assert hasattr(model, "Search"), f"{model.__name__} missing Search"


class TestPersonInjectionContract:
    def test_rpg_person_carries_character_fields(self):
        rp = RPG_PersonModel(
            kind="pc",
            campaign_id="c1",
            user_id="user-1",
            location_id="loc-tavern",
        )
        assert rp.kind == "pc"
        assert rp.campaign_id == "c1"
        assert rp.user_id == "user-1"
        assert rp.location_id == "loc-tavern"

    def test_npc_leaves_user_id_null(self):
        rp = RPG_PersonModel(kind="npc", campaign_id="c1")
        assert rp.user_id is None

    def test_person_location_separate_from_body_part_locations(self):
        # Person.location_id is the scene/region location;
        # body parts are sub-Locations whose
        # associated_person_id = person.id (NOT person.location_id).
        rp = RPG_PersonModel(location_id="loc-clearing")
        assert rp.location_id == "loc-clearing"

    def test_no_character_model_in_rpg_state(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "CharacterModel")
        assert not hasattr(BLL_RPGState, "CharacterTraitModel")
        assert not hasattr(BLL_RPGState, "CharacterFactionModel")


class TestRelationshipFactionInjection:
    def test_subject_faction_endpoint(self):
        rr = RPG_RelationshipModel(faction_id="f-guild")
        assert rr.faction_id == "f-guild"
        assert rr.target_faction_id is None

    def test_target_faction_endpoint(self):
        rr = RPG_RelationshipModel(target_faction_id="f-thieves")
        assert rr.target_faction_id == "f-thieves"

    def test_both_faction_endpoints(self):
        rr = RPG_RelationshipModel(
            faction_id="f-guild", target_faction_id="f-thieves"
        )
        assert rr.faction_id == "f-guild"
        assert rr.target_faction_id == "f-thieves"


class TestUnifiedTraitContract:
    """StatusEffect is folded into Trait via kind='status_effect'.
    No separate StatusEffectModel exists."""

    def test_no_status_effect_model(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "StatusEffectModel")
        assert not hasattr(BLL_RPGState, "StatusEffectTraitModel")
        assert not hasattr(BLL_RPGState, "PersonStatusEffectModel")

    def test_status_effect_is_a_trait_kind(self):
        bs = TraitModel.Create(
            name="Bull's Strength",
            kind="status_effect",
            default_duration_seconds=600,
        )
        assert bs.kind == "status_effect"
        assert bs.default_duration_seconds == 600

    def test_attribute_trait_has_no_duration(self):
        # default_duration_seconds is meaningful only for status_effect.
        str_attr = TraitModel.Create(name="STR", kind="attribute")
        assert str_attr.default_duration_seconds is None


class TestDerivativeTraitContract:
    """DerivativeTrait carries operation, value, order_index,
    stacking_group, qualifier. source_trait_id NULL = constant.
    operation is locked to a closed enum at the Pydantic layer."""

    def test_static_buff_via_derivative(self):
        # Bull's Strength → STR, +2 always.
        d = DerivativeTraitModel.Create(
            source_trait_id="bulls-strength",
            target_trait_id="str",
            operation="additive",
            value=2.0,
        )
        assert d.source_trait_id == "bulls-strength"
        assert d.target_trait_id == "str"
        assert d.operation == "additive"
        assert d.value == 2.0

    def test_operation_rejects_unknown_value(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            DerivativeTraitModel.Create(
                source_trait_id="x",
                target_trait_id="y",
                operation="not-a-real-op",
                value=1.0,
            )

    def test_constant_contribution_with_null_source(self):
        # Initiative = DEX + 5 — the +5 is a constant edge.
        d = DerivativeTraitModel.Create(
            source_trait_id=None,
            target_trait_id="initiative",
            operation="additive",
            value=5.0,
        )
        assert d.source_trait_id is None
        assert d.value == 5.0

    def test_signed_value_for_damage(self):
        # Wound → HealthMax, value=-1 multiplier; PersonTrait.value
        # carries the damage magnitude. Or use static -8 here.
        d = DerivativeTraitModel.Create(
            source_trait_id="wound",
            target_trait_id="health-max",
            operation="additive",
            value=-1.0,
        )
        assert d.value == -1.0

    def test_phase_via_operation_and_order_index(self):
        d = DerivativeTraitModel.Create(
            source_trait_id="haste",
            target_trait_id="speed",
            operation="multiplicative",
            value=2.0,
            order_index=1,
        )
        assert d.operation == "multiplicative"
        assert d.order_index == 1

    def test_stacking_group(self):
        d = DerivativeTraitModel.Create(
            source_trait_id="ring-of-protection",
            target_trait_id="ac",
            operation="additive",
            value=1.0,
            stacking_group="deflection_bonus",
        )
        assert d.stacking_group == "deflection_bonus"

    def test_qualifier_for_conditional(self):
        d = DerivativeTraitModel.Create(
            source_trait_id="orcbane",
            target_trait_id="attack-bonus",
            operation="additive",
            value=2.0,
            qualifier="vs orcs",
        )
        assert d.qualifier == "vs orcs"

    def test_clamp_operation_supported(self):
        d = DerivativeTraitModel.Create(
            source_trait_id=None,
            target_trait_id="hp-current",
            operation="clamp",
            value=0.0,  # 0 = lower bound by convention
        )
        assert d.operation == "clamp"


class TestPersonTraitFoldsStatusEffect:
    """PersonTrait carries both durable trait values and applied-effect
    attachments. Status effects are PersonTrait rows with started_at /
    expires_at / source_* set; durable traits leave them NULL."""

    def test_durable_attribute(self):
        pt = PersonTraitModel.Create(
            person_id="bob", trait_id="str", value=16.0
        )
        assert pt.value == 16.0
        assert pt.started_at is None
        assert pt.expires_at is None

    def test_applied_status_effect(self):
        from datetime import datetime

        pt = PersonTraitModel.Create(
            person_id="bob",
            trait_id="bulls-strength",
            started_at=datetime(2026, 1, 1, 12, 0),
            expires_at=datetime(2026, 1, 1, 12, 10),
            source_person_id="wizard",
            source_item_instance_id="ii-potion",
        )
        assert pt.source_person_id == "wizard"
        assert pt.source_item_instance_id == "ii-potion"

    def test_wound_carries_damage_magnitude_on_value(self):
        pt = PersonTraitModel.Create(
            person_id="bob",
            trait_id="wound",
            value=-8.0,
            source_person_id="orc",
        )
        assert pt.value == -8.0
        assert pt.source_person_id == "orc"

    def test_target_location_localises_damage_to_body_part(self):
        # Bob takes 8 damage to his left arm (a body-part Location
        # under his body-root Location, associated_person_id=Bob).
        pt = PersonTraitModel.Create(
            person_id="bob",
            trait_id="wound",
            value=-8.0,
            target_location_id="loc-bob-left-arm",
            source_person_id="orc",
        )
        assert pt.target_location_id == "loc-bob-left-arm"

    def test_target_location_optional_for_full_body_buffs(self):
        # Bull's Strength is whole-body — target_location_id stays NULL.
        pt = PersonTraitModel.Create(
            person_id="bob",
            trait_id="bulls-strength",
        )
        assert pt.target_location_id is None

    def test_person_trait_fully_editable(self):
        from datetime import datetime

        u = PersonTraitModel.Update(
            value=20.0,
            expires_at=datetime(2026, 1, 1, 13, 0),
            source_person_id="caster-X",
            source_item_instance_id="ii-Y",
        )
        assert u.value == 20.0
        assert u.source_person_id == "caster-X"


class TestItemOwnershipSemantics:
    def test_neither_owner_set(self):
        ii = ItemInstanceModel.Create(quantity=1)
        assert ii.owner_person_id is None
        assert ii.owner_faction_id is None

    def test_both_owners_set(self):
        ii = ItemInstanceModel.Create(
            owner_person_id="bob", owner_faction_id="guild", quantity=1
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
    def test_nested_locations(self):
        outer = LocationModel.Create(name="Tavern")
        inner = LocationModel.Create(
            name="Tavern → Cellar", parent_id="outer-id"
        )
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


class TestHookDAG:
    """Quest+Objective fold into Hook with a kind discriminator. The
    DAG of dependencies lives in HookDependency; presence of a row IS
    the dependency, the reason string explains it."""

    def test_no_quest_or_objective_models(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "QuestModel")
        assert not hasattr(BLL_RPGState, "ObjectiveModel")
        assert not hasattr(BLL_RPGState, "PersonQuestModel")
        assert not hasattr(BLL_RPGState, "PersonObjectiveModel")
        assert not hasattr(BLL_RPGState, "FactionQuestModel")

    def test_hook_is_kind_discriminated(self):
        major = HookModel.Create(
            name="Slay the dragon",
            kind="major_quest",
            campaign_id="c1",
            giver_faction_id="f-king",
        )
        thread = HookModel.Create(
            name="The cursed amulet rumor",
            kind="rumor",
            campaign_id="c1",
        )
        assert major.kind == "major_quest"
        assert thread.kind == "rumor"

    def test_hook_kind_rejects_unknown_value(self):
        # kind is locked to the closed HookKind Literal.
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            HookModel.Create(name="X", kind="invented_kind")

    def test_dependency_is_presence_plus_reason(self):
        d = HookDependencyModel.Create(
            parent_hook_id="hook-find-dragon",
            child_hook_id="hook-slay-dragon",
            reason="must locate the dragon's lair before the assault",
        )
        assert d.parent_hook_id == "hook-find-dragon"
        assert d.child_hook_id == "hook-slay-dragon"
        assert "lair" in d.reason

    def test_no_dependency_kind_enum(self):
        # The HookDependency model has no `kind` or `dependency_kind`
        # field — presence of the row IS the dependency, period.
        for cls in (
            HookDependencyModel.Create,
            HookDependencyModel.Update,
        ):
            assert "kind" not in cls.model_fields
            assert "dependency_kind" not in cls.model_fields

    def test_person_hook_progress(self):
        ph = PersonHookModel.Create(
            person_id="bob",
            hook_id="hook-slay-wolves",
            status="active",
            progress=3.0,  # 3 of 5 wolves slain
        )
        assert ph.status == "active"
        assert ph.progress == 3.0

    def test_faction_hook_progress(self):
        fh = FactionHookModel.Create(
            faction_id="f-guild",
            hook_id="hook-recover-relic",
            status="active",
            progress=0.5,
        )
        assert fh.status == "active"
        assert fh.progress == 0.5


class TestFactionHierarchy:
    def test_subfactions_via_parent_id(self):
        party = FactionModel.Create(name="Tavern Crew", parent_id="guild-id")
        assert party.parent_id == "guild-id"

    def test_no_character_factions_table(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        assert not hasattr(BLL_RPGState, "CharacterFactionModel")
        assert not hasattr(BLL_RPGState, "PersonFactionModel")


class TestCycleGuardHooksRegistered:
    """The cycle-guard hooks for FactionManager, LocationManager, and
    HookDependencyManager are registered at import time. The full DB
    integration is exercised under the test-app fixture; here we just
    confirm the hook functions exist on the module so the registration
    succeeded."""

    def test_cycle_guard_hook_callables_present(self):
        from serverframework.extensions.rpg_state import BLL_RPGState

        for name in (
            "_faction_no_cycle",
            "_location_no_cycle",
            "_hook_dependency_no_cycle",
        ):
            assert hasattr(
                BLL_RPGState, name
            ), f"cycle-guard hook {name} missing"

    def test_cycle_guard_utility_imported(self):
        from serverframework.lib.CycleGuard import (
            CycleGuardError,
            would_create_dag_cycle,
            would_create_tree_cycle,
        )

        assert callable(would_create_tree_cycle)
        assert callable(would_create_dag_cycle)
        assert issubclass(CycleGuardError, Exception)
