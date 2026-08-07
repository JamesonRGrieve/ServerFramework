"""Structural tests for the rpg_log extension's BLL surface."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "rpg_log_test")

from serverframework.extensions.rpg_log.BLL_RPGLog import (
    ALL_MODELS,
    CombatActionLogManager,
    CombatActionLogModel,
    DialogueLogManager,
    DialogueLogModel,
    DialogueParticipantManager,
    DialogueParticipantModel,
    EncounterLogManager,
    EncounterLogModel,
    EncounterParticipantManager,
    EncounterParticipantModel,
    InteractionLogManager,
    InteractionLogModel,
    TransactionLogManager,
    TransactionLogModel,
)


class TestModelManagerWiring:
    PAIRS = [
        (EncounterLogModel, EncounterLogManager),
        (EncounterParticipantModel, EncounterParticipantManager),
        (CombatActionLogModel, CombatActionLogManager),
        (DialogueLogModel, DialogueLogManager),
        (DialogueParticipantModel, DialogueParticipantManager),
        (InteractionLogModel, InteractionLogManager),
        (TransactionLogModel, TransactionLogManager),
    ]

    def test_each_model_has_its_manager(self):
        for model, manager in self.PAIRS:
            assert model.Manager is manager
            assert manager._model is model

    def test_all_models_listed(self):
        assert set(ALL_MODELS) == {model for model, _ in self.PAIRS}


class TestCreateUpdateSearchPresent:
    def test_nested_classes_exist(self):
        for model in ALL_MODELS:
            assert hasattr(model, "Create"), f"{model.__name__} missing Create"
            assert hasattr(model, "Update"), f"{model.__name__} missing Update"
            assert hasattr(model, "Search"), f"{model.__name__} missing Search"


class TestEncounterParticipation:
    """Multi-faction encounters are expressed via EncounterParticipant
    rows with free-form side labels, not a single FactionID on
    EncounterLog."""

    def test_encounter_log_has_no_faction_field(self):
        ep = EncounterLogModel.Create(name="Tavern brawl")
        assert not hasattr(ep, "faction_id")

    def test_participant_carries_side_and_faction(self):
        p = EncounterParticipantModel.Create(
            encounter_log_id="enc-1",
            person_id="bob",
            faction_id="party",
            side="defenders",
            initiative=14.0,
        )
        assert p.side == "defenders"
        assert p.faction_id == "party"


class TestCombatActionShape:
    """A combat action has actor + (optional) target persons and may
    reference a weapon (item_instance) and/or a spell/skill (trait)."""

    def test_attack_with_weapon(self):
        a = CombatActionLogModel.Create(
            encounter_log_id="enc-1",
            actor_person_id="bob",
            target_person_id="orc-1",
            item_instance_id="ii-sword",
            action_type="attack",
            result="hit",
            damage_amount=8.0,
            round_number=2,
        )
        assert a.actor_person_id == "bob"
        assert a.item_instance_id == "ii-sword"
        assert a.damage_amount == 8.0

    def test_spell_uses_trait_id(self):
        # A spell IS a Trait (kind='spell') in the unified model.
        a = CombatActionLogModel.Create(
            encounter_log_id="enc-1",
            actor_person_id="wizard",
            target_person_id="orc-2",
            trait_id="spell-fireball",
            action_type="cast",
            result="hit",
            damage_amount=24.0,
        )
        assert a.trait_id == "spell-fireball"


class TestDialogueGroupSupport:
    def test_dialogue_log_basic(self):
        d = DialogueLogModel.Create(
            actor_person_id="bob",
            target_person_id="innkeep",
            text="One ale, please.",
            tone="friendly",
        )
        assert d.actor_person_id == "bob"
        assert d.text == "One ale, please."

    def test_dialogue_participant_for_eavesdroppers(self):
        # A bard secretly listening from the next table.
        p = DialogueParticipantModel.Create(
            dialogue_log_id="dl-1",
            person_id="bard",
            role="overhearer",
        )
        assert p.role == "overhearer"


class TestTransactionShape:
    """TransactionLog captures the full from→to delta in a single row."""

    def test_trade_carries_both_endpoints(self):
        t = TransactionLogModel.Create(
            item_instance_id="ii-sword",
            quantity=1,
            from_owner_person_id="merchant",
            to_owner_person_id="bob",
            from_location_id="loc-shop",
            to_location_id="loc-bob-pack",
            price=50.0,
            kind="trade",
        )
        assert t.from_owner_person_id == "merchant"
        assert t.to_owner_person_id == "bob"
        assert t.price == 50.0
        assert t.kind == "trade"

    def test_theft_has_no_consensual_owner_handoff(self):
        # Stolen: location moves but legal owner doesn't.
        t = TransactionLogModel.Create(
            item_instance_id="ii-amulet",
            quantity=1,
            from_location_id="loc-altar",
            to_location_id="loc-thief-pack",
            kind="theft",
        )
        assert t.from_owner_person_id is None
        assert t.to_owner_person_id is None
        assert t.kind == "theft"


class TestLogsAreEditable:
    """Logs use UpdateMixinModel — editable over append-only."""

    def test_combat_action_update_present(self):
        u = CombatActionLogModel.Update(damage_amount=10.0, result="crit")
        assert u.damage_amount == 10.0
        assert u.result == "crit"

    def test_dialogue_text_editable(self):
        u = DialogueLogModel.Update(text="One ale, on the house.")
        assert u.text == "One ale, on the house."
