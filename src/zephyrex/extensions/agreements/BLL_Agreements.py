# SPDX-License-Identifier: AGPL-3.0-or-later
"""Written agreements between people, confirmed by each and revisited together.

An agreement is started by one account (its owner), who names the other
accounts in it (its parties; the owner is one too). Every party reads it and
writes it: its title, when to revisit it, and its terms. A term is one thing
agreed, a like or a limit.

Each party confirms the agreement as it stands. Any change to its terms
moves it to a new version, and everyone confirms again; until they all
have, it is not in effect. A party may leave; only the owner adds people,
and deletes it.

Routes, all under ``/v1/agreements``: ``GET /mine``; ``POST ""`` (start
one); ``POST /{id}/details``; ``POST /{id}/remove``; ``POST /{id}/parties`` and
``POST /{id}/parties/remove``; ``POST /{id}/terms``, ``POST
/terms/{term_id}`` and ``POST /terms/{term_id}/remove``; ``POST
/{id}/confirm``.
"""

from datetime import date, datetime
from typing import Any, ClassVar, List, Literal, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.privacy.Erasure import register_eraser
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

TermKind = Literal["agreement", "like", "limit"]
MAX_PARTIES = 12
MAX_TERMS = 200
MAX_TITLE = 200
MAX_TERM = 2000


class AgreementModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["AgreementManager"]]
    title: str = Field(..., description="What the agreement is called")
    revisit_on: Optional[date] = Field(None, description="When to look at it again")
    version: int = Field(1, description="Moves on whenever its terms change")

    table_comment: ClassVar[str] = "Written agreements between people"

    class Create(BaseModel):
        title: str = Field(..., min_length=1, max_length=MAX_TITLE)
        revisit_on: Optional[date] = None
        version: int = 1

    class Update(BaseModel):
        title: Optional[str] = Field(None, min_length=1, max_length=MAX_TITLE)
        revisit_on: Optional[date] = None
        version: Optional[int] = None

    class Search(ApplicationModel.Search):
        title: Optional[StringSearchModel] = None


class AgreementPartyModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["AgreementPartyManager"]]
    agreement_id: str = Field(..., description="The agreement")
    user_id: str = Field(..., description="The account that is party to it")
    confirmed_version: Optional[int] = Field(
        None, description="The version they last confirmed; none until they do"
    )

    table_comment: ClassVar[str] = (
        "Who is party to each agreement, and what they confirmed"
    )

    class Create(BaseModel):
        agreement_id: str
        user_id: str
        confirmed_version: Optional[int] = None

    class Update(BaseModel):
        confirmed_version: Optional[int] = None

    class Search(ApplicationModel.Search):
        agreement_id: Optional[StringSearchModel] = None


class AgreementTermModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["AgreementTermManager"]]
    agreement_id: str = Field(..., description="The agreement")
    kind: str = Field("agreement", description="agreement, like or limit")
    text: str = Field(..., description="The term, in the parties' own words")
    position: int = Field(0, description="Its place in the agreement")
    written_by: str = Field(..., description="The party who last wrote it")

    table_comment: ClassVar[str] = (
        "The terms of each agreement: things agreed, likes and limits"
    )

    class Create(BaseModel):
        agreement_id: str
        kind: str = "agreement"
        text: str
        position: int = 0
        written_by: str

    class Update(BaseModel):
        kind: Optional[str] = None
        text: Optional[str] = None
        position: Optional[int] = None
        written_by: Optional[str] = None

    class Search(ApplicationModel.Search):
        agreement_id: Optional[StringSearchModel] = None


class TermEntry(ResponseModel):
    id: str
    kind: str
    text: str
    position: int
    written_by: str
    updated_at: Optional[datetime] = None


class PartyEntry(ResponseModel):
    user_id: str
    confirmed: bool = Field(..., description="Whether they confirmed this version")
    confirmed_version: Optional[int] = None


class AgreementEntry(ResponseModel):
    id: str
    title: str
    revisit_on: Optional[date] = None
    version: int
    owner_id: str
    parties: List[PartyEntry]
    terms: List[TermEntry]
    in_effect: bool = Field(..., description="Every party has confirmed this version")
    updated_at: Optional[datetime] = None


class AgreementList(ResponseModel):
    agreements: List[AgreementEntry]


class AgreementStart(ResponseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE)
    revisit_on: Optional[date] = None
    party_user_ids: List[str] = Field(default_factory=list, max_length=MAX_PARTIES)


class AgreementChange(ResponseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=MAX_TITLE)
    revisit_on: Optional[date] = None
    clear_revisit: bool = Field(False, description="Stop asking to revisit it")


class NoDetails(ResponseModel):
    """The body of an action that needs nothing more than its path."""


class PartyRequest(ResponseModel):
    user_id: str


class TermRequest(ResponseModel):
    kind: TermKind = "agreement"
    text: str = Field(..., min_length=1, max_length=MAX_TERM)


class TermChange(ResponseModel):
    kind: Optional[TermKind] = None
    text: Optional[str] = Field(None, min_length=1, max_length=MAX_TERM)


def _root() -> str:
    return str(env("ROOT_ID"))


def _db(model: Any, model_registry: Any) -> Any:
    return model.DB(model_registry.DB.manager.Base)


def _rows(model: Any, model_registry: Any, conditions: Any) -> List[Any]:
    db = _db(model, model_registry)
    return (
        db.list(
            requester_id=_root(),
            model_registry=model_registry,
            filters=[*conditions(db), db.deleted_at.is_(None)],
            return_type="dto",
            override_dto=model,
        )
        or []
    )


def _set(model: Any, model_registry: Any, row_id: str, **fields: Any) -> Any:
    db = _db(model, model_registry)
    return db.update(
        requester_id=_root(),
        model_registry=model_registry,
        new_properties=fields,
        filters=[db.id == row_id],
        return_type="dto",
        override_dto=model,
    )


def _drop(model: Any, model_registry: Any, conditions: Any) -> None:
    db = _db(model, model_registry)
    for row in _rows(model, model_registry, conditions):
        db.delete(
            requester_id=_root(),
            model_registry=model_registry,
            filters=[db.id == row.id],
        )


def _add(model: Any, model_registry: Any, requester_id: str, **fields: Any) -> Any:
    return _db(model, model_registry).create(
        requester_id=requester_id,
        model_registry=model_registry,
        return_type="dto",
        override_dto=model,
        **fields,
    )


def in_effect(version: int, parties: List[Any]) -> bool:
    """Whether every party has confirmed ``version``."""
    return bool(parties) and all(p.confirmed_version == version for p in parties)


def _entry(agreement: Any, parties: List[Any], terms: List[Any]) -> AgreementEntry:
    return AgreementEntry(
        id=agreement.id,
        title=agreement.title,
        revisit_on=agreement.revisit_on,
        version=agreement.version,
        owner_id=str(agreement.created_by_user_id),
        parties=[
            PartyEntry(
                user_id=str(p.user_id),
                confirmed=p.confirmed_version == agreement.version,
                confirmed_version=p.confirmed_version,
            )
            for p in sorted(parties, key=lambda p: p.created_at or datetime.min)
        ],
        terms=[
            TermEntry(
                id=t.id,
                kind=t.kind,
                text=t.text,
                position=t.position,
                written_by=str(t.written_by),
                updated_at=t.updated_at or t.created_at,
            )
            for t in sorted(
                terms, key=lambda t: (t.position, t.created_at or datetime.min)
            )
        ],
        in_effect=in_effect(agreement.version, parties),
        updated_at=agreement.updated_at or agreement.created_at,
    )


class AgreementPartyManager(AbstractBLLManager, RouterMixin):
    """Parties are kept through ``/v1/agreements``; no routes of their own."""

    _model = AgreementPartyModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []


class AgreementTermManager(AbstractBLLManager, RouterMixin):
    """Terms are kept through ``/v1/agreements``; no routes of their own."""

    _model = AgreementTermModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []


class AgreementManager(AbstractBLLManager, RouterMixin):
    """``/v1/agreements``: what the parties to an agreement see and do."""

    _model = AgreementModel
    prefix: ClassVar[Optional[str]] = "/v1/agreements"
    tags: ClassVar[Optional[List[str]]] = ["Agreements"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @property
    def _me(self) -> str:
        return str(self.requester.id)

    def _parties(self, agreement_id: str) -> List[Any]:
        return _rows(
            AgreementPartyModel,
            self.model_registry,
            lambda db: [db.agreement_id == agreement_id],
        )

    def _terms(self, agreement_id: str) -> List[Any]:
        return _rows(
            AgreementTermModel,
            self.model_registry,
            lambda db: [db.agreement_id == agreement_id],
        )

    def _party_to(self, agreement_id: str) -> tuple[Any, List[Any]]:
        """The agreement and its parties, when the requester is one; else 404."""
        found = _rows(
            AgreementModel, self.model_registry, lambda db: [db.id == agreement_id]
        )
        parties = self._parties(agreement_id) if found else []
        if not found or self._me not in {str(p.user_id) for p in parties}:
            raise HTTPException(status_code=404, detail="Agreement not found")
        return found[0], parties

    def _owned(self, agreement_id: str) -> tuple[Any, List[Any]]:
        agreement, parties = self._party_to(agreement_id)
        if str(agreement.created_by_user_id) != self._me:
            raise HTTPException(
                status_code=403, detail="Only the person who started it can do that"
            )
        return agreement, parties

    def _shown(self, agreement_id: str) -> AgreementEntry:
        agreement, parties = self._party_to(agreement_id)
        return _entry(agreement, parties, self._terms(agreement_id))

    def _changed(self, agreement: Any) -> AgreementEntry:
        """Terms changed: a new version, which everyone confirms again."""
        _set(
            AgreementModel,
            self.model_registry,
            agreement.id,
            version=agreement.version + 1,
        )
        return self._shown(agreement.id)

    def _term(self, term_id: str) -> tuple[Any, Any]:
        found = _rows(
            AgreementTermModel, self.model_registry, lambda db: [db.id == term_id]
        )
        if not found:
            raise HTTPException(status_code=404, detail="Term not found")
        agreement, _ = self._party_to(found[0].agreement_id)
        return agreement, found[0]

    def _mine(self) -> AgreementList:
        me = self._me
        ids = {
            p.agreement_id
            for p in _rows(
                AgreementPartyModel, self.model_registry, lambda db: [db.user_id == me]
            )
        }
        found = (
            _rows(AgreementModel, self.model_registry, lambda db: [db.id.in_(ids)])
            if ids
            else []
        )
        return AgreementList(
            agreements=[
                _entry(a, self._parties(a.id), self._terms(a.id))
                for a in sorted(found, key=lambda a: a.title.lower())
            ]
        )

    @custom_route(
        method="GET",
        path="/mine",
        output_model=AgreementList,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="The agreements you are party to",
        expose_in=(ExposeIn.REST,),
    )
    def mine_route(self) -> AgreementList:
        return self._mine()

    @custom_route(
        method="POST",
        path="",
        input_model=AgreementStart,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Start an agreement with the people named",
        expose_in=(ExposeIn.REST,),
    )
    def start_route(self, body: AgreementStart) -> AgreementEntry:
        me = self._me
        others = list(dict.fromkeys(u for u in body.party_user_ids if u != me))
        agreement = _add(
            AgreementModel,
            self.model_registry,
            me,
            title=body.title.strip(),
            revisit_on=body.revisit_on,
            version=1,
        )
        for user_id in [me, *others]:
            _add(
                AgreementPartyModel,
                self.model_registry,
                _root(),
                agreement_id=agreement.id,
                user_id=user_id,
            )
        return self._shown(agreement.id)

    @custom_route(
        method="POST",
        path="/{agreement_id}/details",
        input_model=AgreementChange,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Rename it, or change when to revisit it",
        expose_in=(ExposeIn.REST,),
    )
    def change_route(self, agreement_id: str, body: AgreementChange) -> AgreementEntry:
        agreement, _ = self._party_to(agreement_id)
        fields: dict[str, Any] = {}
        if body.title is not None:
            fields["title"] = body.title.strip()
        if body.clear_revisit:
            fields["revisit_on"] = None
        elif body.revisit_on is not None:
            fields["revisit_on"] = body.revisit_on
        if fields:
            _set(AgreementModel, self.model_registry, agreement.id, **fields)
        return self._shown(agreement.id)

    @custom_route(
        method="POST",
        path="/{agreement_id}/remove",
        input_model=NoDetails,
        output_model=AgreementList,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Delete an agreement you started, for everyone in it",
        expose_in=(ExposeIn.REST,),
    )
    def remove_route(self, agreement_id: str, body: NoDetails) -> AgreementList:
        agreement, _ = self._owned(agreement_id)
        erase_agreement(agreement.id, self.model_registry)
        return self._mine()

    @custom_route(
        method="POST",
        path="/{agreement_id}/parties",
        input_model=PartyRequest,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Add someone to an agreement you started",
        expose_in=(ExposeIn.REST,),
    )
    def add_party_route(self, agreement_id: str, body: PartyRequest) -> AgreementEntry:
        agreement, parties = self._owned(agreement_id)
        if body.user_id in {str(p.user_id) for p in parties}:
            raise HTTPException(status_code=409, detail="They are already in it")
        if len(parties) >= MAX_PARTIES:
            raise HTTPException(
                status_code=409, detail="An agreement has room for twelve people"
            )
        _add(
            AgreementPartyModel,
            self.model_registry,
            _root(),
            agreement_id=agreement.id,
            user_id=body.user_id,
        )
        # Someone new has not confirmed it, so it is no longer in effect until they do.
        return self._shown(agreement.id)

    @custom_route(
        method="POST",
        path="/{agreement_id}/parties/remove",
        input_model=PartyRequest,
        output_model=AgreementList,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Leave an agreement, or (its owner) take someone out of it",
        expose_in=(ExposeIn.REST,),
    )
    def remove_party_route(
        self, agreement_id: str, body: PartyRequest
    ) -> AgreementList:
        agreement, parties = self._party_to(agreement_id)
        owner = str(agreement.created_by_user_id)
        if body.user_id != self._me and self._me != owner:
            raise HTTPException(
                status_code=403,
                detail="Only the person who started it can take someone out",
            )
        if body.user_id == owner:
            raise HTTPException(
                status_code=409,
                detail="The person who started it deletes it instead of leaving",
            )
        if body.user_id not in {str(p.user_id) for p in parties}:
            raise HTTPException(status_code=404, detail="They are not in it")
        _drop(
            AgreementPartyModel,
            self.model_registry,
            lambda db: [db.agreement_id == agreement.id, db.user_id == body.user_id],
        )
        return self._mine()

    @custom_route(
        method="POST",
        path="/{agreement_id}/terms",
        input_model=TermRequest,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Add a term: something agreed, a like or a limit",
        expose_in=(ExposeIn.REST,),
    )
    def add_term_route(self, agreement_id: str, body: TermRequest) -> AgreementEntry:
        agreement, _ = self._party_to(agreement_id)
        terms = self._terms(agreement.id)
        if len(terms) >= MAX_TERMS:
            raise HTTPException(
                status_code=409,
                detail="That agreement has as many terms as it can hold",
            )
        _add(
            AgreementTermModel,
            self.model_registry,
            self._me,
            agreement_id=agreement.id,
            kind=body.kind,
            text=body.text.strip(),
            position=max((t.position for t in terms), default=-1) + 1,
            written_by=self._me,
        )
        return self._changed(agreement)

    @custom_route(
        method="POST",
        path="/terms/{term_id}",
        input_model=TermChange,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Reword a term, or change what kind it is",
        expose_in=(ExposeIn.REST,),
    )
    def change_term_route(self, term_id: str, body: TermChange) -> AgreementEntry:
        agreement, term = self._term(term_id)
        fields: dict[str, Any] = {}
        if body.kind is not None and body.kind != term.kind:
            fields["kind"] = body.kind
        if body.text is not None and body.text.strip() != term.text:
            fields["text"] = body.text.strip()
        if not fields:
            return self._shown(agreement.id)
        _set(
            AgreementTermModel,
            self.model_registry,
            term.id,
            **fields,
            written_by=self._me,
        )
        return self._changed(agreement)

    @custom_route(
        method="POST",
        path="/terms/{term_id}/remove",
        input_model=NoDetails,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Take a term out",
        expose_in=(ExposeIn.REST,),
    )
    def remove_term_route(self, term_id: str, body: NoDetails) -> AgreementEntry:
        agreement, term = self._term(term_id)
        _drop(AgreementTermModel, self.model_registry, lambda db: [db.id == term.id])
        return self._changed(agreement)

    @custom_route(
        method="POST",
        path="/{agreement_id}/confirm",
        input_model=NoDetails,
        output_model=AgreementEntry,
        authentication_type="jwt",
        openapi_tags=("Agreements",),
        summary="Confirm the agreement as it stands",
        expose_in=(ExposeIn.REST,),
    )
    def confirm_route(self, agreement_id: str, body: NoDetails) -> AgreementEntry:
        agreement, parties = self._party_to(agreement_id)
        (mine,) = [p for p in parties if str(p.user_id) == self._me]
        _set(
            AgreementPartyModel,
            self.model_registry,
            mine.id,
            confirmed_version=agreement.version,
        )
        return self._shown(agreement.id)


AgreementModel.Manager = AgreementManager
AgreementPartyModel.Manager = AgreementPartyManager
AgreementTermModel.Manager = AgreementTermManager


def erase_agreement(agreement_id: str, model_registry: Any) -> None:
    """An agreement goes, with its parties and terms."""
    _drop(
        AgreementTermModel, model_registry, lambda db: [db.agreement_id == agreement_id]
    )
    _drop(
        AgreementPartyModel,
        model_registry,
        lambda db: [db.agreement_id == agreement_id],
    )
    _drop(AgreementModel, model_registry, lambda db: [db.id == agreement_id])


def erase_agreements(user_id: str, model_registry: Any) -> None:
    """A deleted account's agreements go, for everyone in them; it leaves
    other people's. Terms it wrote in those stay: they belong to the
    agreement. Safe to run twice."""
    for agreement in _rows(
        AgreementModel, model_registry, lambda db: [db.created_by_user_id == user_id]
    ):
        erase_agreement(agreement.id, model_registry)
    _drop(AgreementPartyModel, model_registry, lambda db: [db.user_id == user_id])


register_eraser("agreements", erase_agreements)

ALL_MODELS: List[type] = [AgreementModel, AgreementPartyModel, AgreementTermModel]
