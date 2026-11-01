# SPDX-License-Identifier: AGPL-3.0-or-later
"""A repeating rota of nights, and swaps both people agree to.

A rota belongs to the account that keeps it (its owner): a cycle of
``cycle_days`` nights from ``starts_on``, each night with one account (a
``RotaNight``). Only the owner sets the nights. Everyone with a night in
the rota, and the owner, can read it and its schedule.

A swap hands one dated night from the person it is with to another member.
It needs both of them: whichever of the two asks, the other answers, and
until they accept the night stays where it was.

Routes: the rota and its nights are the owner's (``/v1/rota``,
``/v1/rota_night``); members use ``/v1/rotas``: ``GET /mine`` (the rotas
they keep or are in), ``GET /{rota_id}/schedule`` (who each night is with,
swaps applied), ``GET``/``POST /{rota_id}/swaps`` and
``POST /swaps/{swap_id}/answer``.
"""

from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Dict, List, Literal, Optional, Set, Type

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
from zephyrex.logic.AbstractLogicManager.ownership import each_created
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

MAX_CYCLE_DAYS = 28
MAX_SCHEDULE_DAYS = 62
PENDING, ACCEPTED, DECLINED = "pending", "accepted", "declined"
SwapStatus = Literal["pending", "accepted", "declined"]


class RotaModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["RotaManager"]]
    name: str = Field(..., description="What the rota is called, e.g. 'Nights'")
    cycle_days: int = Field(..., description="How many nights before it repeats")
    starts_on: date = Field(..., description="The first night of the first cycle")

    table_comment: ClassVar[str] = "Repeating rotas of nights, kept by their owner"

    class Create(BaseModel):
        name: str = Field(..., min_length=1, max_length=100)
        cycle_days: int = Field(..., ge=1, le=MAX_CYCLE_DAYS)
        starts_on: date

    class Update(BaseModel):
        name: Optional[str] = Field(None, min_length=1, max_length=100)
        cycle_days: Optional[int] = Field(None, ge=1, le=MAX_CYCLE_DAYS)
        starts_on: Optional[date] = None

    class Search(ApplicationModel.Search):
        name: Optional[StringSearchModel] = None


class RotaNightModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["RotaNightManager"]]
    rota_id: str = Field(..., description="The rota")
    day_index: int = Field(..., description="Which night of the cycle, from 0")
    user_id: str = Field(..., description="The account that night is with")

    table_comment: ClassVar[str] = "Who each night of a rota's cycle is with"

    class Create(BaseModel):
        rota_id: str
        day_index: int = Field(..., ge=0, lt=MAX_CYCLE_DAYS)
        user_id: str

    class Update(BaseModel):
        user_id: Optional[str] = None

    class Search(ApplicationModel.Search):
        rota_id: Optional[StringSearchModel] = None


class RotaSwapModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    Manager: ClassVar[Type["RotaSwapManager"]]
    rota_id: str = Field(..., description="The rota")
    night: date = Field(..., description="The night swapped")
    from_user_id: str = Field(..., description="Whom the night is with before the swap")
    to_user_id: str = Field(..., description="Whom it would be with after")
    status: str = Field(PENDING, description="pending, accepted or declined")

    table_comment: ClassVar[str] = (
        "One-off swaps of a rota's night, agreed by both people"
    )

    class Create(BaseModel):
        rota_id: str
        night: date
        from_user_id: str
        to_user_id: str
        status: str = PENDING

    class Update(BaseModel):
        status: Optional[str] = None

    class Search(ApplicationModel.Search):
        rota_id: Optional[StringSearchModel] = None


class NightEntry(ResponseModel):
    day_index: int
    user_id: str


class RotaEntry(ResponseModel):
    id: str
    name: str
    cycle_days: int
    starts_on: date
    owner_id: str
    nights: List[NightEntry]


class RotaList(ResponseModel):
    rotas: List[RotaEntry]


class ScheduledNight(ResponseModel):
    night: date
    user_id: Optional[str] = Field(
        None, description="Whom it is with; none when the cycle leaves it open"
    )
    swapped: bool = False


class Schedule(ResponseModel):
    rota_id: str
    nights: List[ScheduledNight]


class SwapEntry(ResponseModel):
    id: str
    night: date
    from_user_id: str
    to_user_id: str
    status: str
    requested_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SwapList(ResponseModel):
    swaps: List[SwapEntry]


class SwapRequest(ResponseModel):
    night: date
    to_user_id: str


class SwapAnswer(ResponseModel):
    accept: bool


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


def _rota(rota_id: str, model_registry: Any) -> Any:
    found = _rows(RotaModel, model_registry, lambda db: [db.id == rota_id])
    return found[0] if found else None


def _nights(rota_id: str, model_registry: Any) -> List[Any]:
    return _rows(RotaNightModel, model_registry, lambda db: [db.rota_id == rota_id])


def members(rota: Any, nights: List[Any]) -> Set[str]:
    """Who may read a rota: its owner and everyone with a night in it."""
    return {str(rota.created_by_user_id)} | {str(night.user_id) for night in nights}


def scheduled(
    starts_on: date,
    cycle_days: int,
    nights: List[Any],
    swaps: List[Any],
    start: date,
    days: int,
) -> List[ScheduledNight]:
    """Who each night from ``start`` is with, for ``days`` nights: the cycle's
    night, unless an accepted swap of that night moved it."""
    by_index = {night.day_index: str(night.user_id) for night in nights}
    accepted = {
        (swap.night, str(swap.from_user_id)): str(swap.to_user_id)
        for swap in sorted(
            swaps, key=lambda swap: swap.updated_at or swap.created_at or datetime.min
        )
        if swap.status == ACCEPTED
    }
    out = []
    for offset in range(days):
        night = start + timedelta(days=offset)
        base = (
            by_index.get((night - starts_on).days % cycle_days)
            if night >= starts_on
            else None
        )
        moved = accepted.get((night, base)) if base is not None else None
        out.append(
            ScheduledNight(
                night=night, user_id=moved or base, swapped=moved is not None
            )
        )
    return out


def _swap_entry(swap: Any) -> SwapEntry:
    return SwapEntry(
        id=swap.id,
        night=swap.night,
        from_user_id=swap.from_user_id,
        to_user_id=swap.to_user_id,
        status=swap.status,
        requested_by=swap.created_by_user_id,
        created_at=swap.created_at,
        updated_at=swap.updated_at,
    )


class RotaManager(AbstractBLLManager, RouterMixin):
    """``/v1/rota``: the owner's own rotas."""

    _model = RotaModel


class RotaNightManager(AbstractBLLManager, RouterMixin):
    """``/v1/rota_night``: the nights of the owner's rotas; one per night of the cycle."""

    _model = RotaNightModel

    def create(self, **kwargs: Any) -> Any:
        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            rota = _rota(str(fields.get("rota_id")), self.model_registry)
            if rota is None or str(rota.created_by_user_id) != str(self.requester.id):
                raise HTTPException(status_code=404, detail="Rota not found")
            if int(fields.get("day_index", -1)) >= rota.cycle_days:
                raise HTTPException(
                    status_code=422, detail="That night is outside the rota's cycle"
                )
            taken = [
                n
                for n in _nights(rota.id, self.model_registry)
                if n.day_index == fields["day_index"]
            ]
            if taken:
                raise HTTPException(
                    status_code=409,
                    detail="That night already has someone; change it instead",
                )
            return fields

        return super().create(**each_created(kwargs, owned))


class RotaSwapManager(AbstractBLLManager, RouterMixin):
    """``/v1/rotas``: what members of a rota see and do."""

    _model = RotaSwapModel
    prefix: ClassVar[Optional[str]] = "/v1/rotas"
    tags: ClassVar[Optional[List[str]]] = ["Rota"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def _member_rota(self, rota_id: str) -> tuple[Any, List[Any]]:
        """The rota and its nights, when the requester may read it; else 404."""
        rota = _rota(rota_id, self.model_registry)
        if rota is None:
            raise HTTPException(status_code=404, detail="Rota not found")
        nights = _nights(rota.id, self.model_registry)
        if str(self.requester.id) not in members(rota, nights):
            raise HTTPException(status_code=404, detail="Rota not found")
        return rota, nights

    def _swaps(self, rota_id: str) -> List[Any]:
        return _rows(
            RotaSwapModel, self.model_registry, lambda db: [db.rota_id == rota_id]
        )

    @custom_route(
        method="GET",
        path="/mine",
        output_model=RotaList,
        authentication_type="jwt",
        openapi_tags=("Rota",),
        summary="The rotas you keep, and those you have a night in",
        expose_in=(ExposeIn.REST,),
    )
    def mine_route(self) -> RotaList:
        me = str(self.requester.id)
        mine = {
            night.rota_id
            for night in _rows(
                RotaNightModel, self.model_registry, lambda db: [db.user_id == me]
            )
        }
        rotas = _rows(
            RotaModel,
            self.model_registry,
            lambda db: [(db.created_by_user_id == me) | (db.id.in_(mine))],
        )
        return RotaList(
            rotas=[
                RotaEntry(
                    id=rota.id,
                    name=rota.name,
                    cycle_days=rota.cycle_days,
                    starts_on=rota.starts_on,
                    owner_id=str(rota.created_by_user_id),
                    nights=[
                        NightEntry(day_index=night.day_index, user_id=night.user_id)
                        for night in sorted(
                            _nights(rota.id, self.model_registry),
                            key=lambda n: n.day_index,
                        )
                    ],
                )
                for rota in sorted(rotas, key=lambda r: r.name)
            ]
        )

    @custom_route(
        method="GET",
        path="/{rota_id}/schedule",
        output_model=Schedule,
        authentication_type="jwt",
        openapi_tags=("Rota",),
        summary="Who each night is with, swaps applied",
        expose_in=(ExposeIn.REST,),
    )
    def schedule_route(
        self, rota_id: str, start: Optional[date] = None, days: int = 14
    ) -> Schedule:
        rota, nights = self._member_rota(rota_id)
        days = max(1, min(days, MAX_SCHEDULE_DAYS))
        first = start or date.today()
        return Schedule(
            rota_id=rota.id,
            nights=scheduled(
                rota.starts_on,
                rota.cycle_days,
                nights,
                self._swaps(rota.id),
                first,
                days,
            ),
        )

    @custom_route(
        method="GET",
        path="/{rota_id}/swaps",
        output_model=SwapList,
        authentication_type="jwt",
        openapi_tags=("Rota",),
        summary="The rota's swaps, asked and answered",
        expose_in=(ExposeIn.REST,),
    )
    def swaps_route(self, rota_id: str) -> SwapList:
        rota, _ = self._member_rota(rota_id)
        swaps = sorted(
            self._swaps(rota.id),
            key=lambda swap: (swap.night, swap.created_at or datetime.min),
        )
        return SwapList(swaps=[_swap_entry(swap) for swap in swaps])

    @custom_route(
        method="POST",
        path="/{rota_id}/swaps",
        input_model=SwapRequest,
        output_model=SwapEntry,
        authentication_type="jwt",
        openapi_tags=("Rota",),
        summary="Ask to hand one night to another member; the other person answers",
        expose_in=(ExposeIn.REST,),
    )
    def ask_route(self, rota_id: str, body: SwapRequest) -> SwapEntry:
        rota, nights = self._member_rota(rota_id)
        me = str(self.requester.id)
        (planned,) = scheduled(
            rota.starts_on, rota.cycle_days, nights, self._swaps(rota.id), body.night, 1
        )
        if planned.user_id is None:
            raise HTTPException(status_code=422, detail="Nobody has that night")
        if (
            body.to_user_id not in members(rota, nights)
            or body.to_user_id == planned.user_id
        ):
            raise HTTPException(
                status_code=422,
                detail="A night is handed to another member of the rota",
            )
        if me not in (planned.user_id, body.to_user_id):
            raise HTTPException(
                status_code=403, detail="Only the two people swapping can ask"
            )
        swap = self.create(
            rota_id=rota.id,
            night=body.night,
            from_user_id=planned.user_id,
            to_user_id=body.to_user_id,
            status=PENDING,
        )
        return _swap_entry(swap)

    @custom_route(
        method="POST",
        path="/swaps/{swap_id}/answer",
        input_model=SwapAnswer,
        output_model=SwapEntry,
        authentication_type="jwt",
        openapi_tags=("Rota",),
        summary="Accept or decline a swap you were asked for",
        expose_in=(ExposeIn.REST,),
    )
    def answer_route(self, swap_id: str, body: SwapAnswer) -> SwapEntry:
        found = _rows(RotaSwapModel, self.model_registry, lambda db: [db.id == swap_id])
        if not found:
            raise HTTPException(status_code=404, detail="Swap not found")
        swap = found[0]
        me = str(self.requester.id)
        the_two = {str(swap.from_user_id), str(swap.to_user_id)}
        if me not in the_two:
            raise HTTPException(status_code=404, detail="Swap not found")
        if me == str(swap.created_by_user_id):
            raise HTTPException(
                status_code=403, detail="The other person answers a swap you asked for"
            )
        if swap.status != PENDING:
            raise HTTPException(status_code=409, detail="That swap has been answered")
        db = RotaSwapModel.DB(self.model_registry.DB.manager.Base)
        answered = db.update(
            requester_id=_root(),
            model_registry=self.model_registry,
            new_properties={"status": ACCEPTED if body.accept else DECLINED},
            filters=[db.id == swap.id],
            return_type="dto",
            override_dto=RotaSwapModel,
        )
        return _swap_entry(answered)


RotaModel.Manager = RotaManager
RotaNightModel.Manager = RotaNightManager
RotaSwapModel.Manager = RotaSwapManager


def erase_rota(user_id: str, model_registry: Any) -> None:
    """A deleted account's rotas go, with their nights and swaps; its nights
    in other people's rotas are left open, and its swaps go. Safe to run twice."""
    base = model_registry.DB.manager.Base
    owned = [
        rota.id
        for rota in _rows(
            RotaModel, model_registry, lambda db: [db.created_by_user_id == user_id]
        )
    ]
    doomed = [
        (
            RotaNightModel,
            lambda db: [(db.rota_id.in_(owned)) | (db.user_id == user_id)],
        ),
        (
            RotaSwapModel,
            lambda db: [
                (db.rota_id.in_(owned))
                | (db.from_user_id == user_id)
                | (db.to_user_id == user_id)
            ],
        ),
        (RotaModel, lambda db: [db.created_by_user_id == user_id]),
    ]
    for model, conditions in doomed:
        db = model.DB(base)
        for row in _rows(model, model_registry, conditions):
            db.delete(
                requester_id=_root(),
                model_registry=model_registry,
                filters=[db.id == row.id],
            )


register_eraser("rota", erase_rota)

ALL_MODELS: List[type] = [RotaModel, RotaNightModel, RotaSwapModel]
