# SPDX-License-Identifier: AGPL-3.0-or-later
"""genealogy — Person + Relationship graph.

PersonModel owns the actor identity (name, birth/death, biography).
RelationshipModel is a directed labelled edge whose default endpoints
are both Persons. Downstream extensions may inject additional endpoint
columns (e.g. ``faction_id`` / ``target_faction_id``) via
``@extension_model``; manager-layer XOR enforces "exactly one endpoint
per side" once those are injected.

An edge reads "subject (kind, discriminator) object", the discriminator
naming the subject's role: ``ancestry`` reads parent → child. The kinds
genealogy knows, and the discriminators each accepts, are in
``Relationships``; a relationship of one of them is checked on every write.

Routes on ``/v1/person``: ``/{id}/ancestors``, ``/{id}/descendants`` and
``/{id}/kinship/{other_id}`` walk the ancestry graph (``FamilyTree``);
``/{id}/connections``, ``/{id}/metamours`` and ``/{id}/network`` walk the
symmetric ties (partnership, friendship, …; ``SocialGraph``);
``GET /gedcom`` exports the people the requester can see as GEDCOM 5.5.1
and ``POST /gedcom`` imports GEDCOM 5.5 to 7.0 (``GEDCOM``). Every read is
as the requester, so a tree never reaches past what they may see.
"""

from datetime import date, datetime, time, timezone
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type

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
from zephyrex.extensions.genealogy.Relationships import (
    PARTNERSHIP,
    RelationshipError,
    check_relationship,
    describe_discriminators,
    directed_kinds,
    symmetric_kinds,
)
from zephyrex.extensions.genealogy.MergedTrees import (
    MergedGraph,
    Tree,
    TreePerson,
    merge,
)
from zephyrex.extensions.genealogy.SocialGraph import SocialGraph, Tie
from zephyrex.extensions.genealogy.GEDCOM import (
    ExportFamily,
    ExportPerson,
    GEDCOMError,
    dumps,
    parse,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.extensions.privacy.Erasure import register_eraser
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.logic.AbstractLogicManager.ownership import each_created
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

# How a person shows to strangers in a merged tree, least to most revealing.
Visibility = Literal["hidden", "anonymous", "named", "profile"]

# A relationship's signed magnitude: -1.0 nemesis to +1.0 closest.
INTENSITY_MIN = -1.0
INTENSITY_MAX = 1.0

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
    # Not ``user_id``: that names a record's owner, and the person this record
    # is about must not thereby see what its owner keeps about them.
    linked_user_id: Optional[str] = Field(
        None,
        description=(
            "The account this person is, when they have one; on the account's "
            "own record (created by it) this is its self person"
        ),
    )
    visibility: Optional[str] = Field(
        None,
        description=(
            "How this person shows in the trees of people who are strangers to them, "
            "when linked trees merge: hidden, anonymous (the default), named or profile. "
            "An account sets it on its self person; a ghost's recorder sets it on the ghost."
        ),
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
        linked_user_id: Optional[str] = None
        visibility: Optional[Visibility] = None

    class Update(BaseModel):
        name: Optional[str] = None
        description: Optional[str] = None
        birth_date: Optional[datetime] = None
        death_date: Optional[datetime] = None
        gender: Optional[str] = None
        linked_user_id: Optional[str] = None
        visibility: Optional[Visibility] = None

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None
        birth_date: Optional[DateSearchModel] = None
        death_date: Optional[DateSearchModel] = None
        gender: Optional[StringSearchModel] = None
        linked_user_id: Optional[StringSearchModel] = None


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


class ConnectionEntry(ResponseModel):
    person_id: str
    kind: str
    discriminator: Optional[str] = None


class ConnectionsResponse(ResponseModel):
    person_id: str
    connections: List[ConnectionEntry]


class MetamourEntry(ResponseModel):
    person_id: str
    via: List[str] = Field(..., description="The partners the two people share")


class MetamoursResponse(ResponseModel):
    person_id: str
    metamours: List[MetamourEntry]


class NetworkEntry(ResponseModel):
    person_id: str
    distance: int = Field(..., description="1 = a direct tie, 2 = a tie of a tie, …")


class NetworkTie(ResponseModel):
    """A tie between two people in a network, once however it was stored:
    a symmetric tie's ``person_id`` sorts first; a directed tie's is its
    subject (the mentor of a mentorship)."""

    person_id: str
    target_person_id: str
    kind: str
    discriminator: Optional[str] = None
    directed: bool = False


class NetworkResponse(ResponseModel):
    person_id: str
    kinds: List[str]
    members: List[NetworkEntry]
    ties: List[NetworkTie] = Field(
        ...,
        description="Every walked tie between the person and their members, to draw the network",
    )


class AttributeEntry(ResponseModel):
    id: str
    category: str
    label: str
    value: str
    unit: Optional[str] = None
    position: int = 0
    created_by_user_id: Optional[str] = None
    # The row's version (updated_at, else created_at), which a write sends back as If-Match.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PersonCardResponse(ResponseModel):
    """A person as the requester sees them: what the requester keeps about
    them, and what they published from their own record to the requester."""

    person_id: str
    linked_user_id: Optional[str] = None
    notes: List[AttributeEntry] = Field(
        ...,
        description="The attributes on this record: the requester's own notes, or their own profile",
    )
    published: List[AttributeEntry] = Field(
        ...,
        description=(
            "The attributes the linked account published from its self person to "
            "the requester (directly or through a team); none for a ghost"
        ),
    )


class LinkedNode(ResponseModel):
    """A person in the merged graph, as the requester may see them."""

    key: str = Field(
        ...,
        description="user:<account> for a linked account, person:<id> for anyone else",
    )
    name: Optional[str] = Field(
        None, description="Null when they show to the requester anonymously"
    )
    visibility: str
    mine: bool = Field(
        ..., description="The requester's own record of them, shown as written"
    )
    person_id: Optional[str] = Field(
        None, description="The requester's record of them, when they have one"
    )
    user_id: Optional[str] = None
    distance: int
    group: Optional[int] = Field(
        None, description="Which of the requester's polycules they are in"
    )


class LinkedTie(ResponseModel):
    source: str
    target: str
    kind: str
    discriminator: Optional[str] = None
    directed: bool = False


class LinkedNetworkResponse(ResponseModel):
    center: str
    kinds: List[str]
    nodes: List[LinkedNode]
    ties: List[LinkedTie]


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


def _kinds(kinds: Optional[str]) -> List[str]:
    """``?kinds=partnership,mentorship``: the kinds a social walk follows;
    every registered symmetric kind when omitted. A directed kind (but not
    ancestry, which the family-tree routes walk) may be named too, and is
    walked both ways with its direction kept. A kind that is not registered
    is refused, never silently ignored."""
    if kinds is None:
        return list(symmetric_kinds())
    known = tuple(sorted({*symmetric_kinds(), *directed_kinds()} - {ANCESTRY}))
    chosen = [k.strip() for k in kinds.split(",") if k.strip()]
    unknown = [k for k in chosen if k not in known]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Not a relationship kind to walk: {', '.join(unknown)}; expected {', '.join(known)}",
        )
    return chosen


def _as_datetime(value: Optional[date]) -> Optional[datetime]:
    return datetime.combine(value, time()) if value is not None else None


# Card categories whose values are sealed at rest (codes, say): an app registers them.
SENSITIVE_CATEGORIES: set[str] = set()


def register_sensitive_category(name: str) -> None:
    """Store every value in card category ``name`` encrypted (``encrypt_secret``),
    opened only when the card is read by someone who may see it."""
    SENSITIVE_CATEGORIES.add(name)


def _entries(rows: List[Any]) -> List[AttributeEntry]:
    return [
        AttributeEntry(
            id=r.id,
            category=r.category,
            label=r.label,
            # A sealed value (a sensitive category's) is opened here; plain ones pass through.
            value=decrypt_secret(r.value),
            unit=r.unit,
            position=r.position or 0,
            created_by_user_id=r.created_by_user_id,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in sorted(rows, key=lambda r: (r.category, r.position or 0, r.label))
    ]


class PersonManager(AbstractBLLManager, RouterMixin):
    _model = PersonModel

    def self_person_id(self, user_id: str) -> Optional[str]:
        """The id of ``user_id``'s self person, looked up as ROOT: only the id
        leaves this method, and what the requester then reads of it is
        filtered as the requester."""
        db = self._db(PersonModel)
        found = (
            db.list(
                requester_id=str(env("ROOT_ID")),
                model_registry=self.model_registry,
                filters=[
                    db.linked_user_id == user_id,
                    db.created_by_user_id == user_id,
                    # ROOT sees deleted rows; a deleted self person is gone.
                    db.deleted_at.is_(None),
                ],
                return_type="dto",
                override_dto=PersonModel,
            )
            or []
        )
        return str(found[0].id) if found else None

    def _check_self_link(
        self, fields: Dict[str, Any], person_id: Optional[str] = None
    ) -> None:
        """An account has one self person: a second record of the requester's
        linked to the requester is refused."""
        if fields.get("linked_user_id") != self.requester.id:
            return
        existing = self.self_person_id(str(self.requester.id))
        if existing is not None and existing != person_id:
            raise HTTPException(
                status_code=409, detail="You already have a self person"
            )

    def create(self, **kwargs: Any) -> Any:
        def checked(fields: Dict[str, Any]) -> Dict[str, Any]:
            self._check_self_link(fields)
            return fields

        return super().create(**each_created(kwargs, checked))

    def update(self, id: str, **kwargs: Any) -> Any:
        self._check_self_link(kwargs, id)
        return super().update(id, **kwargs)

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

    def social_graph(
        self, kinds: List[str], include_ended: bool = False
    ) -> SocialGraph:
        """The ties of ``kinds`` the requester can see; only those in effect
        now unless ``include_ended``."""
        db = self._db(RelationshipModel)
        directed = set(directed_kinds())
        ties = [
            Tie(
                person_id=r.person_id,
                target_person_id=r.target_person_id,
                kind=r.kind,
                discriminator=r.discriminator,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                directed=r.kind in directed,
            )
            for r in self._visible(RelationshipModel, [db.kind.in_(kinds)])
            if r.person_id and r.target_person_id
        ]
        return SocialGraph(
            ties, kinds, None if include_ended else datetime.now(timezone.utc)
        )

    def _tree(self, owner: str, kinds: List[str], include_ended: bool) -> Tree:
        """``owner``'s tree, read as ROOT for merging: only what ``merge``
        lets through, by each person's visibility, leaves this manager."""
        root = str(env("ROOT_ID"))
        people_db = self._db(PersonModel)
        persons = (
            people_db.list(
                requester_id=root,
                model_registry=self.model_registry,
                filters=[
                    people_db.created_by_user_id == owner,
                    people_db.deleted_at.is_(None),
                ],
                return_type="dto",
                override_dto=PersonModel,
            )
            or []
        )
        ties_db = self._db(RelationshipModel)
        directed = set(directed_kinds())
        rows = (
            ties_db.list(
                requester_id=root,
                model_registry=self.model_registry,
                filters=[
                    ties_db.created_by_user_id == owner,
                    ties_db.kind.in_(kinds),
                    ties_db.deleted_at.is_(None),
                ],
                return_type="dto",
                override_dto=RelationshipModel,
            )
            or []
        )
        now = datetime.now(timezone.utc)
        ties = [
            Tie(
                person_id=r.person_id,
                target_person_id=r.target_person_id,
                kind=r.kind,
                discriminator=r.discriminator,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                directed=r.kind in directed,
            )
            for r in rows
            if r.person_id and r.target_person_id
        ]
        return Tree(
            owner=owner,
            persons={
                p.id: TreePerson(
                    id=p.id,
                    owner=owner,
                    name=p.name,
                    linked_user_id=p.linked_user_id,
                    visibility=p.visibility,
                )
                for p in persons
            },
            ties=[t for t in ties if include_ended or t.in_effect(now)],
        )

    def linked_network(
        self, kinds: List[str], include_ended: bool = False
    ) -> Optional[MergedGraph]:
        """The requester's network merged across mutually linked trees, or
        None when they have no self person. For extensions building on it
        (who counts as in a polycule); the route answers the same."""
        viewer = self._tree(str(self.requester.id), kinds, include_ended)
        if viewer.self_person() is None:
            return None
        return merge(
            viewer, lambda owner: self._tree(owner, kinds, include_ended), kinds
        )

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
        path="/linked-network",
        output_model=LinkedNetworkResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="Your network merged with the trees of the accounts you and they have linked",
        expose_in=(ExposeIn.REST,),
    )
    def linked_network_route(
        self, kinds: Optional[str] = None, include_ended: bool = False
    ) -> LinkedNetworkResponse:
        """Centred on your self person; ``?kinds=partnership`` walks polycules.
        Each node's ``group`` says which of your polycules it is in."""
        chosen = _kinds(kinds)
        graph = self.linked_network(chosen, include_ended)
        if graph is None:
            raise HTTPException(status_code=404, detail="You have no self person yet")
        return LinkedNetworkResponse(
            center=graph.center,
            kinds=chosen,
            nodes=[
                LinkedNode(
                    key=n.key,
                    name=n.name,
                    visibility=n.visibility,
                    mine=n.mine,
                    person_id=n.person_id,
                    user_id=n.user_id,
                    distance=n.distance,
                    group=n.group,
                )
                for n in graph.nodes
            ],
            ties=[
                LinkedTie(
                    source=t.person_id,
                    target=t.target_person_id,
                    kind=t.kind,
                    discriminator=t.discriminator,
                    directed=t.directed,
                )
                for t in graph.ties
            ],
        )

    @custom_route(
        method="GET",
        path="/{person_id}/card",
        output_model=PersonCardResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="A person's card: your notes about them, and what they published to you",
        expose_in=(ExposeIn.REST,),
    )
    def card_route(self, person_id: str) -> PersonCardResponse:
        self._require_person(person_id)
        person = self._db(PersonModel).get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=person_id,
            return_type="dto",
            override_dto=PersonModel,
        )
        attributes = self._db(PersonAttributeModel)
        notes = self._visible(PersonAttributeModel, [attributes.person_id == person_id])
        published: List[Any] = []
        linked = person.linked_user_id
        if linked:
            self_id = self.self_person_id(str(linked))
            if self_id is not None and self_id != person_id:
                published = self._visible(
                    PersonAttributeModel, [attributes.person_id == self_id]
                )
        return PersonCardResponse(
            person_id=person_id,
            linked_user_id=linked,
            notes=_entries(notes),
            published=_entries(published),
        )

    @custom_route(
        method="GET",
        path="/{person_id}/connections",
        output_model=ConnectionsResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="Everyone a person is tied to: partners, friends, siblings, acquaintances",
        expose_in=(ExposeIn.REST,),
    )
    def connections_route(
        self,
        person_id: str,
        kinds: Optional[str] = None,
        include_ended: bool = False,
    ) -> ConnectionsResponse:
        """``?kinds=friendship`` limits the kinds of tie; ``?include_ended=true``
        keeps ties whose ``valid_to`` has passed."""
        self._require_person(person_id)
        graph = self.social_graph(_kinds(kinds), include_ended)
        return ConnectionsResponse(
            person_id=person_id,
            connections=[
                ConnectionEntry(
                    person_id=c.person_id, kind=c.kind, discriminator=c.discriminator
                )
                for c in graph.connections(person_id)
            ],
        )

    @custom_route(
        method="GET",
        path="/{person_id}/metamours",
        output_model=MetamoursResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="A person's partners' partners who are not their own partners",
        expose_in=(ExposeIn.REST,),
    )
    def metamours_route(
        self, person_id: str, include_ended: bool = False
    ) -> MetamoursResponse:
        self._require_person(person_id)
        found = self.social_graph([PARTNERSHIP], include_ended).metamours(person_id)
        return MetamoursResponse(
            person_id=person_id,
            metamours=[
                MetamourEntry(person_id=m, via=sorted(via))
                for m, via in sorted(found.items())
            ],
        )

    @custom_route(
        method="GET",
        path="/{person_id}/network",
        output_model=NetworkResponse,
        authentication_type="jwt",
        openapi_tags=("Genealogy",),
        summary="Everyone reachable from a person through the chosen kinds of tie, nearest first",
        expose_in=(ExposeIn.REST,),
    )
    def network_route(
        self,
        person_id: str,
        kinds: Optional[str] = None,
        depth: Optional[int] = None,
        include_ended: bool = False,
    ) -> NetworkResponse:
        """``?kinds=partnership`` walks a polycule; ``?depth=`` limits how many
        ties away."""
        self._require_person(person_id)
        chosen = _kinds(kinds)
        graph = self.social_graph(chosen, include_ended)
        found = graph.network(person_id, depth)
        return NetworkResponse(
            person_id=person_id,
            kinds=chosen,
            members=[
                NetworkEntry(person_id=p, distance=d)
                for p, d in sorted(found.items(), key=lambda item: (item[1], item[0]))
            ],
            ties=[
                NetworkTie(
                    person_id=t.person_id,
                    target_person_id=t.target_person_id,
                    kind=t.kind,
                    discriminator=t.discriminator,
                    directed=t.directed,
                )
                for t in graph.ties_among([person_id, *found])
            ],
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
    # standalone-genealogy users may introduce their own taxonomies; the
    # kinds registered in ``Relationships`` are checked on every write.
    # Conventional values:
    #   ancestry|partnership|sibling_of|friendship|acquaintance (genealogy core)
    #   member_of|affiliated_with                  (rpg_state when injected)
    #   social|obligation                          (rpg_state when injected)
    kind: Optional[str] = Field(
        None,
        description=(
            "Relationship kind. Genealogy core: ancestry (directed) and the "
            "symmetric partnership|sibling_of|friendship|acquaintance. "
            "Downstream extensions add their own labels."
        ),
    )
    discriminator: Optional[str] = Field(
        None,
        description=(
            "Subkind / refinement of the relationship: "
            f"{describe_discriminators()}; "
            "leader|member|treasurer for member_of; etc."
        ),
    )
    intensity: Optional[float] = Field(
        None,
        ge=INTENSITY_MIN,
        le=INTENSITY_MAX,
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
        intensity: Optional[float] = Field(None, ge=INTENSITY_MIN, le=INTENSITY_MAX)
        qualifier: Optional[str] = None
        valid_from: Optional[datetime] = None
        valid_to: Optional[datetime] = None
        notes: Optional[str] = None

    class Update(BaseModel):
        kind: Optional[str] = None
        discriminator: Optional[str] = None
        intensity: Optional[float] = Field(None, ge=INTENSITY_MIN, le=INTENSITY_MAX)
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


def _checked(fields: Dict[str, Any]) -> Dict[str, Any]:
    """``fields`` after :func:`check_relationship`; 422 when they fail it."""
    try:
        check_relationship(
            fields.get("kind"),
            fields.get("discriminator"),
            fields.get("person_id"),
            fields.get("target_person_id"),
        )
    except RelationshipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return fields


class RelationshipManager(AbstractBLLManager, RouterMixin):
    _model = RelationshipModel

    def create(self, **kwargs: Any) -> Any:
        """A relationship of a registered kind must relate two different
        people and name a discriminator the kind accepts."""
        return super().create(**each_created(kwargs, _checked))

    def update(self, id: str, **kwargs: Any) -> Any:
        """The changed relationship, read whole, passes the same check."""
        if {"kind", "discriminator"} & kwargs.keys():
            current = self.get(id=id)
            _checked(
                {
                    "kind": current.kind,
                    "discriminator": current.discriminator,
                    "person_id": current.person_id,
                    "target_person_id": current.target_person_id,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if k in ("kind", "discriminator")
                    },
                }
            )
        return super().update(id, **kwargs)


RelationshipModel.Manager = RelationshipManager


# ---------------------------------------------------------------------------
# Person attribute
# ---------------------------------------------------------------------------


class PersonAttributeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    PersonModel.Reference.Optional,  # → person_id
    metaclass=ModelMeta,
):
    """One typed fact on a person's card (``category`` "sizes", ``label``
    "ring", ``value`` "7", ``unit`` "US"). On someone else's record it is
    its creator's private note; on an account's self person it is that
    account's profile, published per record through the framework's grants
    (a user, or a team), so each field is shared on its own."""

    Manager: ClassVar[Type["PersonAttributeManager"]]
    category: str = Field(
        ..., description="The card section: contact, sizes, allergies, favourites, …"
    )
    label: str = Field(..., description="What the value is, within its category")
    value: str = Field(..., description="The value, as written")
    unit: Optional[str] = Field(
        None, description="The value's unit or system (cm, US, EU, …)"
    )
    position: Optional[int] = Field(0, description="Order within its category")

    table_comment: ClassVar[str] = (
        "Typed facts on a person's card: a note on someone else's record, a "
        "published profile field on an account's self person"
    )

    class Create(BaseModel, PersonModel.Reference.ID):
        category: str = Field(..., min_length=1)
        label: str = Field(..., min_length=1)
        value: str
        unit: Optional[str] = None
        position: Optional[int] = 0

    class Update(BaseModel):
        category: Optional[str] = Field(None, min_length=1)
        label: Optional[str] = Field(None, min_length=1)
        value: Optional[str] = None
        unit: Optional[str] = None
        position: Optional[int] = None

    class Search(ApplicationModel.Search, PersonModel.Reference.ID.Search):
        category: Optional[StringSearchModel] = None
        label: Optional[StringSearchModel] = None


class PersonAttributeManager(AbstractBLLManager, RouterMixin):
    _model = PersonAttributeModel

    def create(self, **kwargs: Any) -> Any:
        """Only on a person the requester owns: their own notes, or their own
        profile on their self person."""
        people = PersonModel.DB(self.model_registry.DB.manager.Base)

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            person = people.get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=fields.get("person_id"),
                return_type="dto",
                override_dto=PersonModel,
            )
            if person is None or person.created_by_user_id != self.requester.id:
                raise HTTPException(status_code=404, detail="Person not found")
            return _sealed(fields, fields.get("category"))

        return super().create(**each_created(kwargs, owned))

    def update(self, id: str, **kwargs: Any) -> Any:
        """A value in a sensitive category stays sealed, including a value
        moved into one (its category changed, its value unchanged)."""
        category = kwargs.get("category")
        if "value" in kwargs or category in SENSITIVE_CATEGORIES:
            current = PersonAttributeModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=id,
                return_type="dto",
                override_dto=PersonAttributeModel,
            )
            if current is not None:
                fields = {"value": decrypt_secret(current.value), **kwargs}
                kwargs = _sealed(fields, category or current.category)
        return super().update(id, **kwargs)


def _sealed(fields: Dict[str, Any], category: Optional[str]) -> Dict[str, Any]:
    """``fields`` with the value encrypted when ``category`` is sensitive."""
    if category in SENSITIVE_CATEGORIES and fields.get("value"):
        return {**fields, "value": encrypt_secret(fields["value"])}
    return fields


PersonAttributeModel.Manager = PersonAttributeManager


# ---------------------------------------------------------------------------
# Right to erasure
# ---------------------------------------------------------------------------


def erase_genealogy(user_id: str, model_registry: Any) -> None:
    """A deleted account's own tree goes (its people, ties, notes and profile);
    the records others keep of them stay theirs, returned to ghosts (no longer
    linked to the account). Safe to run twice."""
    root = env("ROOT_ID")
    base = model_registry.DB.manager.Base
    for model in (PersonAttributeModel, RelationshipModel, PersonModel):
        db = model.DB(base)
        rows = (
            db.list(
                requester_id=root,
                model_registry=model_registry,
                filters=[db.created_by_user_id == user_id, db.deleted_at.is_(None)],
                return_type="dto",
                override_dto=model,
            )
            or []
        )
        for row in rows:
            db.delete(
                requester_id=root,
                model_registry=model_registry,
                filters=[db.id == row.id],
            )
    people = PersonModel.DB(base)
    linked = (
        people.list(
            requester_id=root,
            model_registry=model_registry,
            filters=[people.linked_user_id == user_id, people.deleted_at.is_(None)],
            return_type="dto",
            override_dto=PersonModel,
        )
        or []
    )
    for row in linked:
        people.update(
            requester_id=root,
            model_registry=model_registry,
            new_properties={"linked_user_id": None},
            filters=[people.id == row.id],
        )


register_eraser("genealogy", erase_genealogy)


# ---------------------------------------------------------------------------
# Public roster
# ---------------------------------------------------------------------------


ALL_MODELS: List[type] = [
    PersonModel,
    RelationshipModel,
    PersonAttributeModel,
]


__all__ = [
    "PersonModel",
    "PersonManager",
    "RelationshipModel",
    "RelationshipManager",
    "PersonAttributeModel",
    "PersonAttributeManager",
    "register_sensitive_category",
    "erase_genealogy",
    "ALL_MODELS",
]
