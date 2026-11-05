# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared watch-lists, and tonight's pick from them.

A watch-list belongs to the account that keeps it; the keeper adds the
people it is shared with. Everyone on it (keeper and members) adds titles
from the media catalogue (by media id; the server looks each one up, so a
list holds only real titles), marks the ones they want to see, and marks a
title watched. Tonight's pick is an unwatched title everyone on the list
wants, else one the most of them want; among equals it is chosen at random,
the same choice for everyone that day.

Routes on ``/v1/watch-lists``: ``GET /search`` (the catalogue), ``GET
/mine``, ``POST /`` (start a list), ``POST /{list_id}/members``, ``POST
/{list_id}/members/{user_id}/remove``, ``POST /{list_id}/titles``, ``POST
/titles/{title_id}/want``, ``POST /titles/{title_id}/watched``, ``POST
/titles/{title_id}/remove`` and ``GET /{list_id}/pick``. They are actions on
a shared list rather than writes to a versioned row: two people acting at
once lose nothing.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.extensions.media.EXT_Media import MEDIA_KINDS, EXT_Media
from zephyrex.extensions.privacy.Erasure import register_eraser
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
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

WATCH_LISTS_PREFIX = "/v1/watch-lists"
MAX_NAME = 100
MAX_MEMBERS = 20
MAX_TITLES = 200
MAX_QUERY = 200
SEARCH_RESULTS = 10
HTTP_SERVER_ERROR = 500
CATALOGUE_SILENT = "The film catalogue did not answer; try again shortly"


    Manager: ClassVar[Type["WatchListManager"]]
    name: str = Field(..., description="What the list is called, e.g. 'Movie night'")

    table_comment: ClassVar[str] = (
        "Watch-lists, kept by their keeper and shared with members"
    )

    class Create(BaseModel):
        name: str = Field(..., min_length=1, max_length=MAX_NAME)

    class Update(BaseModel):
        name: Optional[str] = Field(None, min_length=1, max_length=MAX_NAME)

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None


    Manager: ClassVar[Type["WatchListManager"]]
    list_id: str = Field(..., description="The list")
    user_id: str = Field(..., description="An account the list is shared with")

    table_comment: ClassVar[str] = "Whom each watch-list is shared with"

    class Create(BaseModel):
        list_id: str
        user_id: str

    class Update(BaseModel):
        user_id: Optional[str] = None

    class Search(ApplicationModel.Search):
        list_id: Optional[StringSearchModel] = None


    Manager: ClassVar[Type["WatchListManager"]]
    list_id: str = Field(..., description="The list")
    media_id: str = Field(..., description="The catalogue's id, e.g. tmdb:movie:603")
    title: str = Field(..., description="As the catalogue names it")
    kind: str = Field(..., description="movie, tv or video")
    year: Optional[int] = Field(None, description="The year it came out")
    overview: Optional[str] = Field(None, description="What it is about")
    url: Optional[str] = Field(None, description="Its page at the catalogue")
    image: Optional[str] = Field(None, description="Its poster or thumbnail")
    watched_on: Optional[date] = Field(None, description="When the list watched it")

    table_comment: ClassVar[str] = "The titles on each watch-list"

    class Create(BaseModel):
        list_id: str
        media_id: str
        title: str
        kind: str
        year: Optional[int] = None
        overview: Optional[str] = None
        url: Optional[str] = None
        image: Optional[str] = None
        watched_on: Optional[date] = None

    class Update(BaseModel):
        watched_on: Optional[date] = None

    class Search(ApplicationModel.Search):
        list_id: Optional[StringSearchModel] = None


    Manager: ClassVar[Type["WatchListManager"]]
    title_id: str = Field(..., description="The title someone wants to see")

    table_comment: ClassVar[str] = "Who wants to see each title on a watch-list"

    class Create(BaseModel):
        title_id: str

    class Update(BaseModel):
        title_id: Optional[str] = None

    class Search(ApplicationModel.Search):
        title_id: Optional[StringSearchModel] = None


class MediaItem(ResponseModel):
    id: str
    title: str
    kind: str
    year: Optional[int] = None
    overview: Optional[str] = None
    url: Optional[str] = None
    image: Optional[str] = None


class MediaList(ResponseModel):
    results: List[MediaItem]


class TitleEntry(MediaItem):
    title_id: str
    added_by: Optional[str] = None
    watched_on: Optional[date] = None
    wanted_by: List[str]


class WatchListEntry(ResponseModel):
    id: str
    name: str
    keeper_id: str
    member_ids: List[str]
    titles: List[TitleEntry]


class WatchLists(ResponseModel):
    lists: List[WatchListEntry]


class NewList(ResponseModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME)


class NewMember(ResponseModel):
    user_id: str = Field(..., min_length=1)


class NewTitle(ResponseModel):
    media_id: str = Field(..., min_length=1)


class NoDetails(ResponseModel):
    """The body of an action that needs nothing more than its path."""


class Want(ResponseModel):
    want: bool


class Watched(ResponseModel):
    watched: bool


class Pick(ResponseModel):
    title: Optional[TitleEntry] = Field(
        None, description="None when nothing unwatched is wanted"
    )
    everyone: bool = Field(False, description="Everyone on the list wants it")
    wanted_by: int = 0
    of: int = Field(..., description="How many are on the list")


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


def _create(model: Any, model_registry: Any, requester: str, **fields: Any) -> Any:
    return _db(model, model_registry).create(
        requester_id=requester,
        model_registry=model_registry,
        return_type="dto",
        override_dto=model,
        **fields,
    )


def _delete(model: Any, model_registry: Any, row_id: str) -> None:
    db = _db(model, model_registry)
    db.delete(
        requester_id=_root(), model_registry=model_registry, filters=[db.id == row_id]
    )


    """Tonight's pick from ``titles``: unwatched ones everyone (``people``)
    wants, else those the most want; among equals, one chosen by the day, so
    everyone on the list sees the same pick until tomorrow."""
    unwatched = [
        title for title in titles if title.watched_on is None and title.wanted_by
    ]
    if not unwatched:
        return Pick(of=people)
    most = max(len(title.wanted_by) for title in unwatched)
    best = sorted(
        (title for title in unwatched if len(title.wanted_by) == most),
        key=lambda t: t.title_id,
    )
    seed = int(
        hashlib.sha256(f"{list_id}:{today.isoformat()}".encode()).hexdigest(), 16
    )
    return Pick(title=chosen, everyone=most >= people, wanted_by=most, of=people)


def catalogue_refusal(err: Exception) -> HTTPException:
    """The catalogue failing, as an answer a person can act on."""
    if isinstance(err, InvalidInputExternalError):
        return HTTPException(status_code=422, detail=str(err))
    if isinstance(err, RateLimitExternalError):
        return HTTPException(
            status_code=429, detail="The film catalogue is busy; try again shortly"
        )
    logger.warning("media: the catalogue failed: %s", err)
    return HTTPException(status_code=502, detail=CATALOGUE_SILENT)


async def _asked(asking: Any) -> Any:
    try:
        return await asking
    except BaseExternalError as err:
        raise catalogue_refusal(err) from err
    except HTTPException as err:
        if err.status_code < HTTP_SERVER_ERROR:
            raise
        raise catalogue_refusal(err) from err


def _item(found: Dict[str, Any]) -> MediaItem:
    return MediaItem(
        id=str(found["id"]),
        title=str(found.get("title") or ""),
        kind=str(found.get("kind") or ""),
        year=found.get("year"),
        overview=found.get("overview") or None,
        url=found.get("url"),
        image=found.get("image"),
    )


    """``/v1/watch-lists``: the lists someone keeps or is on."""

    _model = WatchListModel
    prefix: ClassVar[Optional[str]] = WATCH_LISTS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Watch lists"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @property
    def _me(self) -> str:
        return str(self.requester.id)

    def _members(self, list_id: str) -> List[str]:
        return [
            str(m.user_id)
            for m in _rows(
                WatchListMemberModel,
                self.model_registry,
                lambda db: [db.list_id == list_id],
            )
        ]

    def _list(self, list_id: str) -> tuple[Any, Set[str]]:
        """The list and everyone on it, when the requester is; else 404."""
        found = _rows(
            WatchListModel, self.model_registry, lambda db: [db.id == list_id]
        )
        if not found:
            raise HTTPException(status_code=404, detail="Watch-list not found")
        everyone = {str(found[0].created_by_user_id), *self._members(list_id)}
        if self._me not in everyone:
            raise HTTPException(status_code=404, detail="Watch-list not found")
        return found[0], everyone

    def _kept(self, list_id: str) -> Any:
        watch_list, _ = self._list(list_id)
        if str(watch_list.created_by_user_id) != self._me:
            raise HTTPException(
                status_code=403, detail="Only the list's keeper changes who is on it"
            )
        return watch_list

    def _title(self, title_id: str) -> tuple[Any, Set[str]]:
        found = _rows(
            WatchListTitleModel, self.model_registry, lambda db: [db.id == title_id]
        )
        if not found:
            raise HTTPException(status_code=404, detail="Title not found")
        try:
            _, everyone = self._list(str(found[0].list_id))
        except HTTPException as err:
            raise HTTPException(status_code=404, detail="Title not found") from err
        return found[0], everyone

        titles = _rows(
            WatchListTitleModel, self.model_registry, lambda db: [db.list_id == list_id]
        )
        ids = [title.id for title in titles]
        wants: Dict[str, List[str]] = {}
        for want in _rows(
            WatchListWantModel, self.model_registry, lambda db: [db.title_id.in_(ids)]
        ):
            wants.setdefault(str(want.title_id), []).append(
                str(want.created_by_user_id)
            )
        return [
            TitleEntry(
                title_id=title.id,
                id=title.media_id,
                title=title.title,
                kind=title.kind,
                year=title.year,
                overview=title.overview,
                url=title.url,
                image=title.image,
                added_by=title.created_by_user_id,
                watched_on=title.watched_on,
                wanted_by=sorted(wants.get(title.id, [])),
            )
            for title in sorted(titles, key=lambda t: t.created_at or datetime.min)
        ]

    def _entry(self, watch_list: Any) -> WatchListEntry:
        return WatchListEntry(
            id=watch_list.id,
            name=watch_list.name,
            keeper_id=str(watch_list.created_by_user_id),
            member_ids=sorted(self._members(watch_list.id)),
            titles=self._titles(watch_list.id),
        )

    @custom_route(
        method="GET",
        path="/search",
        output_model=MediaList,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Titles in the catalogue matching a query",
        expose_in=(ExposeIn.REST,),
    )
    async def search_route(self, q: str, kind: Optional[str] = "movie") -> MediaList:
        if not q.strip() or len(q) > MAX_QUERY:
            raise HTTPException(
                status_code=422, detail=f"Search for 1 to {MAX_QUERY} characters"
            )
        if kind is not None and kind not in MEDIA_KINDS:
            raise HTTPException(
                status_code=422, detail=f"kind must be one of {', '.join(MEDIA_KINDS)}"
            )
        found = await _asked(EXT_Media.search_media(q, kind, SEARCH_RESULTS))
        return MediaList(results=[_item(item) for item in found])

    @custom_route(
        method="GET",
        path="/mine",
        output_model=WatchLists,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="The watch-lists you keep or are on",
        expose_in=(ExposeIn.REST,),
    )
    def mine_route(self) -> WatchLists:
        on = {
            str(m.list_id)
            for m in _rows(
                WatchListMemberModel,
                self.model_registry,
                lambda db: [db.user_id == self._me],
            )
        }
        lists = _rows(
            WatchListModel,
            self.model_registry,
            lambda db: [(db.created_by_user_id == self._me) | (db.id.in_(on))],
        )
        return WatchLists(
            lists=[self._entry(w) for w in sorted(lists, key=lambda w: w.name)]
        )

    @custom_route(
        method="POST",
        path="",
        input_model=NewList,
        output_model=WatchListEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Start a watch-list that you keep",
        expose_in=(ExposeIn.REST,),
    )
    def start_route(self, body: NewList) -> WatchListEntry:
        watch_list = _create(
            WatchListModel, self.model_registry, self._me, name=body.name
        )
        return self._entry(watch_list)

    @custom_route(
        method="POST",
        path="/{list_id}/members",
        input_model=NewMember,
        output_model=WatchListEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Share your list with someone",
        expose_in=(ExposeIn.REST,),
    )
    def add_member_route(self, list_id: str, body: NewMember) -> WatchListEntry:
        watch_list = self._kept(list_id)
        members = self._members(list_id)
        if body.user_id == self._me or body.user_id in members:
            raise HTTPException(status_code=409, detail="They are already on the list")
        if len(members) >= MAX_MEMBERS:
            raise HTTPException(
                status_code=422,
                detail=f"A list is shared with at most {MAX_MEMBERS} people",
            )
        _create(
            WatchListMemberModel,
            self.model_registry,
            _root(),
            list_id=list_id,
            user_id=body.user_id,
        )
        return self._entry(watch_list)

    @custom_route(
        method="POST",
        path="/{list_id}/members/{user_id}/remove",
        input_model=NoDetails,
        output_model=WatchListEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Take someone off your list, or leave someone else's",
        expose_in=(ExposeIn.REST,),
    )
    def remove_member_route(
        self, list_id: str, user_id: str, body: NoDetails
    ) -> WatchListEntry:
        watch_list, _ = self._list(list_id)
        if self._me not in (str(watch_list.created_by_user_id), user_id):
            raise HTTPException(
                status_code=403, detail="Only the list's keeper changes who is on it"
            )
        for member in _rows(
            WatchListMemberModel,
            self.model_registry,
            lambda db: [db.list_id == list_id, db.user_id == user_id],
        ):
            _delete(WatchListMemberModel, self.model_registry, member.id)
        return self._entry(watch_list)

    @custom_route(
        method="POST",
        path="/{list_id}/titles",
        input_model=NewTitle,
        output_model=TitleEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Add a title from the catalogue; you want to see it",
        expose_in=(ExposeIn.REST,),
    )
    async def add_title_route(self, list_id: str, body: NewTitle) -> TitleEntry:
        self._list(list_id)
        titles = _rows(
            WatchListTitleModel, self.model_registry, lambda db: [db.list_id == list_id]
        )
        if any(title.media_id == body.media_id for title in titles):
            raise HTTPException(
                status_code=409, detail="That title is already on the list"
            )
        if len(titles) >= MAX_TITLES:
            raise HTTPException(
                status_code=422, detail=f"A list holds at most {MAX_TITLES} titles"
            )
        found = _item(await _asked(EXT_Media.get_media_info(body.media_id)))
        title = _create(
            WatchListTitleModel,
            self.model_registry,
            self._me,
            list_id=list_id,
            media_id=found.id,
            title=found.title,
            kind=found.kind,
            year=found.year,
            overview=found.overview,
            url=found.url,
            image=found.image,
        )
        _create(WatchListWantModel, self.model_registry, self._me, title_id=title.id)
        return next(
            entry for entry in self._titles(list_id) if entry.title_id == title.id
        )

    @custom_route(
        method="POST",
        path="/titles/{title_id}/want",
        input_model=Want,
        output_model=TitleEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Say whether you want to see a title",
        expose_in=(ExposeIn.REST,),
    )
    def want_route(self, title_id: str, body: Want) -> TitleEntry:
        title, _ = self._title(title_id)
        mine = _rows(
            WatchListWantModel,
            self.model_registry,
            lambda db: [db.title_id == title_id, db.created_by_user_id == self._me],
        )
        if body.want and not mine:
            _create(
                WatchListWantModel, self.model_registry, self._me, title_id=title_id
            )
        if not body.want:
            for want in mine:
                _delete(WatchListWantModel, self.model_registry, want.id)
        return next(
            entry
            for entry in self._titles(str(title.list_id))
            if entry.title_id == title_id
        )

    @custom_route(
        method="POST",
        path="/titles/{title_id}/watched",
        input_model=Watched,
        output_model=TitleEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Mark a title watched, or not yet",
        expose_in=(ExposeIn.REST,),
    )
    def watched_route(self, title_id: str, body: Watched) -> TitleEntry:
        title, _ = self._title(title_id)
        db = _db(WatchListTitleModel, self.model_registry)
        db.update(
            requester_id=_root(),
            model_registry=self.model_registry,
            new_properties={
                "watched_on": (
                    datetime.now(timezone.utc).date() if body.watched else None
                )
            },
            filters=[db.id == title_id],
        )
        return next(
            entry
            for entry in self._titles(str(title.list_id))
            if entry.title_id == title_id
        )

    @custom_route(
        method="POST",
        path="/titles/{title_id}/remove",
        input_model=NoDetails,
        output_model=WatchListEntry,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Take a title off the list (its keeper, or whoever added it)",
        expose_in=(ExposeIn.REST,),
    )
    def remove_title_route(self, title_id: str, body: NoDetails) -> WatchListEntry:
        title, _ = self._title(title_id)
        watch_list, _ = self._list(str(title.list_id))
        if self._me not in (
            str(watch_list.created_by_user_id),
            str(title.created_by_user_id),
        ):
            raise HTTPException(
                status_code=403,
                detail="The keeper, or whoever added it, takes a title off",
            )
        for want in _rows(
            WatchListWantModel,
            self.model_registry,
            lambda db: [db.title_id == title_id],
        ):
            _delete(WatchListWantModel, self.model_registry, want.id)
        _delete(WatchListTitleModel, self.model_registry, title_id)
        return self._entry(watch_list)

    @custom_route(
        method="GET",
        path="/{list_id}/pick",
        output_model=Pick,
        authentication_type="jwt",
        openapi_tags=("Watch lists",),
        summary="Tonight's pick: something everyone on the list wants to see",
        expose_in=(ExposeIn.REST,),
    )
    def pick_route(self, list_id: str) -> Pick:
        watch_list, everyone = self._list(list_id)
        return tonight(
            self._titles(list_id), len(everyone), watch_list.id, date.today()
        )


WatchListModel.Manager = WatchListManager
WatchListMemberModel.Manager = WatchListManager
WatchListTitleModel.Manager = WatchListManager
WatchListWantModel.Manager = WatchListManager


    """A deleted account's lists go with their titles, members and wants;
    it leaves the lists it was on, and its wants go. Titles it added to
    others' lists stay theirs. Safe to run twice."""
    kept = [
        w.id
        for w in _rows(
            WatchListModel,
            model_registry,
            lambda db: [db.created_by_user_id == user_id],
        )
    ]
    titles = [
        t.id
        for t in _rows(
            WatchListTitleModel, model_registry, lambda db: [db.list_id.in_(kept)]
        )
    ]
    doomed = [
        (
            WatchListWantModel,
            lambda db: [(db.title_id.in_(titles)) | (db.created_by_user_id == user_id)],
        ),
        (WatchListTitleModel, lambda db: [db.list_id.in_(kept)]),
        (
            WatchListMemberModel,
            lambda db: [(db.list_id.in_(kept)) | (db.user_id == user_id)],
        ),
        (WatchListModel, lambda db: [db.created_by_user_id == user_id]),
    ]
    for model, conditions in doomed:
        for row in _rows(model, model_registry, conditions):
            _delete(model, model_registry, row.id)


register_eraser("media", erase_watch_lists)

ALL_MODELS: List[type] = [
    WatchListModel,
    WatchListMemberModel,
    WatchListTitleModel,
    WatchListWantModel,
]
