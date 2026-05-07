"""rpg_state — present-state models and managers for RPG campaigns.

Hard-depends on ``genealogy``. RPG **does not** define a separate
``Character`` table. Instead, ``rpg_state`` widens
``genealogy.PersonModel`` via ``@extension_model`` to add the
character-specific fields (``kind``, ``campaign_id``, ``user_id``); the
``persons`` table is the character table for RPG users. Per-character
joins (traits, status-effect applications, hook progress) reference
``persons.id`` and live in the ``person_*`` join tables defined here.

``rpg_state`` also widens ``genealogy.RelationshipModel`` with Faction
endpoints (``faction_id`` and ``target_faction_id``) so that a single
relationship table holds Person↔Person, Person↔Faction, and
Faction↔Faction edges. The migration adds a CHECK constraint enforcing
endpoint XOR (exactly one endpoint per side: a Person or a Faction, not
both, not neither).

Unified Trait model
-------------------
There is **no** separate StatusEffect table. A status effect is a Trait
with ``kind='status_effect'``. Bull's Strength, Wound, Bleed, Marked,
Slowed are all Traits. Templates and applied instances share one model:

- ``TraitModel`` is the catalog (attributes, skills, spells, talents,
  feats, abilities, languages, resources, progression dimensions, and
  status effects).
- ``DerivativeTraitModel`` (replaces ``StatusEffectTrait``) is the
  edge: how one Trait contributes to another. Bull's Strength → STR
  (op='additive', value=2.0). Initiative ← DEX (op='additive',
  value=1.0) plus a constant (op='additive', value=5.0,
  source_trait_id=NULL).
- ``PersonTraitModel`` is the join: this person carries this trait,
  with the magnitude on ``value`` (and, for transient applications,
  ``started_at`` / ``expires_at`` / ``source_*``). Multiple rows per
  ``(person, trait)`` are allowed (e.g. several stacks of Wound).

Resolution pipeline (deterministic, indexable):

1. ``override`` — last write wins by ``order_index``.
2. ``additive`` — sum the contributions, in ``order_index`` order.
3. ``multiplicative`` — apply each multiplier, in ``order_index`` order.
4. ``clamp`` — apply min/max bounds (sign of ``value`` selects min vs
   max).

Within each phase, ``stacking_group`` controls de-duplication: rows
sharing a non-NULL group keep only the max-abs contribution; rows with
``stacking_group=NULL`` always sum.

Contribution magnitude:
- ``source_trait_id`` set → ``effective = value × (source PersonTrait.value or 1.0)``.
- ``source_trait_id`` NULL → ``effective = value`` (a constant).

``qualifier`` is conditional context ("vs orcs", "while prone"). NULL =
always applies; non-NULL = the UI surfaces it as a toggle and the
calculation only includes the row when the condition is on.

Hook DAG (replaces Quest+Objective)
-----------------------------------
There is **no** separate Quest / Objective hierarchy. Both fold into
``HookModel``: a campaign-scoped narrative element with a free-form
``kind`` discriminator (major_quest|side_quest|objective|plot_thread|
mystery|rumor|obligation|foreshadowing|background|...).

Dependencies between Hooks live in ``HookDependencyModel(parent_hook_id,
child_hook_id, reason)``. The presence of a row IS the dependency; the
``reason`` string explains why ("requires the dragon to be slain
first", "alternative path via diplomacy", "blocked while the curse is
active"). No dependency-kind enum.

Per-actor progress lives in ``PersonHookModel`` and ``FactionHookModel``
with status / accepted_at / completed_at / progress (numeric for
incremental hooks; NULL for atomic).

Model groups (declared in dependency order so ``.Reference`` mixins
resolve):

1. ``GameSystemModel`` — reference catalog ("DnD 5e", "Pathfinder 2e").
2. ``CampaignModel`` — root of campaign-scoped data; ``user_id`` is the GM.
3. ``TraitModel`` — unified property catalog including status effects.
4. ``DerivativeTraitModel`` — edges defining how Traits derive from each other.
5. RPG injection onto ``PersonModel`` (kind, campaign_id, user_id).
6. ``PersonTraitModel`` — per-person trait values and applied effects.
7. ``FactionModel`` — hierarchical faction (parent_id self-FK).
8. ``LocationModel`` / ``ItemModel`` / ``ItemInstanceModel`` —
   spatial / inventory.
9. ``HookModel`` / ``HookDependencyModel`` /
   ``PersonHookModel`` / ``FactionHookModel`` — narrative DAG.
10. RPG injection onto ``RelationshipModel`` (faction_id,
    target_faction_id; endpoint-XOR CHECK in the migration).

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
from typing import ClassVar, List, Literal, Optional, Type

from pydantic import Field

from serverframework.extensions.genealogy.BLL_Genealogy import (
    PersonModel,
    RelationshipModel,
)
from serverframework.lib.CycleGuard import (
    CycleGuardError,
    would_create_dag_cycle,
    would_create_tree_cycle,
)
from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import RouterMixin
from serverframework.lib.Pydantic2SQLAlchemy import extension_model
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    DescriptionMixinModel,
    HookContext,
    HookTiming,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)
from serverframework.logic.BLL_Auth import UserModel


# ---------------------------------------------------------------------------
# Closed enums
# ---------------------------------------------------------------------------

DerivativeOperation = Literal["override", "additive", "multiplicative", "clamp"]
"""Closed set of derivation operations for ``DerivativeTraitModel``.

Doubles as the resolution-pipeline phase: override → additive →
multiplicative → clamp. Within each phase, ``order_index`` orders ties.
"""

HookKind = Literal[
    "major_quest",
    "side_quest",
    "objective",
    "plot_thread",
    "mystery",
    "rumor",
    "obligation",
    "foreshadowing",
    "hint",
    "background",
]
"""Closed set of Hook narrative kinds for ``HookModel``."""


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
# 3. Trait — unified property catalog (incl. status effects)
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
    #                 'experience', 'spent_experience'.
    #   status_effect — Bull's Strength, Wound, Bleed, Marked, Slowed.
    #                   Applied to a person via PersonTrait with timing
    #                   (started_at / expires_at) and source attribution.
    kind: Optional[str] = Field(
        None,
        description=(
            "attribute|skill|spell|talent|feat|ability|language|resource|"
            "progression|status_effect|... (free-form)"
        ),
    )
    default_duration_seconds: Optional[int] = Field(
        None,
        description=(
            "Default lifetime when applied. Only meaningful when "
            "kind='status_effect'; NULL = persists until manually removed."
        ),
    )
    table_comment: ClassVar[str] = (
        "Unified property catalog: stats, skills, spells, talents, feats, "
        "abilities, languages, resources, progression, and status effects. "
        "campaign_id=NULL = globally-shared template; set = "
        "campaign-scoped override. default_duration_seconds is meaningful "
        "only for kind='status_effect'."
    )

    class Create(
        BaseModel,
        GameSystemModel.Reference.ID.Optional,
        CampaignModel.Reference.ID.Optional,
    ):
        name: str = Field(...)
        description: Optional[str] = None
        kind: Optional[str] = None
        default_duration_seconds: Optional[int] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        kind: Optional[str] = None
        default_duration_seconds: Optional[int] = None

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


# ---------------------------------------------------------------------------
# 4. DerivativeTrait — edges defining how Traits derive from each other
# ---------------------------------------------------------------------------


class DerivativeTraitModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["DerivativeTraitManager"]] = None
    # Both endpoints reference traits.id; manual columns since
    # TraitModel.Reference would generate one trait_id and collide on
    # the second.
    source_trait_id: Optional[str] = Field(
        None,
        description=(
            "FK → traits.id. When set, contribution scales with the "
            "source's PersonTrait.value (or 1.0 if null). When NULL, "
            "the row contributes a constant equal to `value`."
        ),
    )
    target_trait_id: Optional[str] = Field(
        None,
        description="FK → traits.id. The trait being modified.",
    )
    operation: Optional[DerivativeOperation] = Field(
        None,
        description=(
            "Closed enum: override|additive|multiplicative|clamp. Also "
            "identifies the resolution phase: override → additive → "
            "multiplicative → clamp. Within each phase, order_index "
            "sequences ties. Validated by Pydantic Literal and enforced "
            "in DDL by a CHECK constraint in the migration."
        ),
    )
    value: Optional[float] = Field(
        None,
        description=(
            "Magnitude. Signed. For additive: the contribution; for "
            "multiplicative: the multiplier; for override: the new value; "
            "for clamp: positive = upper bound, negative = lower bound."
        ),
    )
    order_index: Optional[int] = Field(
        None,
        description="Tie-breaker within phase (lower runs first; default 0).",
    )
    stacking_group: Optional[str] = Field(
        None,
        description=(
            "NULL = always stacks (sum). Non-NULL = within group, only "
            "the max-abs contribution wins. Used to model 'doesn't stack "
            "with itself' / typed-bonus stacking rules."
        ),
    )
    qualifier: Optional[str] = Field(
        None,
        description=(
            "Conditional context. NULL = always applies; non-NULL = "
            "conditional ('vs orcs', 'while prone', 'in moonlight'). UI "
            "surfaces non-NULL qualifiers as toggles."
        ),
    )
    table_comment: ClassVar[str] = (
        "Edges defining how one Trait contributes to another. Replaces "
        "the prior StatusEffectTrait; subsumes both arithmetic "
        "derivations (Initiative ← DEX + 5) and applied-effect "
        "modifiers (Bull's Strength → STR +2). Resolution pipeline: "
        "override → additive → multiplicative → clamp; order_index "
        "sequences within phase; stacking_group de-duplicates by "
        "max-abs. source_trait_id NULL = constant contribution."
    )

    class Create(BaseModel):
        source_trait_id: Optional[str] = None
        target_trait_id: Optional[str] = None
        operation: Optional[DerivativeOperation] = None
        value: Optional[float] = None
        order_index: Optional[int] = None
        stacking_group: Optional[str] = None
        qualifier: Optional[str] = None

    class Update(BaseModel):
        operation: Optional[DerivativeOperation] = None
        value: Optional[float] = None
        order_index: Optional[int] = None
        stacking_group: Optional[str] = None
        qualifier: Optional[str] = None

    class Search(ApplicationModel.Search):
        source_trait_id: Optional[StringSearchModel] = None
        target_trait_id: Optional[StringSearchModel] = None
        operation: Optional[StringSearchModel] = None
        value: Optional[NumericalSearchModel] = None
        stacking_group: Optional[StringSearchModel] = None
        qualifier: Optional[StringSearchModel] = None


class DerivativeTraitManager(AbstractBLLManager, RouterMixin):
    _model = DerivativeTraitModel


DerivativeTraitModel.Manager = DerivativeTraitManager


# ---------------------------------------------------------------------------
# 5. Inject character fields onto genealogy.PersonModel
# ---------------------------------------------------------------------------


@extension_model(PersonModel)
class RPG_PersonModel(BaseModel):
    """RPG character-specific fields injected onto ``genealogy.PersonModel``.

    The persons table becomes the character table for RPG users. Adds:

    - ``kind``: pc|npc|monster|creature|vehicle|construct|... — free-form.
    - ``campaign_id``: FK to ``campaigns.id``; NULL = unattached / template.
    - ``user_id``: FK to ``users.id``; NULL = NPC.
    - ``location_id``: FK to ``locations.id``; the person's current
      scene/region location. Distinct from any *body-part*
      sub-Locations associated with the person (see ``LocationModel``
      for the body-parts-as-locations convention).
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
    location_id: Optional[str] = Field(
        None,
        description=(
            "FK → locations.id. Current scene/region location. "
            "Distinct from body-part sub-Locations (those are owned via "
            "Location.associated_person_id, not Person.location_id)."
        ),
    )

    class Create(BaseModel):
        kind: Optional[str] = None
        campaign_id: Optional[str] = None
        user_id: Optional[str] = None
        location_id: Optional[str] = None

    class Update(BaseModel):
        kind: Optional[str] = None
        campaign_id: Optional[str] = None
        user_id: Optional[str] = None
        location_id: Optional[str] = None

    class Search(BaseModel):
        kind: Optional[StringSearchModel] = None
        campaign_id: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        location_id: Optional[StringSearchModel] = None


# ---------------------------------------------------------------------------
# 6. PersonTrait — per-person trait values + applied-effect attachments
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
            "Per-instance magnitude. For attribute traits: STR=16. For "
            "resource traits: current HP. For status-effect applications: "
            "the magnitude carried by this stack (e.g. -8 for a Wound). "
            "Effective contribution to derived traits = "
            "DerivativeTrait.value × (this value or 1.0)."
        ),
    )
    rank: Optional[int] = Field(
        None,
        description="Optional discrete rank (e.g. proficiency tier)",
    )
    started_at: Optional[datetime] = Field(
        None,
        description=(
            "Application start. NULL = persistent (durable trait). "
            "Set for transient applications (status effects, buffs, "
            "wounds)."
        ),
    )
    expires_at: Optional[datetime] = Field(
        None,
        description=(
            "Application end. NULL = persists until manually removed. "
            "For active filtering, use ``expires_at IS NULL OR "
            "expires_at > now()``."
        ),
    )
    # Source attribution for applied effects. Manual columns since
    # PersonModel.Reference is already used for the affected person and
    # ItemInstance.Reference would create a cycle.
    source_person_id: Optional[str] = Field(
        None, description="The caster / applier (FK → persons.id)"
    )
    source_item_instance_id: Optional[str] = Field(
        None,
        description="Item consumed to apply the effect (FK → item_instances.id)",
    )
    # Target localisation: where on the person (or, more generally,
    # which Location) does this trait/effect apply? Wounds use this to
    # target a body part sub-Location ("Bob's left arm"); buffs that
    # are full-body leave it NULL.
    target_location_id: Optional[str] = Field(
        None,
        description=(
            "FK → locations.id. Where this trait/effect is localised. "
            "For Wounds and similar damage applications: a body-part "
            "sub-Location. NULL = whole-body / location-agnostic."
        ),
    )
    notes: Optional[str] = Field(None)
    table_comment: ClassVar[str] = (
        "Per-person trait instances. Multiple rows per (person, trait) "
        "are allowed (e.g. several Wound stacks at different body "
        "parts). Durable traits leave started_at/expires_at NULL; "
        "transient applications fill them. target_location_id "
        "localises the application (e.g. damage to a specific limb). "
        "The full effective value of a derived target trait is computed "
        "by walking DerivativeTrait edges into it from active source "
        "Traits the person carries."
    )

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
        TraitModel.Reference.ID.Optional,
    ):
        value: Optional[float] = None
        rank: Optional[int] = None
        started_at: Optional[datetime] = None
        expires_at: Optional[datetime] = None
        source_person_id: Optional[str] = None
        source_item_instance_id: Optional[str] = None
        target_location_id: Optional[str] = None
        notes: Optional[str] = None

    class Update(BaseModel):
        # Fully editable so GMs can retcon any aspect.
        value: Optional[float] = None
        rank: Optional[int] = None
        started_at: Optional[datetime] = None
        expires_at: Optional[datetime] = None
        source_person_id: Optional[str] = None
        source_item_instance_id: Optional[str] = None
        target_location_id: Optional[str] = None
        notes: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        PersonModel.Reference.ID.Search,
        TraitModel.Reference.ID.Search,
    ):
        value: Optional[NumericalSearchModel] = None
        rank: Optional[NumericalSearchModel] = None
        started_at: Optional[DateSearchModel] = None
        expires_at: Optional[DateSearchModel] = None
        target_location_id: Optional[StringSearchModel] = None


class PersonTraitManager(AbstractBLLManager, RouterMixin):
    _model = PersonTraitModel


PersonTraitModel.Manager = PersonTraitManager


# ---------------------------------------------------------------------------
# 7. Faction hierarchy
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
# 8. Spatial: Location / Item / ItemInstance
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
        description=(
            "region|room|container|equipment_slot|inventory|body|"
            "body_part|organ|... (free-form)"
        ),
    )
    table_comment: ClassVar[str] = (
        "Recursive spatial node. parent_id = parent location; "
        "container_item_instance_id = container that this is the inside "
        "of; associated_person_id = the person this Location is bound "
        "to. Equipping is a spatial move (item moves into an "
        "equipment-slot Location whose associated_person_id is the "
        "wearer). Body parts are also Locations: a person's anatomy "
        "lives as a tree of Locations (kind='body'/'body_part'/'organ') "
        "rooted at a body Location with associated_person_id=person. "
        "PersonTrait.target_location_id points at body-part Locations "
        "to localise damage / effect application."
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
# 9. Hooks — narrative DAG (replaces Quest+Objective)
# ---------------------------------------------------------------------------


class HookModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    CampaignModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HookManager"]] = None
    kind: Optional[HookKind] = Field(
        None,
        description=(
            "Closed enum: major_quest|side_quest|objective|plot_thread|"
            "mystery|rumor|obligation|foreshadowing|hint|background. "
            "Validated by Pydantic Literal and enforced in DDL by a "
            "CHECK constraint in the migration."
        ),
    )
    giver_faction_id: Optional[str] = Field(
        None,
        description="Faction that issued the hook (FK → factions.id).",
    )
    table_comment: ClassVar[str] = (
        "Narrative element. Replaces Quest+Objective with a single "
        "table; hierarchy and prerequisite relationships move to "
        "HookDependency. Per-actor progress lives in PersonHook / "
        "FactionHook."
    )

    class Create(BaseModel, CampaignModel.Reference.ID.Optional):
        name: str = Field(...)
        description: Optional[str] = None
        kind: Optional[HookKind] = None
        giver_faction_id: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        kind: Optional[HookKind] = None
        giver_faction_id: Optional[str] = None

    class Search(ApplicationModel.Search, CampaignModel.Reference.ID.Search):
        name: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None


class HookManager(AbstractBLLManager, RouterMixin):
    _model = HookModel


HookModel.Manager = HookManager


class HookDependencyModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["HookDependencyManager"]] = None
    # Both endpoints reference hooks.id; manual columns since
    # HookModel.Reference would generate one hook_id and collide on the
    # second.
    parent_hook_id: Optional[str] = Field(
        None,
        description="The prerequisite Hook (FK → hooks.id).",
    )
    child_hook_id: Optional[str] = Field(
        None,
        description="The dependent Hook (FK → hooks.id).",
    )
    reason: Optional[str] = Field(
        None,
        description=(
            "Free-form explanation of why the dependency exists "
            "('requires the dragon to be slain first', "
            "'alternative path via diplomacy', "
            "'blocked while the curse is active'). The presence of the "
            "row IS the dependency; reason explains it."
        ),
    )
    table_comment: ClassVar[str] = (
        "DAG edge between two Hooks. Presence of a row IS the "
        "dependency; the reason string explains it. No dependency-kind "
        "enum: complete/fail/unlock/block/alternative-path semantics "
        "are described in the reason text. Manager-layer cycle guard "
        "rejects edits that would close a cycle."
    )

    class Create(BaseModel):
        parent_hook_id: Optional[str] = None
        child_hook_id: Optional[str] = None
        reason: Optional[str] = None

    class Update(BaseModel):
        reason: Optional[str] = None

    class Search(ApplicationModel.Search):
        parent_hook_id: Optional[StringSearchModel] = None
        child_hook_id: Optional[StringSearchModel] = None
        reason: Optional[StringSearchModel] = None


class HookDependencyManager(AbstractBLLManager, RouterMixin):
    _model = HookDependencyModel


HookDependencyModel.Manager = HookDependencyManager


class PersonHookModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,
    HookModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonHookManager"]] = None
    status: Optional[str] = Field(
        None,
        description=(
            "available|active|completed|failed|abandoned|superseded|... "
            "(free-form; non-quest Hooks may use 'unresolved' / "
            "'resolved' instead)"
        ),
    )
    accepted_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    progress: Optional[float] = Field(
        None,
        description=(
            "Numeric progress for incremental hooks (3 of 5 wolves "
            "slain). NULL for atomic hooks."
        ),
    )
    table_comment: ClassVar[str] = (
        "Per-person hook progress. Replaces both PersonQuest and "
        "PersonObjective."
    )

    class Create(
        BaseModel,
        PersonModel.Reference.ID.Optional,
        HookModel.Reference.ID.Optional,
    ):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None
        progress: Optional[float] = None

    class Update(BaseModel):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None
        progress: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        PersonModel.Reference.ID.Search,
        HookModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None
        progress: Optional[NumericalSearchModel] = None


class PersonHookManager(AbstractBLLManager, RouterMixin):
    _model = PersonHookModel


PersonHookModel.Manager = PersonHookManager


class FactionHookModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    FactionModel.Reference.Optional,
    HookModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FactionHookManager"]] = None
    status: Optional[str] = Field(None, description="See PersonHookModel.status")
    accepted_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    progress: Optional[float] = Field(None)
    table_comment: ClassVar[str] = "Per-faction hook progress."

    class Create(
        BaseModel,
        FactionModel.Reference.ID.Optional,
        HookModel.Reference.ID.Optional,
    ):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None
        progress: Optional[float] = None

    class Update(BaseModel):
        status: Optional[str] = None
        accepted_at: Optional[datetime] = None
        completed_at: Optional[datetime] = None
        progress: Optional[float] = None

    class Search(
        ApplicationModel.Search,
        FactionModel.Reference.ID.Search,
        HookModel.Reference.ID.Search,
    ):
        status: Optional[StringSearchModel] = None
        progress: Optional[NumericalSearchModel] = None


class FactionHookManager(AbstractBLLManager, RouterMixin):
    _model = FactionHookModel


FactionHookModel.Manager = FactionHookManager


# ---------------------------------------------------------------------------
# 10. Inject Faction endpoints onto genealogy.RelationshipModel
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
# Cycle guards — Faction.parent_id, Location.parent_id, HookDependency
# ---------------------------------------------------------------------------
#
# Manager-layer integrity for invariants the DB cannot express. These
# hooks reject mutations that would close a cycle in a self-referential
# parent chain (Faction, Location) or in the Hook DAG.


def _payload_value(context: HookContext, key: str) -> Optional[str]:
    """Pull ``key`` off the most likely create/update payload arg.

    Hooks see the manager's call args by position or kwarg; the payload
    is conventionally the first positional or a ``data`` / ``payload``
    kwarg. We try the common shapes and return None if nothing
    matches.
    """
    for kwarg in ("data", "payload", "create_data", "update_data"):
        candidate = context.kwarg(kwarg, default=None)
        if candidate is not None and hasattr(candidate, key):
            return getattr(candidate, key, None)
    arg0 = context.arg(0, default=None)
    if arg0 is not None and hasattr(arg0, key):
        return getattr(arg0, key, None)
    return None


def _record_id(context: HookContext) -> Optional[str]:
    """Pull the record id off an update call (kwarg ``id`` or second arg)."""
    rid = context.kwarg("id", default=None)
    if rid is not None:
        return rid
    return context.arg(1, default=None)


def _faction_parent_lookup(manager: AbstractBLLManager):
    def _get(faction_id: str) -> Optional[str]:
        try:
            row = manager.get(id=faction_id)
        except Exception:
            return None
        return getattr(row, "parent_id", None) if row else None

    return _get


def _location_parent_lookup(manager: AbstractBLLManager):
    def _get(location_id: str) -> Optional[str]:
        try:
            row = manager.get(id=location_id)
        except Exception:
            return None
        return getattr(row, "parent_id", None) if row else None

    return _get


def _hook_parents_lookup(manager: AbstractBLLManager):
    """Return a function that yields the parent_hook_ids of a given child."""

    def _get(child_hook_id: str):
        try:
            edges = manager.list(child_hook_id=child_hook_id) or []
        except Exception:
            return ()
        return [
            getattr(edge, "parent_hook_id", None)
            for edge in edges
            if getattr(edge, "parent_hook_id", None)
        ]

    return _get


@hook_bll(FactionManager, timing=HookTiming.BEFORE, priority=10)
def _faction_no_cycle(context: HookContext) -> None:
    if context.method_name not in ("update", "create"):
        return
    candidate = _payload_value(context, "parent_id")
    if not candidate:
        return
    if context.method_name == "create":
        # New row has no descendants; only self-parent could be a cycle.
        return
    rid = _record_id(context)
    if not rid:
        return
    if would_create_tree_cycle(
        rid, candidate, _faction_parent_lookup(context.manager)
    ):
        raise CycleGuardError(
            f"Setting Faction({rid}).parent_id={candidate} would close a cycle."
        )


@hook_bll(LocationManager, timing=HookTiming.BEFORE, priority=10)
def _location_no_cycle(context: HookContext) -> None:
    if context.method_name not in ("update", "create"):
        return
    candidate = _payload_value(context, "parent_id")
    if not candidate:
        return
    if context.method_name == "create":
        return
    rid = _record_id(context)
    if not rid:
        return
    if would_create_tree_cycle(
        rid, candidate, _location_parent_lookup(context.manager)
    ):
        raise CycleGuardError(
            f"Setting Location({rid}).parent_id={candidate} would close a cycle."
        )


@hook_bll(HookDependencyManager, timing=HookTiming.BEFORE, priority=10)
def _hook_dependency_no_cycle(context: HookContext) -> None:
    if context.method_name not in ("create", "update"):
        return
    parent_id = _payload_value(context, "parent_hook_id")
    child_id = _payload_value(context, "child_hook_id")
    if not parent_id or not child_id:
        return
    if would_create_dag_cycle(
        parent_id, child_id, _hook_parents_lookup(context.manager)
    ):
        raise CycleGuardError(
            f"HookDependency parent={parent_id} → child={child_id} would close a cycle."
        )


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    GameSystemModel,
    CampaignModel,
    TraitModel,
    DerivativeTraitModel,
    PersonTraitModel,
    FactionModel,
    LocationModel,
    ItemModel,
    ItemInstanceModel,
    HookModel,
    HookDependencyModel,
    PersonHookModel,
    FactionHookModel,
]


__all__ = [
    "GameSystemModel",
    "GameSystemManager",
    "CampaignModel",
    "CampaignManager",
    "TraitModel",
    "TraitManager",
    "DerivativeTraitModel",
    "DerivativeTraitManager",
    "RPG_PersonModel",
    "PersonTraitModel",
    "PersonTraitManager",
    "FactionModel",
    "FactionManager",
    "LocationModel",
    "LocationManager",
    "ItemModel",
    "ItemManager",
    "ItemInstanceModel",
    "ItemInstanceManager",
    "HookModel",
    "HookManager",
    "HookDependencyModel",
    "HookDependencyManager",
    "PersonHookModel",
    "PersonHookManager",
    "FactionHookModel",
    "FactionHookManager",
    "RPG_RelationshipModel",
    "ALL_MODELS",
]
