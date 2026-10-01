# SPDX-License-Identifier: AGPL-3.0-or-later
"""genealogy — Person + Relationship graph.

PersonModel owns the actor identity (name, birth/death, biography).
RelationshipModel is a directed labelled edge whose default endpoints
are both Persons. Downstream extensions may inject additional endpoint
columns (e.g. ``faction_id`` / ``target_faction_id``) via
``@extension_model``; manager-layer XOR enforces "exactly one endpoint
per side" once those are injected.

An edge reads "subject (kind, discriminator) object", the discriminator
naming the subject's role: ``ancestry`` reads parent → child.

Routes on ``/v1/person``: ``/{id}/ancestors``, ``/{id}/descendants`` and
``/{id}/kinship/{other_id}`` walk the ancestry graph (``FamilyTree``);
``GET /gedcom`` exports the people the requester can see as GEDCOM 5.5.1
and ``POST /gedcom`` imports GEDCOM 5.5 to 7.0 (``GEDCOM``). Every read is
as the requester, so a tree never reaches past what they may see.
"""

from datetime import date, datetime, time
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.genealogy.FamilyTree import (
    ANCESTRY,
    FamilyTree,
    Kinship,
    ParentEdge,
)
from zephyrex.extensions.genealogy.GEDCOM import (
    ExportFamily,
    ExportPerson,
    GEDCOMError,
    dumps,
    parse,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.logic.AbstractLogicManager import (
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
    Manager: ClassVar[Type["PersonManager"]]
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


class RelativeEntry(ResponseModel):
    person_id: str
    generation: int = Field(
        ..., description="1 = parent (or child), 2 = grandparent, …"
    )


class RelativesResponse(ResponseModel):
    person_id: str
    relatives: List[RelativeEntry]


class KinshipResponse(ResponseModel):
    person_id: str
    other_id: str
    related: bool
    common_ancestors: List[str] = []
    up_from_person: Optional[int] = None
    up_from_other: Optional[int] = None
    degree: Optional[int] = Field(None, description="Civil-law degree of kinship")
    cousin: Optional[int] = Field(None, description="1 = first cousins; 0 = closer")
    removed: Optional[int] = Field(None, description="Generation gap")
    lineal: Optional[bool] = Field(None, description="One is the other's ancestor")


class GEDCOMImportRequest(ResponseModel):
    gedcom: str = Field(..., description="GEDCOM 5.5, 5.5.1, 5.5.5 or 7.0 text")


class GEDCOMImportResponse(ResponseModel):
    version: str
    people: Dict[str, str] = Field(
        ..., description="Each GEDCOM INDI xref and the person created for it"
    )
    relationships: int


def _roles(roles: Optional[str]) -> Optional[List[str]]:
    """``?roles=biological,adopted``: the parent roles a walk counts."""
    if roles is None:
        return None
    return [r.strip() for r in roles.split(",") if r.strip()]


def _as_datetime(value: Optional[date]) -> Optional[datetime]:
    return datetime.combine(value, time()) if value is not None else None


class PersonManager(AbstractBLLManager, RouterMixin):
    _model = PersonModel

    def _db(self, model: Any) -> Any:
        return model.DB(self.model_registry.DB.manager.Base)

    def _visible(self, model: Any, filters: Optional[List[Any]] = None) -> List[Any]:
        """Rows of ``model`` the requester may see."""
        db = self._db(model)
        return (
            db.list(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                filters=filters or [],
                return_type="dto",
                override_dto=model,
            )
            or []
        )

    def _require_person(self, person_id: str) -> None:
        found = self._db(PersonModel).get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=person_id,
            return_type="dto",
            override_dto=PersonModel,
        )
        if found is None:
            raise HTTPException(status_code=404, detail="Person not found")

    def family_tree(self, roles: Optional[List[str]] = None) -> FamilyTree:
        """The ancestry graph of the relationships the requester can see."""
        db = self._db(RelationshipModel)
        edges = [
            ParentEdge(r.person_id, r.target_person_id, r.discriminator)
            for r in self._visible(RelationshipModel, [db.kind == ANCESTRY])
            if r.person_id and r.target_person_id
        ]
        return FamilyTree(edges, roles)

    def _relatives(self, person_id: str, found: Dict[str, int]) -> RelativesResponse:
        return RelativesResponse(
            person_id=person_id,
            relatives=[
                RelativeEntry(person_id=p, generation=g)
                for p, g in sorted(found.items(), key=lambda item: (item[1], item[0]))
            ],
        )

    @custom_route(
        method="GET",
        path="/{person_id}/ancestors",
        output_model=RelativesResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="A person's ancestors, nearest generation first",
        expose_in=(ExposeIn.REST,),
    )
    def ancestors_route(
        self,
        person_id: str,
        generations: Optional[int] = None,
        roles: Optional[str] = None,
    ) -> RelativesResponse:
        """``?generations=`` limits how far back; ``?roles=biological`` counts
        only those parent roles."""
        self._require_person(person_id)
        tree = self.family_tree(_roles(roles))
        return self._relatives(person_id, tree.ancestors(person_id, generations))

    @custom_route(
        method="GET",
        path="/{person_id}/descendants",
        output_model=RelativesResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="A person's descendants, nearest generation first",
        expose_in=(ExposeIn.REST,),
    )
    def descendants_route(
        self,
        person_id: str,
        generations: Optional[int] = None,
        roles: Optional[str] = None,
    ) -> RelativesResponse:
        self._require_person(person_id)
        tree = self.family_tree(_roles(roles))
        return self._relatives(person_id, tree.descendants(person_id, generations))

    @custom_route(
        method="GET",
        path="/{person_id}/kinship/{other_id}",
        output_model=KinshipResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="How two people are related through their nearest common ancestors",
        expose_in=(ExposeIn.REST,),
    )
    def kinship_route(
        self, person_id: str, other_id: str, roles: Optional[str] = None
    ) -> KinshipResponse:
        """``?roles=biological`` for blood kinship."""
        self._require_person(person_id)
        self._require_person(other_id)
        kinship: Optional[Kinship] = self.family_tree(_roles(roles)).kinship(
            person_id, other_id
        )
        if kinship is None:
            return KinshipResponse(
                person_id=person_id, other_id=other_id, related=False
            )
        return KinshipResponse(
            person_id=person_id,
            other_id=other_id,
            related=True,
            common_ancestors=list(kinship.common_ancestors),
            up_from_person=kinship.up_from_a,
            up_from_other=kinship.up_from_b,
            degree=kinship.degree,
            cousin=kinship.cousin,
            removed=kinship.removed,
            lineal=kinship.lineal,
        )

    @custom_route(
        method="GET",
        path="/gedcom",
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="Export the people you can see as GEDCOM 5.5.1",
        expose_in=(ExposeIn.REST,),
        response_class=PlainTextResponse,
    )
    def export_gedcom_route(self) -> PlainTextResponse:
        people = self._visible(PersonModel)
        ids = {p.id for p in people}
        relationships = [
            r
            for r in self._visible(RelationshipModel)
            if r.person_id in ids and r.target_person_id in ids
        ]
        return PlainTextResponse(
            dumps(
                [
                    ExportPerson(
                        id=p.id,
                        name=p.name or "Unknown",
                        gender=p.gender,
                        birth_date=p.birth_date.date() if p.birth_date else None,
                        death_date=p.death_date.date() if p.death_date else None,
                    )
                    for p in people
                ],
                _families(relationships),
            ),
            media_type="text/plain; charset=utf-8",
        )

    @custom_route(
        method="POST",
        path="/gedcom",
        input_model=GEDCOMImportRequest,
        output_model=GEDCOMImportResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="Import people and their families from GEDCOM 5.5 to 7.0",
        expose_in=(ExposeIn.REST,),
    )
    def import_gedcom_route(self, body: GEDCOMImportRequest) -> GEDCOMImportResponse:
        try:
            file = parse(body.gedcom)
        except GEDCOMError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        people_db = self._db(PersonModel)
        created: Dict[str, str] = {}
        for xref, person in file.people.items():
            row = people_db.create(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                return_type="dto",
                override_dto=PersonModel,
                name=person.name,
                gender=person.gender,
                birth_date=_as_datetime(person.birth_date),
                death_date=_as_datetime(person.death_date),
                description="\n".join(person.notes) or None,
            )
            created[xref] = row.id
        relationships_db = self._db(RelationshipModel)
        count = 0

        def relate(subject: str, target: str, kind: str, discriminator: str) -> None:
            nonlocal count
            relationships_db.create(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                return_type="dto",
                override_dto=RelationshipModel,
                person_id=created[subject],
                target_person_id=created[target],
                kind=kind,
                discriminator=discriminator,
            )
            count += 1

        for family in file.families:
            partners = family.partners
            for i, first in enumerate(partners):
                for second in partners[i + 1 :]:
                    kind = "marriage" if family.married else "partnership"
                    relate(first, second, "partnership", kind)
                    relate(second, first, "partnership", kind)
            for child in family.children:
                for parent in partners:
                    relate(parent, child, ANCESTRY, file.child_role(child, family.xref))
        return GEDCOMImportResponse(
            version=file.version, people=created, relationships=count
        )


def _families(relationships: List[Any]) -> List[ExportFamily]:
    """GEDCOM families from relationships: a child's parents in one role
    form a family, in pairs (a GEDCOM family has at most two partners);
    partners with no children form one of their own."""
    parents: Dict[Tuple[str, str], List[str]] = {}
    partnered: Dict[frozenset, bool] = {}
    for r in relationships:
        if r.kind == ANCESTRY:
            role = r.discriminator or "biological"
            parents.setdefault((r.target_person_id, role), []).append(r.person_id)
        elif r.kind == "partnership" and r.person_id != r.target_person_id:
            pair = frozenset((r.person_id, r.target_person_id))
            partnered[pair] = (
                partnered.get(pair, False) or r.discriminator == "marriage"
            )
    grouped: Dict[Tuple[Tuple[str, ...], str], List[str]] = {}
    for (child, role), ids in parents.items():
        ids = sorted(set(ids))
        for start in range(0, len(ids), 2):
            key = (tuple(ids[start : start + 2]), role)
            grouped.setdefault(key, []).append(child)
    families = [
        ExportFamily(
            partners=partners,
            children=tuple(sorted(children)),
            married=partnered.get(frozenset(partners), False),
            role=role,
        )
        for (partners, role), children in sorted(grouped.items())
    ]
    with_children = {frozenset(f.partners) for f in families}
    families += [
        ExportFamily(partners=tuple(sorted(pair)), married=married)
        for pair, married in sorted(partnered.items(), key=lambda item: sorted(item[0]))
        if pair not in with_children
    ]
    return families


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
    Manager: ClassVar[Type["RelationshipManager"]]

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
