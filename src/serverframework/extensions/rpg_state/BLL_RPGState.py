"""rpg_state — present-state models and managers for RPG campaigns.

Model groups (declared in dependency order so ``.Reference`` mixins resolve):

1. ``GameSystemModel`` — reference catalog ("DnD 5e", "Pathfinder 2e", ...).
2. ``CampaignModel`` — root of campaign-scoped data; ``user_id`` is the GM.
3. Trait / StatusEffect templates — system-agnostic property catalog. The
   unified ``TraitModel`` carries a ``kind`` discriminator
   (attribute / skill / spell / talent / feat / language / ability) so the
   same calculation surface handles permanent learnings and transient buffs.
4. ``CharacterModel`` — PCs, NPCs, monsters; ``user_id`` is the controlling
   player when one exists (NPCs leave it null).
5. Per-character joins (``CharacterTraitModel``,
   ``CharacterStatusEffectModel``). Magnitude lives on the join: a
   character's STR=16 is a row on ``CharacterTraitModel.value``; the
   "+2 STR" granted by Bull's Strength is a ``StatusEffectTraitModel``
   row with operation='additive', value=2.
6. Faction hierarchy. ``FactionModel`` uses ``parent_id`` (from
   ``ParentMixinModel``) to model squads-within-platoons / parties-as-
   sub-guilds without a separate Party entity.
7. Spatial model. ``LocationModel`` is recursive (``parent_id``) and may
   itself be the inside of a container item via
   ``container_item_instance_id``. Equipment is modelled as a Location
   bound to a character (``associated_character_id``); equipping is a
   spatial move, no IsEquipped flag.
8. Quest / Objective templates separated from per-character /
   per-faction instances. Objectives carry an optional
   ``prerequisite_objective_id`` for branching / linear chains.

Ownership semantics on ``ItemInstanceModel``:
- ``owner_faction_id`` = de jure / "in principle" owner (the Guild owns
  this sword).
- ``owner_character_id`` = de facto / "in practice" bearer (Bob carries
  it). Either, both, or neither may be set; theft is the natural state of
  having ``owner_character_id`` differ from the legal owner without an
  intermediate ``TransactionLog``.
"""

from datetime import datetime
from typing import ClassVar, List, Optional, Type

from pydantic import Field

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
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import UserModel


# ---------------------------------------------------------------------------
# 1. GameSystem — reference catalog
# ---------------------------------------------------------------------------


class GameSystemModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["GameSystemManager"]] = None
    table_comment: ClassVar[str] = (
        "Reference catalog of supported RPG systems (DnD 5e, Pathfinder 2e, ...)"
    )

    class Create(BaseModel):
        name: str = Field(..., description="Display name of the system")
        description: Optional[str] = Field(None)

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None


class GameSystemManager(AbstractBLLManager, RouterMixin):
    _model = GameSystemModel


GameSystemModel.Manager = GameSystemManager


# ---------------------------------------------------------------------------
# 2. Campaign — tenant root
# ---------------------------------------------------------------------------


class CampaignModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    GameSystemModel.Reference.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CampaignManager"]] = None
    table_comment: ClassVar[str] = (
        "Top-level campaign owning all in-game state. user_id = GM/owner."
    )

    class Create(
        BaseModel,
        GameSystemModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
    ):
        name: str = Field(...)
        description: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        GameSystemModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None


class CampaignManager(AbstractBLLManager, RouterMixin):
    _model = CampaignModel


CampaignModel.Manager = CampaignManager


# ---------------------------------------------------------------------------
# 3. Trait, StatusEffect, StatusEffectTrait — property catalog
# ---------------------------------------------------------------------------


class TraitModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    GameSystemModel.Reference.Optional,
    CampaignModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TraitManager"]] = None
    # Unified discriminator: attribute|skill|spell|talent|feat|language|ability.
    # Free string so downstream systems can extend without a schema change.
    kind: Optional[str] = Field(
        None,
        description="attribute|skill|spell|talent|feat|language|ability|...",
    )
    # campaign_id null = global template; set = campaign-specific override.
    table_comment: ClassVar[str] = (
        "Unified property catalog: stats, skills, spells, talents, feats. "
        "campaign_id=NULL means a globally-shared template."
    )

    class Create(
        BaseModel,
        GameSystemModel.Reference.ID.Optional,
        CampaignModel.Reference.ID.Optional,
    ):
        name: str = Field(...)
        description: Optional[str] = None
        kind: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        kind: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        GameSystemModel.Reference.ID.Search,
        CampaignModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None


class TraitManager(AbstractBLLManager, RouterMixin):
    _model = TraitModel


TraitModel.Manager = TraitManager


class StatusEffectModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    GameSystemModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["StatusEffectManager"]] = None
    default_duration_seconds: Optional[int] = Field(
        None,
        description="Default lifetime when applied; null = until removed",
    )
    table_comment: ClassVar[str] = (
        "Status effect templates (buffs, debuffs, conditions). "
        "Per-trait modifiers live on StatusEffectTrait."
    )

    class Create(BaseModel, GameSystemModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        default_duration_seconds: Optional[int] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        default_duration_seconds: Optional[int] = None

    class Search(ApplicationModel.Search, GameSystemModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None


class StatusEffectManager(AbstractBLLManager, RouterMixin):
    _model = StatusEffectModel


StatusEffectModel.Manager = StatusEffectManager


class StatusEffectTraitModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    StatusEffectModel.Reference.Optional,
    TraitModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["StatusEffectTraitManager"]] = None
    operation: Optional[str] = Field(
        None,
        description="additive|multiplicative|set",
    )
    value: Optional[float] = Field(
        None,
        description="Modifier magnitude (e.g. +2 for additive, x1.5 for multiplicative)",
    )
    table_comment: ClassVar[str] = (
        "Per-trait modifiers contributed by a StatusEffect template. "
        "Bull's Strength → (status_effect=BullsStrength, trait=STR, "
        "operation='additive', value=2.0)."
    )

    class Create(
        BaseModel,
        StatusEffectModel.Reference.ID.Optional,
        TraitModel.Reference.ID.Optional,
    ):
        operation: Optional[str] = None
        value: Optional[float] = None

    class Update(BaseModel):
        operation: Optional[str] = None
        value: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        StatusEffectModel.Reference.ID.Search,
        TraitModel.Reference.ID.Search,
    ):
        operation: Optional[StringSearchModel] = None
        value: Optional[NumericalSearchModel] = None


class StatusEffectTraitManager(AbstractBLLManager, RouterMixin):
    _model = StatusEffectTraitModel


StatusEffectTraitModel.Manager = StatusEffectTraitManager


# ---------------------------------------------------------------------------
# 4. Character
# ---------------------------------------------------------------------------


class CharacterModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    CampaignModel.Reference.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterManager"]] = None
    # pc|npc|monster|creature|vehicle|construct — free-form for system flex.
    kind: Optional[str] = Field(None, description="pc|npc|monster|creature|...")
    level: Optional[int] = Field(None, description="System-defined level/tier")
    table_comment: ClassVar[str] = (
        "Any in-game actor. user_id = controlling player (PC); null = NPC."
    )

    class Create(
        BaseModel,
        CampaignModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
    ):
        name: str = Field(...)
        description: Optional[str] = None
        kind: Optional[str] = None
        level: Optional[int] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        kind: Optional[str] = None
        level: Optional[int] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None


class CharacterManager(AbstractBLLManager, RouterMixin):
    _model = CharacterModel


CharacterModel.Manager = CharacterManager


# ---------------------------------------------------------------------------
# 5. Per-character joins
# ---------------------------------------------------------------------------


class CharacterTraitModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CharacterModel.Reference.Optional,
    TraitModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterTraitManager"]] = None
    value: Optional[float] = Field(
        None,
        description="Base magnitude (e.g. STR=16). Active modifiers stack on top.",
    )
    rank: Optional[int] = Field(
        None,
        description="Optional discrete rank (e.g. proficiency tier)",
    )
    notes: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "Per-character base trait values. Effective value = this.value + "
        "Σ active StatusEffectTrait modifiers via CharacterStatusEffect."
    )

    class Create(
        BaseModel,
        CharacterModel.Reference.ID.Optional,
        TraitModel.Reference.ID.Optional,
    ):
        value: Optional[float] = None
        rank: Optional[int] = None
        notes: Optional[str] = None

    class Update(BaseModel):
        value: Optional[float] = None
        rank: Optional[int] = None
        notes: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        CharacterModel.Reference.ID.Search,
        TraitModel.Reference.ID.Search,
    ):
        value: Optional[NumericalSearchModel] = None
        rank: Optional[NumericalSearchModel] = None


class CharacterTraitManager(AbstractBLLManager, RouterMixin):
    _model = CharacterTraitModel


CharacterTraitModel.Manager = CharacterTraitManager


class CharacterStatusEffectModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CharacterModel.Reference.Optional,
    StatusEffectModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterStatusEffectManager"]] = None
    started_at: Optional[datetime] = Field(None)
    expires_at: Optional[datetime] = Field(
        None,
        description="Effect end; null = persists until manually removed",
    )
    # Caster / source. Manual columns (Character already used for the
    # affected character; ItemInstance.Reference would create a cycle).
    source_character_id: Optional[str] = Field(
        None, description="The caster / applier"
    )
    source_item_instance_id: Optional[str] = Field(
        None, description="Item consumed to apply the effect"
    )
    table_comment: ClassVar[str] = (
        "Active StatusEffect attachments per character. "
        "The effect's per-trait modifiers come from StatusEffectTrait."
    )

    class Create(
        BaseModel,
        CharacterModel.Reference.ID.Optional,
        StatusEffectModel.Reference.ID.Optional,
    ):
        started_at: Optional[datetime] = None
        expires_at: Optional[datetime] = None
        source_character_id: Optional[str] = None
        source_item_instance_id: Optional[str] = None

    class Update(BaseModel):
        expires_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        CharacterModel.Reference.ID.Search,
        StatusEffectModel.Reference.ID.Search,
    ):
        expires_at: Optional[DateSearchModel] = None


class CharacterStatusEffectManager(AbstractBLLManager, RouterMixin):
    _model = CharacterStatusEffectModel


CharacterStatusEffectModel.Manager = CharacterStatusEffectManager


# ---------------------------------------------------------------------------
# 6. Faction hierarchy
# ---------------------------------------------------------------------------


class FactionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    ParentMixinModel.Optional,
    CampaignModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FactionManager"]] = None
    table_comment: ClassVar[str] = (
        "Hierarchical faction (parent_id self-FK). A 'party' is a "
        "Faction with a Guild parent or no parent."
    )

    class Create(BaseModel, CampaignModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        parent_id: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        parent_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        CampaignModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        parent_id: Optional[StringSearchModel] = None


class FactionManager(AbstractBLLManager, RouterMixin):
    _model = FactionModel


FactionModel.Manager = FactionManager


class CharacterFactionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CharacterModel.Reference.Optional,
    FactionModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterFactionManager"]] = None
    role: Optional[str] = Field(
        None, description="member|leader|enemy|ally|... (free-form)"
    )
    reputation: Optional[float] = Field(
        None, description="Standing within the faction"
    )
    table_comment: ClassVar[str] = "Character↔Faction membership join"

    class Create(
        BaseModel,
        CharacterModel.Reference.ID.Optional,
        FactionModel.Reference.ID.Optional,
    ):
        role: Optional[str] = None
        reputation: Optional[float] = None

    class Update(BaseModel):
        role: Optional[str] = None
        reputation: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        CharacterModel.Reference.ID.Search,
        FactionModel.Reference.ID.Search,
    ):
        role: Optional[StringSearchModel] = None


class CharacterFactionManager(AbstractBLLManager, RouterMixin):
    _model = CharacterFactionModel


CharacterFactionModel.Manager = CharacterFactionManager


# ---------------------------------------------------------------------------
# 7. Spatial: Location / Item / ItemInstance
# ---------------------------------------------------------------------------


class LocationModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    ParentMixinModel.Optional,
    CampaignModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["LocationManager"]] = None
    # When this Location IS the inside of a container item (a satchel's
    # interior is a Location whose container_item_instance_id points to
    # the satchel ItemInstance). Manual column to avoid the cyclic
    # ItemInstance ↔ Location reference at class-definition time.
    container_item_instance_id: Optional[str] = Field(
        None,
        description="Set when this Location represents the inside of a container item",
    )
    associated_character_id: Optional[str] = Field(
        None,
        description="Set when this Location is an equipment slot bound to a character",
    )
    kind: Optional[str] = Field(
        None,
        description="region|room|container|equipment_slot|inventory|... (free-form)",
    )
    table_comment: ClassVar[str] = (
        "Recursive spatial node. parent_id = parent location; "
        "container_item_instance_id = container that this is the inside of; "
        "associated_character_id = equipment-slot owner. Equipping is a move."
    )

    class Create(BaseModel, CampaignModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        parent_id: Optional[str] = None
        container_item_instance_id: Optional[str] = None
        associated_character_id: Optional[str] = None
        kind: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        parent_id: Optional[str] = None
        container_item_instance_id: Optional[str] = None
        associated_character_id: Optional[str] = None
        kind: Optional[str] = None

    class Search(ApplicationModel.Search, CampaignModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None
        parent_id: Optional[StringSearchModel] = None


class LocationManager(AbstractBLLManager, RouterMixin):
    _model = LocationModel


LocationModel.Manager = LocationManager


class ItemModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    GameSystemModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["ItemManager"]] = None
    weight: Optional[float] = Field(None, description="Per-unit weight")
    base_value: Optional[float] = Field(
        None, description="Default monetary value per unit"
    )
    stack_size: Optional[int] = Field(
        1, description="Max units per ItemInstance stack (1 = non-stackable)"
    )
    kind: Optional[str] = Field(
        None,
        description="weapon|armor|consumable|container|currency|misc (free-form)",
    )
    table_comment: ClassVar[str] = (
        "Item template (catalog). ItemInstance is the per-instance row."
    )

    class Create(BaseModel, GameSystemModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        weight: Optional[float] = None
        base_value: Optional[float] = None
        stack_size: Optional[int] = None
        kind: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        weight: Optional[float] = None
        base_value: Optional[float] = None
        stack_size: Optional[int] = None
        kind: Optional[str] = None

    class Search(ApplicationModel.Search, GameSystemModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None


class ItemManager(AbstractBLLManager, RouterMixin):
    _model = ItemModel


ItemModel.Manager = ItemManager


class ItemInstanceModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ItemModel.Reference.Optional,
    LocationModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["ItemInstanceManager"]] = None
    # Two ownership axes, both nullable, no XOR constraint:
    #   owner_faction_id = de jure / "in principle" (the Guild owns it)
    #   owner_character_id = de facto / "in practice" (Bob is carrying it)
    # Theft = the two disagree without an intervening TransactionLog.
    owner_character_id: Optional[str] = Field(
        None, description="De facto bearer / in-practice owner"
    )
    owner_faction_id: Optional[str] = Field(
        None, description="De jure / in-principle owner"
    )
    quantity: Optional[int] = Field(1, description="Stack count (≥1)")
    durability: Optional[float] = Field(None, description="System-defined HP/durability")
    table_comment: ClassVar[str] = (
        "Per-instance item row. location_id = current physical spot; "
        "owner_* fields decoupled from location to capture theft / lend."
    )

    class Create(
        BaseModel,
        ItemModel.Reference.ID.Optional,
        LocationModel.Reference.ID.Optional,
    ):
        owner_character_id: Optional[str] = None
        owner_faction_id: Optional[str] = None
        quantity: Optional[int] = None
        durability: Optional[float] = None

    class Update(BaseModel):
        location_id: Optional[str] = None
        owner_character_id: Optional[str] = None
        owner_faction_id: Optional[str] = None
        quantity: Optional[int] = None
        durability: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        ItemModel.Reference.ID.Search,
        LocationModel.Reference.ID.Search,
    ):
        owner_character_id: Optional[StringSearchModel] = None
        owner_faction_id: Optional[StringSearchModel] = None


class ItemInstanceManager(AbstractBLLManager, RouterMixin):
    _model = ItemInstanceModel


ItemInstanceModel.Manager = ItemInstanceManager


# ---------------------------------------------------------------------------
# 8. Quests, Objectives, and per-character/per-faction progress
# ---------------------------------------------------------------------------


class QuestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    CampaignModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["QuestManager"]] = None
    # Quest issuer; nullable so unattributed quests work. Manual to avoid
    # collision with future Faction.Reference uses on this model.
    giver_faction_id: Optional[str] = Field(
        None, description="Faction that issued the quest"
    )
    table_comment: ClassVar[str] = "Quest template"

    class Create(BaseModel, CampaignModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        giver_faction_id: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        giver_faction_id: Optional[str] = None

    class Search(ApplicationModel.Search, CampaignModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None


class QuestManager(AbstractBLLManager, RouterMixin):
    _model = QuestModel


QuestModel.Manager = QuestManager


class ObjectiveModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    QuestModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["ObjectiveManager"]] = None
    prerequisite_objective_id: Optional[str] = Field(
        None,
        description="Objective that must complete first (self-FK; chains/branches)",
    )
    order_index: Optional[int] = Field(
        None, description="Display/precedence ordering within the quest"
    )
    table_comment: ClassVar[str] = (
        "Objective template; chains via prerequisite_objective_id."
    )

    class Create(BaseModel, QuestModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        prerequisite_objective_id: Optional[str] = None
        order_index: Optional[int] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        prerequisite_objective_id: Optional[str] = None
        order_index: Optional[int] = None

    class Search(ApplicationModel.Search, QuestModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None
        prerequisite_objective_id: Optional[StringSearchModel] = None


class ObjectiveManager(AbstractBLLManager, RouterMixin):
    _model = ObjectiveModel


ObjectiveModel.Manager = ObjectiveManager


class CharacterQuestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CharacterModel.Reference.Optional,
    QuestModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterQuestManager"]] = None
    status: Optional[str] = Field(
        None, description="available|active|completed|failed|abandoned"
    )
    accepted_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    table_comment: ClassVar[str] = "Per-character quest progress instance"

    class Create(
        BaseModel,
        CharacterModel.Reference.ID.Optional,
        QuestModel.Reference.ID.Optional,
    ):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None

    class Update(BaseModel):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        CharacterModel.Reference.ID.Search,
        QuestModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class CharacterQuestManager(AbstractBLLManager, RouterMixin):
    _model = CharacterQuestModel


CharacterQuestModel.Manager = CharacterQuestManager


class FactionQuestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    FactionModel.Reference.Optional,
    QuestModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FactionQuestManager"]] = None
    status: Optional[str] = Field(None, description="See CharacterQuestModel.status")
    accepted_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    table_comment: ClassVar[str] = "Per-faction quest progress instance"

    class Create(
        BaseModel,
        FactionModel.Reference.ID.Optional,
        QuestModel.Reference.ID.Optional,
    ):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None

    class Update(BaseModel):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        FactionModel.Reference.ID.Search,
        QuestModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class FactionQuestManager(AbstractBLLManager, RouterMixin):
    _model = FactionQuestModel


FactionQuestModel.Manager = FactionQuestManager


class CharacterObjectiveModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    CharacterModel.Reference.Optional,
    ObjectiveModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["CharacterObjectiveManager"]] = None
    status: Optional[str] = Field(None)
    progress: Optional[float] = Field(
        None, description="Numeric progress (e.g. 3 of 5 wolves slain)"
    )
    completed_at: Optional[datetime] = Field(None)
    table_comment: ClassVar[str] = "Per-character objective progress instance"

    class Create(
        BaseModel,
        CharacterModel.Reference.ID.Optional,
        ObjectiveModel.Reference.ID.Optional,
    ):
        status: Optional[str] = None
        progress: Optional[float] = None
        completed_at: Optional[datetime] = None

    class Update(BaseModel):
        status: Optional[str] = None
        progress: Optional[float] = None
        completed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        CharacterModel.Reference.ID.Search,
        ObjectiveModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class CharacterObjectiveManager(AbstractBLLManager, RouterMixin):
    _model = CharacterObjectiveModel


CharacterObjectiveModel.Manager = CharacterObjectiveManager


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    GameSystemModel,
    CampaignModel,
    TraitModel,
    StatusEffectModel,
    StatusEffectTraitModel,
    CharacterModel,
    CharacterTraitModel,
    CharacterStatusEffectModel,
    FactionModel,
    CharacterFactionModel,
    LocationModel,
    ItemModel,
    ItemInstanceModel,
    QuestModel,
    ObjectiveModel,
    CharacterQuestModel,
    FactionQuestModel,
    CharacterObjectiveModel,
]


__all__ = [
    "GameSystemModel",
    "GameSystemManager",
    "CampaignModel",
    "CampaignManager",
    "TraitModel",
    "TraitManager",
    "StatusEffectModel",
    "StatusEffectManager",
    "StatusEffectTraitModel",
    "StatusEffectTraitManager",
    "CharacterModel",
    "CharacterManager",
    "CharacterTraitModel",
    "CharacterTraitManager",
    "CharacterStatusEffectModel",
    "CharacterStatusEffectManager",
    "FactionModel",
    "FactionManager",
    "CharacterFactionModel",
    "CharacterFactionManager",
    "LocationModel",
    "LocationManager",
    "ItemModel",
    "ItemManager",
    "ItemInstanceModel",
    "ItemInstanceManager",
    "QuestModel",
    "QuestManager",
    "ObjectiveModel",
    "ObjectiveManager",
    "CharacterQuestModel",
    "CharacterQuestManager",
    "FactionQuestModel",
    "FactionQuestManager",
    "CharacterObjectiveModel",
    "CharacterObjectiveManager",
    "ALL_MODELS",
]
