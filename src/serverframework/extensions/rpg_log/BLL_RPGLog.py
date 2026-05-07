"""rpg_log — event log models and managers for RPG campaigns.

Sibling to ``rpg_state``. Every log row carries:
- ``campaign_id`` (Reference to rpg_state.CampaignModel)
- ``occurred_at`` — in-game time the event happened, distinct from the
  audit ``created_at`` (when the row was logged).
- ``session_id`` — optional grouping by play session.

Logs are editable (UpdateMixinModel) so GMs can fix typos and retcon;
the standard ``updated_at`` / ``updated_by_user_id`` audit fields
preserve the change trail.

Multiple FKs to the same target table (e.g. attacker / defender both
referencing characters; from / to both referencing locations) are
declared as plain ``Optional[str]`` columns rather than via the
``Reference`` mixin, since the metaclass keys field-name generation off
the target model name and would collide.
"""

from datetime import datetime
from typing import ClassVar, List, Optional, Type

from pydantic import Field

from serverframework.extensions.rpg_state.BLL_RPGState import (
    CampaignModel,
    CharacterModel,
    ItemInstanceModel,
    LocationModel,
    TraitModel,
)
from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    DescriptionMixinModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Encounters & participation
# ---------------------------------------------------------------------------


class EncounterLogModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    CampaignModel.Reference.Optional,
    LocationModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["EncounterLogManager"]] = None
    occurred_at: Optional[datetime] = Field(
        None, description="In-game time the encounter began"
    )
    session_id: Optional[str] = Field(None, description="Optional play-session group")
    started_at: Optional[datetime] = Field(None)
    ended_at: Optional[datetime] = Field(None)
    outcome: Optional[str] = Field(
        None, description="Free-form summary (won|fled|negotiated|...)"
    )
    table_comment: ClassVar[str] = "Top-level encounter log (combat or otherwise)"

    class Create(
        BaseModel,
        CampaignModel.Reference.ID.Optional,
        LocationModel.Reference.ID.Optional,
    ):
        name: Optional[str] = None
        description: Optional[str] = None
        occurred_at: Optional[datetime] = None
        session_id: Optional[str] = None
        started_at: Optional[datetime] = None
        ended_at: Optional[datetime] = None
        outcome: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        ended_at: Optional[datetime] = None
        outcome: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
        LocationModel.Reference.ID.Search,
    ):
        occurred_at: Optional[DateSearchModel] = None
        session_id: Optional[StringSearchModel] = None
        outcome: Optional[StringSearchModel] = None


class EncounterLogManager(AbstractBLLManager, RouterMixin):
    _model = EncounterLogModel


EncounterLogModel.Manager = EncounterLogManager


class EncounterParticipantModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    EncounterLogModel.Reference.Optional,
    CharacterModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["EncounterParticipantManager"]] = None
    # Free-form side label so multi-side fights work
    # (attackers / defenders / wildlife / bystanders / ...).
    side: Optional[str] = Field(None, description="Side / faction-of-convenience label")
    # Faction at encounter time. Manual to avoid a Reference collision —
    # the participant table doesn't need a back-resolved Faction object.
    faction_id: Optional[str] = Field(None)
    initiative: Optional[float] = Field(None)
    table_comment: ClassVar[str] = (
        "Roster of an encounter. Faction inferable; explicit faction_id "
        "captures the snapshot at encounter time."
    )

    class Create(
        BaseModel,
        EncounterLogModel.Reference.ID.Optional,
        CharacterModel.Reference.ID.Optional,
    ):
        side: Optional[str] = None
        faction_id: Optional[str] = None
        initiative: Optional[float] = None

    class Update(BaseModel):
        side: Optional[str] = None
        faction_id: Optional[str] = None
        initiative: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        EncounterLogModel.Reference.ID.Search,
        CharacterModel.Reference.ID.Search,
    ):
        side: Optional[StringSearchModel] = None
        faction_id: Optional[StringSearchModel] = None


class EncounterParticipantManager(AbstractBLLManager, RouterMixin):
    _model = EncounterParticipantModel


EncounterParticipantModel.Manager = EncounterParticipantManager


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------


class CombatActionLogModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    EncounterLogModel.Reference.Optional,
    CampaignModel.Reference.Optional,
    ItemInstanceModel.Reference.Optional,
    TraitModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CombatActionLogManager"]] = None
    # Two characters per action (actor + target); declared manually since
    # CharacterModel.Reference would collide.
    actor_character_id: Optional[str] = Field(None)
    target_character_id: Optional[str] = Field(None)
    action_type: Optional[str] = Field(
        None, description="attack|cast|defend|item_use|movement|other"
    )
    result: Optional[str] = Field(
        None, description="hit|miss|crit|saved|... (free-form)"
    )
    damage_amount: Optional[float] = Field(None)
    round_number: Optional[int] = Field(None)
    occurred_at: Optional[datetime] = Field(None)
    session_id: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "One row per combat action. trait_id = spell/skill used (unified Trait); "
        "item_instance_id = weapon/consumable used."
    )

    class Create(
        BaseModel,
        EncounterLogModel.Reference.ID.Optional,
        CampaignModel.Reference.ID.Optional,
        ItemInstanceModel.Reference.ID.Optional,
        TraitModel.Reference.ID.Optional,
    ):
        actor_character_id: Optional[str] = None
        target_character_id: Optional[str] = None
        action_type: Optional[str] = None
        result: Optional[str] = None
        damage_amount: Optional[float] = None
        round_number: Optional[int] = None
        occurred_at: Optional[datetime] = None
        session_id: Optional[str] = None

    class Update(BaseModel):
        action_type: Optional[str] = None
        result: Optional[str] = None
        damage_amount: Optional[float] = None
        round_number: Optional[int] = None

    class Search(
        ApplicationModel.Search,
        EncounterLogModel.Reference.ID.Search,
        CampaignModel.Reference.ID.Search,
    ):
        actor_character_id: Optional[StringSearchModel] = None
        target_character_id: Optional[StringSearchModel] = None
        action_type: Optional[StringSearchModel] = None
        damage_amount: Optional[NumericalSearchModel] = None
        round_number: Optional[NumericalSearchModel] = None
        occurred_at: Optional[DateSearchModel] = None


class CombatActionLogManager(AbstractBLLManager, RouterMixin):
    _model = CombatActionLogModel


CombatActionLogModel.Manager = CombatActionLogManager


# ---------------------------------------------------------------------------
# Dialogue (with group support)
# ---------------------------------------------------------------------------


class DialogueLogModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CampaignModel.Reference.Optional,
    LocationModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["DialogueLogManager"]] = None
    actor_character_id: Optional[str] = Field(None, description="Speaker")
    target_character_id: Optional[str] = Field(
        None, description="Primary addressee (null for group / monologue)"
    )
    text: Optional[str] = Field(None)
    tone: Optional[str] = Field(None, description="hostile|friendly|sarcastic|...")
    occurred_at: Optional[datetime] = Field(None)
    session_id: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "One uttered line. DialogueParticipant carries the full audience "
        "for group conversations."
    )

    class Create(
        BaseModel,
        CampaignModel.Reference.ID.Optional,
        LocationModel.Reference.ID.Optional,
    ):
        actor_character_id: Optional[str] = None
        target_character_id: Optional[str] = None
        text: Optional[str] = None
        tone: Optional[str] = None
        occurred_at: Optional[datetime] = None
        session_id: Optional[str] = None

    class Update(BaseModel):
        text: Optional[str] = None
        tone: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
        LocationModel.Reference.ID.Search,
    ):
        actor_character_id: Optional[StringSearchModel] = None
        target_character_id: Optional[StringSearchModel] = None
        tone: Optional[StringSearchModel] = None
        occurred_at: Optional[DateSearchModel] = None


class DialogueLogManager(AbstractBLLManager, RouterMixin):
    _model = DialogueLogModel


DialogueLogModel.Manager = DialogueLogManager


class DialogueParticipantModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    DialogueLogModel.Reference.Optional,
    CharacterModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["DialogueParticipantManager"]] = None
    role: Optional[str] = Field(
        None, description="speaker|addressee|overhearer"
    )
    table_comment: ClassVar[str] = (
        "Audience join for DialogueLog (group conversations, eavesdroppers)."
    )

    class Create(
        BaseModel,
        DialogueLogModel.Reference.ID.Optional,
        CharacterModel.Reference.ID.Optional,
    ):
        role: Optional[str] = None

    class Update(BaseModel):
        role: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        DialogueLogModel.Reference.ID.Search,
        CharacterModel.Reference.ID.Search,
    ):
        role: Optional[StringSearchModel] = None


class DialogueParticipantManager(AbstractBLLManager, RouterMixin):
    _model = DialogueParticipantModel


DialogueParticipantModel.Manager = DialogueParticipantManager


# ---------------------------------------------------------------------------
# Interactions (use / examine / pickup / open / lock / ...)
# ---------------------------------------------------------------------------


class InteractionLogModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CampaignModel.Reference.Optional,
    LocationModel.Reference.Optional,
    ItemInstanceModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["InteractionLogManager"]] = None
    actor_character_id: Optional[str] = Field(None)
    interaction_type: Optional[str] = Field(
        None,
        description="examine|use|open|pickup|drop|lock|unlock|read|...",
    )
    notes: Optional[str] = Field(None)
    occurred_at: Optional[datetime] = Field(None)
    session_id: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "Non-combat / non-dialogue interactions with the world or items."
    )

    class Create(
        BaseModel,
        CampaignModel.Reference.ID.Optional,
        LocationModel.Reference.ID.Optional,
        ItemInstanceModel.Reference.ID.Optional,
    ):
        actor_character_id: Optional[str] = None
        interaction_type: Optional[str] = None
        notes: Optional[str] = None
        occurred_at: Optional[datetime] = None
        session_id: Optional[str] = None

    class Update(BaseModel):
        interaction_type: Optional[str] = None
        notes: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
        LocationModel.Reference.ID.Search,
        ItemInstanceModel.Reference.ID.Search,
    ):
        actor_character_id: Optional[StringSearchModel] = None
        interaction_type: Optional[StringSearchModel] = None
        occurred_at: Optional[DateSearchModel] = None


class InteractionLogManager(AbstractBLLManager, RouterMixin):
    _model = InteractionLogModel


InteractionLogModel.Manager = InteractionLogManager


# ---------------------------------------------------------------------------
# Transactions (trade, theft, looting, gifts)
# ---------------------------------------------------------------------------


class TransactionLogModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CampaignModel.Reference.Optional,
    ItemInstanceModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TransactionLogManager"]] = None
    quantity: Optional[int] = Field(1)
    # From / to. Manual columns since both reference the same target tables.
    from_location_id: Optional[str] = Field(None)
    to_location_id: Optional[str] = Field(None)
    from_owner_character_id: Optional[str] = Field(None)
    to_owner_character_id: Optional[str] = Field(None)
    from_owner_faction_id: Optional[str] = Field(None)
    to_owner_faction_id: Optional[str] = Field(None)
    price: Optional[float] = Field(None, description="Sale price (null = non-monetary)")
    kind: Optional[str] = Field(
        None, description="trade|gift|theft|loot|inheritance|..."
    )
    occurred_at: Optional[datetime] = Field(None)
    session_id: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "One row per item movement. captures trade, theft, looting, and gifts."
    )

    class Create(
        BaseModel,
        CampaignModel.Reference.ID.Optional,
        ItemInstanceModel.Reference.ID.Optional,
    ):
        quantity: Optional[int] = None
        from_location_id: Optional[str] = None
        to_location_id: Optional[str] = None
        from_owner_character_id: Optional[str] = None
        to_owner_character_id: Optional[str] = None
        from_owner_faction_id: Optional[str] = None
        to_owner_faction_id: Optional[str] = None
        price: Optional[float] = None
        kind: Optional[str] = None
        occurred_at: Optional[datetime] = None
        session_id: Optional[str] = None

    class Update(BaseModel):
        kind: Optional[str] = None
        price: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
        ItemInstanceModel.Reference.ID.Search,
    ):
        kind: Optional[StringSearchModel] = None
        from_owner_character_id: Optional[StringSearchModel] = None
        to_owner_character_id: Optional[StringSearchModel] = None
        from_owner_faction_id: Optional[StringSearchModel] = None
        to_owner_faction_id: Optional[StringSearchModel] = None
        occurred_at: Optional[DateSearchModel] = None


class TransactionLogManager(AbstractBLLManager, RouterMixin):
    _model = TransactionLogModel


TransactionLogModel.Manager = TransactionLogManager


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    EncounterLogModel,
    EncounterParticipantModel,
    CombatActionLogModel,
    DialogueLogModel,
    DialogueParticipantModel,
    InteractionLogModel,
    TransactionLogModel,
]


__all__ = [
    "EncounterLogModel",
    "EncounterLogManager",
    "EncounterParticipantModel",
    "EncounterParticipantManager",
    "CombatActionLogModel",
    "CombatActionLogManager",
    "DialogueLogModel",
    "DialogueLogManager",
    "DialogueParticipantModel",
    "DialogueParticipantManager",
    "InteractionLogModel",
    "InteractionLogManager",
    "TransactionLogModel",
    "TransactionLogManager",
    "ALL_MODELS",
]
