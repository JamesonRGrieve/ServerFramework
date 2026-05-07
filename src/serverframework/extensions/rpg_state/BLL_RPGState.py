"""rpg_state — present-state models and managers for RPG campaigns.

Hard-depends on ``genealogy``. RPG **does not** define a separate
``Character`` table. Instead, ``rpg_state`` widens
``genealogy.PersonModel`` via ``@extension_model`` to add the
character-specific fields (``kind``, ``campaign_id``, ``user_id``); the
``persons`` table is the character table for RPG users. Per-character
joins (traits, status effects, quest progress) reference
``persons.id`` and live in the ``person_*`` join tables defined here.

``rpg_state`` also widens ``genealogy.RelationshipModel`` with Faction
endpoints (``faction_id`` and ``target_faction_id``) so that a single
relationship table holds Person↔Person, Person↔Faction, and
Faction↔Faction edges. The migration adds a CHECK constraint enforcing
endpoint XOR (exactly one endpoint per side: a Person or a Faction, not
both, not neither).

Model groups (declared in dependency order so ``.Reference`` mixins
resolve):

1. ``GameSystemModel`` — reference catalog ("DnD 5e", "Pathfinder 2e").
2. ``CampaignModel`` — root of campaign-scoped data; ``user_id`` is the GM.
3. Trait / StatusEffect templates — system-agnostic property catalog.
   The unified ``TraitModel`` carries a ``kind`` discriminator so the
   same calculation surface handles attributes, skills, spells, talents,
   feats, languages, abilities, resources (HP/mana/spell slots), and
   progression dimensions (level, experience, spent_experience).
4. RPG injection onto ``PersonModel`` — the persons table grows
   ``kind``, ``campaign_id``, ``user_id`` to serve as the RPG character
   table.
5. Per-character joins (``PersonTraitModel``,
   ``PersonStatusEffectModel``). Magnitude lives on the join: STR=16 is
   ``PersonTraitModel.value``; "+2 STR" granted by Bull's Strength is a
   ``StatusEffectTraitModel`` row with operation='additive', value=2.
6. Faction hierarchy. ``FactionModel`` uses ``parent_id`` (from
   ``ParentMixinModel``) to model squads-within-platoons / parties-as-
   sub-guilds without a separate Party entity. Faction membership is
   **not** modelled here — it's a ``RelationshipModel`` row with
   ``kind='member_of'`` and Person↔Faction endpoints.
7. Spatial model. ``LocationModel`` is recursive (``parent_id``) and
   may itself be the inside of a container item via
   ``container_item_instance_id``. Equipment is modelled as a Location
   bound to a person via ``associated_person_id``; equipping is a
   spatial move, no IsEquipped flag.
8. Quest / Objective templates separated from per-character /
   per-faction instances. Objectives carry an optional
   ``prerequisite_objective_id`` for branching / linear chains.
9. RPG injection onto ``RelationshipModel`` — adds ``faction_id`` and
   ``target_faction_id``; the migration adds the endpoint-XOR CHECK.

Ownership semantics on ``ItemInstanceModel``:
- ``owner_faction_id`` = de jure / "in principle" owner (the Guild owns
  this sword on the books).
- ``owner_person_id`` = assigned-to / responsible-party / who-would-
  notice-it-missing. **Not** "is currently equipped". Equipping is a
  spatial concern (an equipment-slot Location bound to a person via
  ``associated_person_id``); the bearer of a sword may differ from its
  owner the same way an employee may use, but not own, a company laptop.
- Either, both, or neither owner field may be set. Theft is the natural
  state of having ``owner_person_id`` differ from the bearer (the
  current ``location_id`` chain) without an intermediate
  ``TransactionLog``.

Membership & visibility
-----------------------
Membership is inferred:

- Campaign owner = ``CampaignModel.user_id`` (GM).
- A user is a player in a campaign iff they own at least one Person
  (with ``user_id`` set) carrying that ``campaign_id``.

Granular visibility (GM-secret NPCs, hidden quests, shared handouts) is
delegated to the optional ``acl_rbac`` extension. ``rpg_state`` does
**not** carry visibility columns.

Progression
-----------
Progression dimensions (level, experience, spent_experience,
milestones, dice pools) live in the unified Trait catalog as
``TraitModel(kind='progression')`` rows joined via ``PersonTraitModel``.
There is no ``level`` column on the persons table.
"""

from datetime import datetime
from typing import ClassVar, List, Optional, Type

from pydantic import Field

from serverframework.extensions.genealogy.BLL_Genealogy import (
    PersonModel,
    RelationshipModel,
)
from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import RouterMixin
from serverframework.lib.Pydantic2SQLAlchemy import extension_model
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
    # Conventional `kind` values (free-form so systems may extend):
    #   attribute  — STR, DEX, CHA, ...
    #   skill      — Stealth, Persuasion, Athletics, ...
    #   spell      — Fireball (a spell IS a Trait so CombatActionLog.trait_id
    #                reaches it directly).
    #   talent / feat / ability / language — durable learnings.
    #   resource   — HP, mana, spell_slot_1, stress, wounds, dice pools;
    #                PersonTrait.value carries current value.
    #   progression — level, experience, spent_experience, milestones,
    #                 advancement_points. By convention named 'level',
    #                 'experience', 'spent_experience'. The persons table
    #                 has NO `level` column on purpose.
    kind: Optional[str] = Field(
        None,
        description=(
            "attribute|skill|spell|talent|feat|ability|language|resource|"
            "progression|... (free-form)"
        ),
    )
    table_comment: ClassVar[str] = (
        "Unified property catalog: stats, skills, spells, talents, feats, "
        "resources, progression dimensions. campaign_id=NULL means a "
        "globally-shared template; set = campaign-scoped override. "
        "Conventional kinds: attribute|skill|spell|talent|feat|ability|"
        "language|resource|progression. Progression Traits are by "
        "convention named 'level', 'experience', 'spent_experience', etc."
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
        "Status effect templates (buffs, debuffs, conditions, damage). "
        "Per-trait modifiers live on StatusEffectTrait. By convention, "
        "HealthDamage and ManaExpenditure are StatusEffects whose "
        "StatusEffectTrait edges point at the relevant resource Trait "
        "with a negative additive value — so 'Bob takes 8 damage' = "
        "applying a Wound StatusEffect with StatusEffectTrait(value=-8) "
        "against the HealthMax Trait."
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
        description=(
            "Modifier magnitude; signed (e.g. +2 for buff, -8 for damage, "
            "x1.5 for multiplicative)."
        ),
    )
    qualifier: Optional[str] = Field(
        None,
        description=(
            "Conditional context. NULL = always applies; non-NULL = "
            "conditional, e.g. 'vs orcs', 'while prone', 'in moonlight'. "
            "UI surfaces non-NULL qualifiers as toggles."
        ),
    )
    table_comment: ClassVar[str] = (
        "Per-trait modifiers contributed by a StatusEffect template. "
        "Bull's Strength → (status_effect=BullsStrength, trait=STR, "
        "operation='additive', value=2.0). Damage uses negative values "
        "against resource Traits. qualifier carries conditional context."
    )

    class Create(
        BaseModel,
        StatusEffectModel.Reference.ID.Optional,
        TraitModel.Reference.ID.Optional,
    ):
        operation: Optional[str] = None
        value: Optional[float] = None
        qualifier: Optional[str] = None

    class Update(BaseModel):
        operation: Optional[str] = None
        value: Optional[float] = None
        qualifier: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        StatusEffectModel.Reference.ID.Search,
        TraitModel.Reference.ID.Search,
    ):
        operation: Optional[StringSearchModel] = None
        value: Optional[NumericalSearchModel] = None
        qualifier: Optional[StringSearchModel] = None


class StatusEffectTraitManager(AbstractBLLManager, RouterMixin):
    _model = StatusEffectTraitModel


StatusEffectTraitModel.Manager = StatusEffectTraitManager


# ---------------------------------------------------------------------------
# 4. Inject character fields onto genealogy.PersonModel
# ---------------------------------------------------------------------------
#
# rpg_state does not own its own Character table. The persons table
# (owned by genealogy) is the character table for RPG users; this
# extension class adds the character-specific columns. Migration adds
# the matching ALTER TABLE on `persons`.


@extension_model(PersonModel)
class RPG_PersonModel(BaseModel):
    """RPG character-specific fields injected onto ``genealogy.PersonModel``.

    The persons table becomes the character table for RPG users. Adds:

    - ``kind``: pc|npc|monster|creature|vehicle|construct|... — free-form
      so each system can extend.
    - ``campaign_id``: FK to ``campaigns.id``; the campaign this actor
      belongs to. NULL = unattached / cross-campaign template.
    - ``user_id``: FK to ``users.id``; the controlling player when one
      exists. NULL = NPC.
    """

    kind: Optional[str] = Field(
        None, description="pc|npc|monster|creature|vehicle|construct|... (free-form)"
    )
    campaign_id: Optional[str] = Field(
        None, description="FK → campaigns.id; NULL = cross-campaign / template"
    )
    user_id: Optional[str] = Field(
        None,
        description="FK → users.id; controlling player. NULL = NPC.",
    )

    class Create(BaseModel):
        kind: Optional[str] = None
        campaign_id: Optional[str] = None
        user_id: Optional[str] = None

    class Update(BaseModel):
        kind: Optional[str] = None
        campaign_id: Optional[str] = None
        user_id: Optional[str] = None

    class Search(BaseModel):
        kind: Optional[StringSearchModel] = None
        campaign_id: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None


# ---------------------------------------------------------------------------
# 5. Per-person joins (RPG-domain join tables; references persons.id)
# ---------------------------------------------------------------------------


class PersonTraitModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,
    TraitModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonTraitManager"]] = None
    value: Optional[float] = Field(
        None,
        description=(
            "Base magnitude (e.g. STR=16; current HP for resource Traits). "
            "Active modifiers stack on top via PersonStatusEffect."
        ),
    )
    rank: Optional[int] = Field(
        None,
        description="Optional discrete rank (e.g. proficiency tier)",
    )
    notes: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "Per-person base trait values. Effective value = this.value + "
        "Σ active StatusEffectTrait modifiers via PersonStatusEffect "
        "(filtered by qualifier conditions when relevant)."
    )

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
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
        PersonModel.Reference.ID.Search,
        TraitModel.Reference.ID.Search,
    ):
        value: Optional[NumericalSearchModel] = None
        rank: Optional[NumericalSearchModel] = None


class PersonTraitManager(AbstractBLLManager, RouterMixin):
    _model = PersonTraitModel


PersonTraitModel.Manager = PersonTraitManager


class PersonStatusEffectModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,
    StatusEffectModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonStatusEffectManager"]] = None
    started_at: Optional[datetime] = Field(None)
    expires_at: Optional[datetime] = Field(
        None,
        description="Effect end; null = persists until manually removed",
    )
    # Source attribution; manual columns to avoid Reference collisions
    # (PersonModel.Reference is already used for the affected person;
    # ItemInstance.Reference would create a cycle).
    source_person_id: Optional[str] = Field(
        None, description="The caster / applier"
    )
    source_item_instance_id: Optional[str] = Field(
        None, description="Item consumed to apply the effect"
    )
    table_comment: ClassVar[str] = (
        "Active StatusEffect attachments per person. The effect's "
        "per-trait modifiers come from StatusEffectTrait. Multiple "
        "stacked effects (e.g. damage ticks) are multiple rows; the "
        "manager may consolidate expired rows on session close."
    )

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
        StatusEffectModel.Reference.ID.Optional,
    ):
        started_at: Optional[datetime] = None
        expires_at: Optional[datetime] = None
        source_person_id: Optional[str] = None
        source_item_instance_id: Optional[str] = None

    class Update(BaseModel):
        # Fully editable — GMs can retcon any aspect of an applied effect.
        started_at: Optional[datetime] = None
        expires_at: Optional[datetime] = None
        source_person_id: Optional[str] = None
        source_item_instance_id: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        PersonModel.Reference.ID.Search,
        StatusEffectModel.Reference.ID.Search,
    ):
        expires_at: Optional[DateSearchModel] = None


class PersonStatusEffectManager(AbstractBLLManager, RouterMixin):
    _model = PersonStatusEffectModel


PersonStatusEffectModel.Manager = PersonStatusEffectManager


# ---------------------------------------------------------------------------
# 6. Faction hierarchy
# ---------------------------------------------------------------------------
#
# Faction membership is **not** a join table — it's a Relationship row
# with kind='member_of', subject = person, target = faction (via the
# faction_id endpoint columns injected onto RelationshipModel below).


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
        "Hierarchical faction (parent_id self-FK fast-path for the "
        "primary tree — squads-within-platoons / parties-as-sub-guilds). "
        "Non-tree faction edges (allied, at-war, vassal) live in "
        "RelationshipModel(kind='affiliated_with', ...). Membership of "
        "persons in factions also lives in RelationshipModel "
        "(kind='member_of'); this extension does not own a "
        "person_factions table."
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
    # Cycle-resolved manually: a satchel's interior is a Location whose
    # container_item_instance_id points to the satchel ItemInstance, and
    # ItemInstance.location_id points back at containing Locations. The
    # ALTER TABLE that adds the FK lands after both tables exist.
    container_item_instance_id: Optional[str] = Field(
        None,
        description="Set when this Location represents the inside of a container item",
    )
    associated_person_id: Optional[str] = Field(
        None,
        description="Set when this Location is an equipment slot bound to a person",
    )
    kind: Optional[str] = Field(
        None,
        description="region|room|container|equipment_slot|inventory|... (free-form)",
    )
    table_comment: ClassVar[str] = (
        "Recursive spatial node. parent_id = parent location; "
        "container_item_instance_id = container that this is the inside "
        "of; associated_person_id = equipment-slot owner. Equipping is "
        "a spatial move."
    )

    class Create(BaseModel, CampaignModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        parent_id: Optional[str] = None
        container_item_instance_id: Optional[str] = None
        associated_person_id: Optional[str] = None
        kind: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        parent_id: Optional[str] = None
        container_item_instance_id: Optional[str] = None
        associated_person_id: Optional[str] = None
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
    # Two ownership axes, both nullable. Ownership is *assignment /
    # responsibility*, NOT equipped-state. owner_faction_id = de jure
    # (the Guild owns it on paper); owner_person_id = assigned-to /
    # who-would-notice-it-missing. Equipping is spatial (location_id
    # pointing at an equipment-slot Location bound to a person), not an
    # ownership concern. Theft = location chain disagrees with owner_*
    # without an intervening consensual TransactionLog.
    owner_person_id: Optional[str] = Field(
        None,
        description=(
            "Assigned-to / responsible person — 'who would notice it "
            "missing'. Not the equipping person (that's spatial)."
        ),
    )
    owner_faction_id: Optional[str] = Field(
        None,
        description="De jure / on-the-books owner faction.",
    )
    quantity: Optional[int] = Field(1, description="Stack count (≥1)")
    durability: Optional[float] = Field(None, description="System-defined HP/durability")
    table_comment: ClassVar[str] = (
        "Per-instance item row. location_id = current physical spot "
        "(equipment-slot Locations bound to a person represent the "
        "*equipped* relationship). owner_person_id = assignment / "
        "responsibility / who-would-notice-missing — distinct from the "
        "equipping person. owner_faction_id = de jure owner. Theft is "
        "the location chain disagreeing with owner_* without a "
        "TransactionLog."
    )

    class Create(
        BaseModel,
        ItemModel.Reference.ID.Optional,
        LocationModel.Reference.ID.Optional,
    ):
        owner_person_id: Optional[str] = None
        owner_faction_id: Optional[str] = None
        quantity: Optional[int] = None
        durability: Optional[float] = None

    class Update(BaseModel):
        location_id: Optional[str] = None
        owner_person_id: Optional[str] = None
        owner_faction_id: Optional[str] = None
        quantity: Optional[int] = None
        durability: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        ItemModel.Reference.ID.Search,
        LocationModel.Reference.ID.Search,
    ):
        owner_person_id: Optional[StringSearchModel] = None
        owner_faction_id: Optional[StringSearchModel] = None


class ItemInstanceManager(AbstractBLLManager, RouterMixin):
    _model = ItemInstanceModel


ItemInstanceModel.Manager = ItemInstanceManager


# ---------------------------------------------------------------------------
# 8. Quests, Objectives, and per-person/per-faction progress
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


class PersonQuestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,
    QuestModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonQuestManager"]] = None
    status: Optional[str] = Field(
        None, description="available|active|completed|failed|abandoned"
    )
    accepted_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    table_comment: ClassVar[str] = "Per-person quest progress instance"

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
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
        PersonModel.Reference.ID.Search,
        QuestModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class PersonQuestManager(AbstractBLLManager, RouterMixin):
    _model = PersonQuestModel


PersonQuestModel.Manager = PersonQuestManager


class FactionQuestModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    FactionModel.Reference.Optional,
    QuestModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FactionQuestManager"]] = None
    status: Optional[str] = Field(None, description="See PersonQuestModel.status")
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


class PersonObjectiveModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,
    ObjectiveModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonObjectiveManager"]] = None
    status: Optional[str] = Field(None)
    progress: Optional[float] = Field(
        None, description="Numeric progress (e.g. 3 of 5 wolves slain)"
    )
    completed_at: Optional[datetime] = Field(None)
    table_comment: ClassVar[str] = "Per-person objective progress instance"

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
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
        PersonModel.Reference.ID.Search,
        ObjectiveModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None


class PersonObjectiveManager(AbstractBLLManager, RouterMixin):
    _model = PersonObjectiveModel


PersonObjectiveModel.Manager = PersonObjectiveManager


# ---------------------------------------------------------------------------
# 9. Inject Faction endpoints onto genealogy.RelationshipModel
# ---------------------------------------------------------------------------


@extension_model(RelationshipModel)
class RPG_RelationshipModel(BaseModel):
    """RPG Faction endpoints injected onto ``genealogy.RelationshipModel``.

    Genealogy ships ``person_id`` (subject) and ``target_person_id``
    (object). RPG widens the table with ``faction_id`` (subject) and
    ``target_faction_id`` (object). The migration adds the endpoint-XOR
    CHECK: exactly one of (``person_id``, ``faction_id``) per row, same
    for the target side.

    Examples (kind, discriminator) → endpoints:
        ('member_of', 'leader')      → person → faction
        ('affiliated_with', 'allied') → faction → faction
        ('social', 'rival')          → person → person (unchanged)
    """

    faction_id: Optional[str] = Field(
        None,
        description=(
            "Subject-side Faction endpoint. XOR with person_id "
            "(enforced by migration CHECK)."
        ),
    )
    target_faction_id: Optional[str] = Field(
        None,
        description=(
            "Object-side Faction endpoint. XOR with target_person_id "
            "(enforced by migration CHECK)."
        ),
    )

    class Create(BaseModel):
        faction_id: Optional[str] = None
        target_faction_id: Optional[str] = None

    class Update(BaseModel):
        faction_id: Optional[str] = None
        target_faction_id: Optional[str] = None

    class Search(BaseModel):
        faction_id: Optional[StringSearchModel] = None
        target_faction_id: Optional[StringSearchModel] = None


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    GameSystemModel,
    CampaignModel,
    TraitModel,
    StatusEffectModel,
    StatusEffectTraitModel,
    PersonTraitModel,
    PersonStatusEffectModel,
    FactionModel,
    LocationModel,
    ItemModel,
    ItemInstanceModel,
    QuestModel,
    ObjectiveModel,
    PersonQuestModel,
    FactionQuestModel,
    PersonObjectiveModel,
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
    "RPG_PersonModel",
    "PersonTraitModel",
    "PersonTraitManager",
    "PersonStatusEffectModel",
    "PersonStatusEffectManager",
    "FactionModel",
    "FactionManager",
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
    "PersonQuestModel",
    "PersonQuestManager",
    "FactionQuestModel",
    "FactionQuestManager",
    "PersonObjectiveModel",
    "PersonObjectiveManager",
    "RPG_RelationshipModel",
    "ALL_MODELS",
]
