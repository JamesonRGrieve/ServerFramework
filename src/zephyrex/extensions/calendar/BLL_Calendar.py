# SPDX-License-Identifier: AGPL-3.0-or-later
"""A user's own calendars, shared availability, and booking a time together.

Each user connects their own calendar accounts (``CalendarAccountModel``):
a CalDAV calendar (its URL, sign-in name and password, the password kept
encrypted), or their Google or Microsoft calendar through the account they
linked with ``oauth_consumer`` (whose sign-in must grant calendar access).
The calendar providers run on each account through ``connected_account``.

A user shares their availability with chosen people
(``AvailabilityShareModel``): those people see when they are busy, never
what their events are, and, when ``can_book`` allows it, may put an event
into their calendars. Finding free time takes the requester and people who
share with them, and answers the slots every one of them is free; booking
writes the event into the requester's calendars and into the calendars of
each person who lets them book.

Routes on ``/v1/calendars``: ``GET``/``POST /accounts``, ``POST
/accounts/{id}/remove``; ``GET``/``POST /shares``, ``POST
/shares/{viewer_user_id}/remove``, ``GET /shared-with-me``; ``POST /free``
and ``POST /book``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Type

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.calendar.EXT_Calendar import (
    MAX_DURATION_MINUTES,
    MAX_SLOT_DAYS,
    AbstractCalendarProvider,
    EXT_Calendar,
    Interval,
    aware,
    clock,
    connected_account,
    event_fields,
    free_slots,
    iso,
    zone,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
)
from zephyrex.extensions.privacy.Erasure import register_eraser
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
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

CALENDARS_PREFIX = "/v1/calendars"
AccountProvider = Literal["caldav", "google_calendar", "microsoft_calendar"]
# A delegated account: the identity provider it signs in through, and the
# scope its sign-in must have granted.
DELEGATED: Dict[str, Tuple[str, str]] = {
    "google_calendar": ("google", "https://www.googleapis.com/auth/calendar.events"),
    "microsoft_calendar": ("microsoft", "Calendars.ReadWrite"),
}
MAX_ACCOUNTS = 10
MAX_PEOPLE = 12
MAX_TEXT = 200
# How far ahead a new account is read to check it answers.
CHECK_WINDOW = timedelta(days=1)


    Manager: ClassVar[Type["CalendarManager"]]
    provider: str = Field(
        ..., description="caldav, google_calendar or microsoft_calendar"
    )
    label: str = Field(..., description="What the user calls it")
    calendar_url: Optional[str] = Field(
        None, description="CalDAV: the calendar collection's URL"
    )
    username: Optional[str] = Field(None, description="CalDAV: the sign-in name")
    secret: Optional[str] = Field(
        None, description="CalDAV: the password, encrypted; never returned"
    )
    calendar_id: Optional[str] = Field(
        None, description="Google: which calendar (primary by default)"
    )

    table_comment: ClassVar[str] = "Each user's own calendar accounts"

    class Create(BaseModel):
        provider: str
        label: str
        calendar_url: Optional[str] = None
        username: Optional[str] = None
        secret: Optional[str] = None
        calendar_id: Optional[str] = None

    class Update(BaseModel):
        label: Optional[str] = None

    class Search(ApplicationModel.Search):
        provider: Optional[StringSearchModel] = None


    Manager: ClassVar[Type["CalendarManager"]]
    viewer_user_id: str = Field(..., description="Who sees when the owner is busy")
    can_book: bool = Field(
        False, description="Whether they may put events into the owner's calendars"
    )

    table_comment: ClassVar[str] = "Whom each user shares their availability with"

    class Create(BaseModel):
        viewer_user_id: str
        can_book: bool = False

    class Update(BaseModel):
        can_book: Optional[bool] = None

    class Search(ApplicationModel.Search):
        viewer_user_id: Optional[StringSearchModel] = None


class AccountView(ResponseModel):
    id: str
    provider: str
    label: str
    calendar_url: Optional[str] = None
    username: Optional[str] = None
    calendar_id: Optional[str] = None


class AccountList(ResponseModel):
    accounts: List[AccountView]


class NewAccount(ResponseModel):
    provider: AccountProvider
    label: str = Field(..., min_length=1, max_length=MAX_TEXT)
    calendar_url: Optional[str] = Field(None, max_length=2_000)
    username: Optional[str] = Field(None, max_length=MAX_TEXT)
    password: Optional[str] = Field(None, max_length=MAX_TEXT)
    calendar_id: Optional[str] = Field(None, max_length=MAX_TEXT)


class ShareView(ResponseModel):
    user_id: str
    can_book: bool


class ShareList(ResponseModel):
    shares: List[ShareView]


class NewShare(ResponseModel):
    viewer_user_id: str = Field(..., min_length=1)
    can_book: bool = False


class NoDetails(ResponseModel):
    """The body of an action that needs nothing more than its path."""


class FreeRequest(ResponseModel):
    user_ids: List[str] = Field(default_factory=list, max_length=MAX_PEOPLE)
    first_day: date
    days: int = Field(7, ge=1, le=MAX_SLOT_DAYS)
    duration_minutes: int = Field(120, ge=15, le=MAX_DURATION_MINUTES)
    day_start: str = "18:00"
    day_end: str = "23:00"
    timezone: str = "UTC"


class Slot(ResponseModel):
    start: str
    end: str


class PersonRead(ResponseModel):
    user_id: str
    calendars: int = Field(..., description="How many of their calendars were read")
    unreadable: int = Field(0, description="How many of their calendars did not answer")


class FreeTime(ResponseModel):
    slots: List[Slot]
    people: List[PersonRead]


class BookRequest(ResponseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TEXT)
    start: datetime
    end: datetime
    location: Optional[str] = Field(None, max_length=MAX_TEXT)
    description: Optional[str] = Field(None, max_length=2_000)
    user_ids: List[str] = Field(default_factory=list, max_length=MAX_PEOPLE)


class Booked(ResponseModel):
    booked: List[str] = Field(..., description="Whose calendars the event went into")
    not_booked: List[str] = Field(
        ...,
        description="People who do not let the requester book for them, or have no calendar",
    )
    failed: List[str] = Field(
        default_factory=list, description="People whose calendar refused it"
    )


def _root() -> str:
    return str(env("ROOT_ID"))


def _rows(model: Any, model_registry: Any, conditions: Any) -> List[Any]:
    db = model.DB(model_registry.DB.manager.Base)
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


def _delete(model: Any, model_registry: Any, row_id: str) -> None:
    db = model.DB(model_registry.DB.manager.Base)
    db.delete(
        requester_id=_root(), model_registry=model_registry, filters=[db.id == row_id]
    )


def provider_class(name: str) -> Type[AbstractCalendarProvider]:
    for provider in EXT_Calendar.providers:
        if provider.name == name and issubclass(provider, AbstractCalendarProvider):
            return provider
    raise HTTPException(status_code=422, detail=f"No calendar service called {name!r}")


    """What the provider reads for ``account``: CalDAV's own settings, or a
    current access token from the owner's linked sign-in."""
    if account.provider == "caldav":
        return {
            "calendar_url": str(account.calendar_url or ""),
            "username": str(account.username or ""),
            "api_key": str(decrypt_secret(account.secret) or ""),
        }
    from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import (
        delegated_access_token,
    )

    identity_provider, scope = DELEGATED[account.provider]
    token = await delegated_access_token(
        str(account.created_by_user_id),
        identity_provider,
        scope,
        provider_class(account.provider).friendly_name,
        model_registry,
    )
    settings = {"api_key": token}
    if account.calendar_id:
        settings["calendar_id"] = str(account.calendar_id)
    return settings


async def on_account(account: Any, model_registry: Any, method: str, *args: Any) -> Any:
    """``method`` of the account's provider, run on the account."""
    provider = provider_class(account.provider)
    settings = await account_settings(account, model_registry)
    with connected_account(settings) as instance:
        return await getattr(provider, method)(instance, *args)


def merged(busy: List[Interval]) -> List[Interval]:
    """``busy`` as the fewest intervals covering the same time."""
    out: List[Interval] = []
    for start, end in sorted(busy):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _view(account: Any) -> AccountView:
    return AccountView(
        id=account.id,
        provider=account.provider,
        label=account.label,
        calendar_url=account.calendar_url,
        username=account.username,
        calendar_id=account.calendar_id,
    )


    """``/v1/calendars``: a user's own calendars, whom they share their
    availability with, and finding and booking a time together."""

    _model = CalendarAccountModel
    prefix: ClassVar[Optional[str]] = CALENDARS_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Calendars"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @property
    def _me(self) -> str:
        return str(self.requester.id)

    def _accounts(self, user_id: str) -> List[Any]:
        return _rows(
            CalendarAccountModel,
            self.model_registry,
            lambda db: [db.created_by_user_id == user_id],
        )

    def _shares_to_me(self) -> Dict[str, bool]:
        """Who shares their availability with the requester, and whether
        they let the requester book."""
        return {
            str(share.created_by_user_id): bool(share.can_book)
            for share in _rows(
                AvailabilityShareModel,
                self.model_registry,
                lambda db: [db.viewer_user_id == self._me],
            )
        }

    def _people(self, user_ids: List[str]) -> List[str]:
        """The requester and ``user_ids``, each of whom must share with them."""
        sharing = self._shares_to_me()
        strangers = [
            user for user in user_ids if user != self._me and user not in sharing
        ]
        if strangers:
            raise HTTPException(
                status_code=403,
                detail="Some of them do not share their availability with you",
            )
        return list(dict.fromkeys([self._me, *user_ids]))

    @custom_route(
        method="GET",
        path="/accounts",
        output_model=AccountList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Your calendar accounts (never their passwords)",
        expose_in=(ExposeIn.REST,),
    )
    def accounts_route(self) -> AccountList:
        return self._account_list()

    def _account_list(self) -> AccountList:
        return AccountList(
            accounts=[
                _view(a)
                for a in sorted(self._accounts(self._me), key=lambda a: a.label)
            ]
        )

        if len(self._accounts(self._me)) >= MAX_ACCOUNTS:
            raise HTTPException(
                status_code=422, detail=f"At most {MAX_ACCOUNTS} calendars"
            )
        if body.provider == "caldav" and not (
            body.calendar_url and body.username and body.password
        ):
            raise HTTPException(
                status_code=422,
                detail="A CalDAV calendar needs its URL, sign-in name and password",
            )
        fields: Dict[str, Any] = {
            "provider": body.provider,
            "label": body.label,
            "calendar_url": body.calendar_url if body.provider == "caldav" else None,
            "username": body.username if body.provider == "caldav" else None,
            "secret": (
                encrypt_secret(body.password) if body.provider == "caldav" else None
            ),
            "calendar_id": (
                body.calendar_id if body.provider == "google_calendar" else None
            ),
        }
        draft = CalendarAccountModel.model_construct(
            created_by_user_id=self._me, **fields
        )
        now = datetime.now(UTC)
        try:
            await on_account(
                draft, self.model_registry, "busy", now, now + CHECK_WINDOW
            )
        except AuthExternalError as err:
            raise HTTPException(
                status_code=422, detail="The calendar refused the sign-in"
            ) from err
        except BaseExternalError as err:
            logger.info(
                "calendar: a new %s account did not answer: %s", body.provider, err
            )
            raise HTTPException(
                status_code=422, detail="The calendar did not answer; check its address"
            ) from err
        account = CalendarAccountModel.DB(self.model_registry.DB.manager.Base).create(
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=CalendarAccountModel,
            **fields,
        )
        return _view(account)

    @custom_route(
        method="POST",
        path="/accounts/{account_id}/remove",
        input_model=NoDetails,
        output_model=AccountList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Disconnect a calendar of yours",
        expose_in=(ExposeIn.REST,),
    )
    def disconnect_route(self, account_id: str, body: NoDetails) -> AccountList:
        if not any(a.id == account_id for a in self._accounts(self._me)):
            raise HTTPException(status_code=404, detail="Calendar not found")
        _delete(CalendarAccountModel, self.model_registry, account_id)
        return self._account_list()

    @custom_route(
        method="GET",
        path="/shares",
        output_model=ShareList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Whom you share your availability with",
        expose_in=(ExposeIn.REST,),
    )
    def shares_route(self) -> ShareList:
        return self._share_list()

    def _share_list(self) -> ShareList:
        shares = _rows(
            AvailabilityShareModel,
            self.model_registry,
            lambda db: [db.created_by_user_id == self._me],
        )
        return ShareList(
            shares=[
                ShareView(user_id=s.viewer_user_id, can_book=bool(s.can_book))
                for s in shares
            ]
        )

    @custom_route(
        method="POST",
        path="/shares",
        input_model=NewShare,
        output_model=ShareList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Share when you are busy with someone, and whether they may book you",
        expose_in=(ExposeIn.REST,),
    )
    def share_route(self, body: NewShare) -> ShareList:
        if body.viewer_user_id == self._me:
            raise HTTPException(
                status_code=422, detail="Your availability is already yours"
            )
        db = AvailabilityShareModel.DB(self.model_registry.DB.manager.Base)
        existing = _rows(
            AvailabilityShareModel,
            self.model_registry,
            lambda d: [
                d.created_by_user_id == self._me,
                d.viewer_user_id == body.viewer_user_id,
            ],
        )
        if existing:
            db.update(
                requester_id=_root(),
                model_registry=self.model_registry,
                new_properties={"can_book": body.can_book},
                filters=[db.id == existing[0].id],
            )
        else:
            db.create(
                requester_id=self._me,
                model_registry=self.model_registry,
                viewer_user_id=body.viewer_user_id,
                can_book=body.can_book,
            )
        return self._share_list()

    @custom_route(
        method="POST",
        path="/shares/{viewer_user_id}/remove",
        input_model=NoDetails,
        output_model=ShareList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Stop sharing your availability with someone",
        expose_in=(ExposeIn.REST,),
    )
    def unshare_route(self, viewer_user_id: str, body: NoDetails) -> ShareList:
        for share in _rows(
            AvailabilityShareModel,
            self.model_registry,
            lambda db: [
                db.created_by_user_id == self._me,
                db.viewer_user_id == viewer_user_id,
            ],
        ):
            _delete(AvailabilityShareModel, self.model_registry, share.id)
        return self._share_list()

    @custom_route(
        method="GET",
        path="/shared-with-me",
        output_model=ShareList,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Who shares their availability with you, and who lets you book them",
        expose_in=(ExposeIn.REST,),
    )
    def shared_with_me_route(self) -> ShareList:
        return ShareList(
            shares=[
                ShareView(user_id=u, can_book=b)
                for u, b in sorted(self._shares_to_me().items())
            ]
        )

    async def _busy(
        self, user_id: str, start: datetime, end: datetime
    ) -> Tuple[List[Interval], PersonRead]:
        accounts = self._accounts(user_id)
        found = await asyncio.gather(
            *(on_account(a, self.model_registry, "busy", start, end) for a in accounts),
            return_exceptions=True,
        )
        busy: List[Interval] = []
        unreadable = 0
        for account, answer in zip(accounts, found):
            if isinstance(answer, (BaseExternalError, HTTPException)):
                logger.info(
                    "calendar: %s's %s did not answer: %s",
                    user_id,
                    account.provider,
                    answer,
                )
                unreadable += 1
            elif isinstance(answer, BaseException):
                raise answer
            else:
                busy.extend(answer)
        return busy, PersonRead(
            user_id=user_id, calendars=len(accounts) - unreadable, unreadable=unreadable
        )

    @custom_route(
        method="POST",
        path="/free",
        input_model=FreeRequest,
        output_model=FreeTime,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Times you and the people chosen are all free (only busy times are read)",
        expose_in=(ExposeIn.REST,),
    )
    async def free_route(self, body: FreeRequest) -> FreeTime:
        people = self._people(body.user_ids)
        try:
            where, opens, closes = (
                zone(body.timezone),
                clock(body.day_start),
                clock(body.day_end),
            )
        except InvalidInputExternalError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        if closes <= opens:
            raise HTTPException(
                status_code=422, detail="The day's window must end after it starts"
            )
        start = aware(datetime.combine(body.first_day, opens, where))
        end = aware(
            datetime.combine(
                body.first_day + timedelta(days=body.days - 1), closes, where
            )
        )
        read = await asyncio.gather(*(self._busy(user, start, end) for user in people))
        busy = merged([interval for intervals, _ in read for interval in intervals])
        slots = free_slots(
            busy,
            body.first_day,
            body.days,
            opens,
            closes,
            timedelta(minutes=body.duration_minutes),
            timedelta(0),
            where,
        )
        now = datetime.now(UTC)
        return FreeTime(
            slots=[Slot(start=iso(a), end=iso(b)) for a, b in slots if a >= now],
            people=[person for _, person in read],
        )

    @custom_route(
        method="POST",
        path="/book",
        input_model=BookRequest,
        output_model=Booked,
        authentication_type="jwt",
        openapi_tags=("Calendars",),
        summary="Put an event into your calendars and into those of the people who let you book them",
        expose_in=(ExposeIn.REST,),
    )
    async def book_route(self, body: BookRequest) -> Booked:
        people = self._people(body.user_ids)
        try:
            fields = event_fields(
                body.title,
                body.start,
                body.end,
                body.location,
                body.description,
                None,
                None,
            )
        except InvalidInputExternalError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
        lets_me = self._shares_to_me()
        bookable = [
            user for user in people if user == self._me or lets_me.get(user, False)
        ]
        booked: List[str] = []
        failed: List[str] = []
        not_booked = [user for user in people if user not in bookable]
        for user in bookable:
            accounts = self._accounts(user)
            if not accounts:
                not_booked.append(user)
                continue
            # Into the first of their calendars: one copy of the date each.
            try:
                await on_account(
                    accounts[0], self.model_registry, "create_event", fields
                )
                booked.append(user)
            except (BaseExternalError, HTTPException) as err:
                logger.info(
                    "calendar: booking into %s's calendar failed: %s", user, err
                )
                failed.append(user)
        return Booked(booked=booked, not_booked=not_booked, failed=failed)


CalendarAccountModel.Manager = CalendarManager
AvailabilityShareModel.Manager = CalendarManager


def erase_calendars(user_id: str, model_registry: Any) -> None:
    """A deleted account's calendar connections and availability shares,
    both ways, go. Safe to run twice."""
    for model, conditions in (
        (CalendarAccountModel, lambda db: [db.created_by_user_id == user_id]),
        (
            AvailabilityShareModel,
            lambda db: [
                (db.created_by_user_id == user_id) | (db.viewer_user_id == user_id)
            ],
        ),
    ):
        for row in _rows(model, model_registry, conditions):
            _delete(model, model_registry, row.id)


register_eraser("calendar", erase_calendars)

ALL_MODELS: List[type] = [CalendarAccountModel, AvailabilityShareModel]
