"""genealogy — Person + Relationship graph.

PersonModel owns the actor identity (name, birth/death, biography).
RelationshipModel is a directed labelled edge whose default endpoints
are both Persons. Downstream extensions may inject additional endpoint
columns (e.g. ``faction_id`` / ``target_faction_id``) via
``@extension_model``; manager-layer XOR enforces "exactly one endpoint
per side" once those are injected.
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
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Person
# ---------------------------------------------------------------------------


class PersonModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    NameMixinModel.Optional,
    DescriptionMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PersonManager"]] = None
    birth_date: Optional[datetime] = Field(None, description="Date of birth")
    death_date: Optional[datetime] = Field(
        None,
        description="Date of death; NULL = living or unknown",
    )
    gender: Optional[str] = Field(
        None,
        description="Free-form gender label",
    )
    table_comment: ClassVar[str] = (
        "An actor identity. Used standalone for genealogy and as the "
        "humanoid subject of RPG Characters via Character.person_id. "
        "death_date NULL means living or unknown — privacy heuristics "
        "for living-person redaction live in the manager layer."
    )

    class Create(BaseModel):
        name: str = Field(...)
        description: Optional[str] = None
        birth_date: Optional[datetime] = None
        death_date: Optional[datetime] = None
        gender: Optional[str] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        birth_date: Optional[datetime] = None
        death_date: Optional[datetime] = None
        gender: Optional[str] = None

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None
        birth_date: Optional[DateSearchModel] = None
        death_date: Optional[DateSearchModel] = None
        gender: Optional[StringSearchModel] = None


class PersonManager(AbstractBLLManager, RouterMixin):
    _model = PersonModel


PersonModel.Manager = PersonManager


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------


class RelationshipModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,  # → person_id (subject side default)
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RelationshipManager"]] = None

    # Object-side default endpoint. Manual column to avoid Reference
    # collision with the subject-side person_id (the metaclass keys
    # field-name generation off the target model name).
    target_person_id: Optional[str] = Field(
        None,
        description="Object-side Person endpoint (default).",
    )

    # The relationship label. Free-form so downstream extensions and
    # standalone-genealogy users may introduce their own taxonomies.
    # Conventional values:
    #   ancestry|partnership|sibling_of           (genealogy core)
    #   member_of|affiliated_with                  (rpg_state when injected)
    #   social|obligation                          (rpg_state when injected)
    kind: Optional[str] = Field(
        None,
        description=(
            "Relationship kind. Genealogy core: ancestry|partnership|"
            "sibling_of. Downstream extensions add their own labels."
        ),
    )
    discriminator: Optional[str] = Field(
        None,
        description=(
            "Subkind / refinement of the relationship: "
            "biological|adopted|step|foster|legal_guardian for ancestry; "
            "marriage|engagement|partnership|liaison for partnership; "
            "leader|member|treasurer for member_of; etc."
        ),
    )
    intensity: Optional[float] = Field(
        None,
        description=(
            "Signed magnitude. -1.0 nemesis ↔ +1.0 closest; reused for "
            "reputation, regard, partnership closeness, etc."
        ),
    )
    qualifier: Optional[str] = Field(
        None,
        description=(
            "Conditional context ('since the war', 'while she lived'). "
            "Distinct from discriminator."
        ),
    )
    valid_from: Optional[datetime] = Field(
        None,
        description="Edge start; NULL = from-the-dawn-of-time.",
    )
    valid_to: Optional[datetime] = Field(
        None,
        description="Edge end; NULL = still in effect.",
    )
    notes: Optional[str] = Field(None, description="Free-form notes")

    table_comment: ClassVar[str] = (
        "Directed labelled edge. Default endpoints are Persons "
        "(person_id, target_person_id). Downstream extensions may "
        "inject additional endpoint columns via @extension_model and "
        "are responsible for ALTER TABLE in their migration plus a "
        "CHECK constraint enforcing endpoint XOR (exactly one endpoint "
        "per side). Symmetric semantics achieved by inserting two rows "
        "or by query convention; no is_symmetric flag."
    )

    class Create(BaseModel, PersonModel.Reference.ID.Optional):
        target_person_id: Optional[str] = None
        kind: Optional[str] = None
        discriminator: Optional[str] = None
        intensity: Optional[float] = None
        qualifier: Optional[str] = None
        valid_from: Optional[datetime] = None
        valid_to: Optional[datetime] = None
        notes: Optional[str] = None

    class Update(BaseModel):
        kind: Optional[str] = None
        discriminator: Optional[str] = None
        intensity: Optional[float] = None
        qualifier: Optional[str] = None
        valid_from: Optional[datetime] = None
        valid_to: Optional[datetime] = None
        notes: Optional[str] = None

    class Search(ApplicationModel.Search, PersonModel.Reference.ID.Search):
        target_person_id: Optional[StringSearchModel] = None
        kind: Optional[StringSearchModel] = None
        discriminator: Optional[StringSearchModel] = None
        intensity: Optional[NumericalSearchModel] = None
        qualifier: Optional[StringSearchModel] = None
        valid_from: Optional[DateSearchModel] = None
        valid_to: Optional[DateSearchModel] = None


class RelationshipManager(AbstractBLLManager, RouterMixin):
    _model = RelationshipModel


RelationshipModel.Manager = RelationshipManager


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    PersonModel,
    RelationshipModel,
]


__all__ = [
    "PersonModel",
    "PersonManager",
    "RelationshipModel",
    "RelationshipManager",
    "ALL_MODELS",
]
