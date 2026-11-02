# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a user may do: their plan, from their payment subscriptions and the
seats others share with them, and the checks that refuse the rest (402).

A user's plan is the best of: the default (free) plan; the plan each of
their active subscriptions buys; and the plan of each subscriber who gave
them a seat, while that subscriber's plan still has the seat to give
(seats are honoured in the order they were given, so a downgrade drops the
newest first). Seats do not chain: a seat holder cannot give seats on.

Routes on ``/v1/entitlement``: ``GET`` the requester's plan, features,
limits and seats; ``GET``/``POST`` ``/seats`` and ``DELETE
/seats/{seat_id}`` for the seats they give.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.entitlements.Plans import (
    PLANS,
    Plan,
    best,
    plan_bought_by,
    plans_allowing,
    plans_with_feature,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

PAYMENT_REQUIRED = 402
ENTITLEMENT_PREFIX = "/v1/entitlement"


class EntitlementSeatModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,  # → user_id: the subscriber giving the seat
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["EntitlementSeatManager"]]
    seat_user_id: str = Field(..., description="The account the seat is given to")
    table_comment: ClassVar[str] = "Seats a subscriber shares their plan with"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        seat_user_id: str

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        seat_user_id: Optional[StringSearchModel] = None


class EntitlementResponse(ResponseModel):
    plan: str
    features: List[str]
    limits: Dict[str, Optional[int]] = Field(
        ..., description="Each limit's count; null is unlimited"
    )
    seats: int = Field(..., description="Seats this plan may give")
    seats_given: int
    via_seat_from: Optional[str] = Field(
        None, description="The subscriber whose seat gives this plan"
    )


class SeatRequest(ResponseModel):
    user_id: str


class SeatEntry(ResponseModel):
    """A seat given; its version (``updated_at``, else ``created_at``) is
    what taking it back names in If-Match."""

    id: str
    user_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SeatList(ResponseModel):
    seats: List[SeatEntry]


def _seat_entry(seat: Any) -> SeatEntry:
    return SeatEntry(
        id=seat.id,
        user_id=seat.seat_user_id,
        created_at=seat.created_at,
        updated_at=seat.updated_at,
    )


def _root() -> str:
    """Lookups run as ROOT, which also sees deleted rows: every one of them
    filters ``deleted_at`` itself."""
    return str(env("ROOT_ID"))


def _subscription_plans(user_id: str, model_registry: Any) -> List[Plan]:
    """The plans ``user_id``'s active subscriptions buy (none without payment)."""
    try:
        from zephyrex.extensions.payment.BLL_Payment import PaymentSubscriptionModel
    except ImportError:
        return []
    db = PaymentSubscriptionModel.DB(model_registry.DB.manager.Base)
    found = (
        db.list(
            requester_id=_root(),
            model_registry=model_registry,
            filters=[
                db.user_id == user_id,
                db.active.is_(True),
                db.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=PaymentSubscriptionModel,
        )
        or []
    )
    return [
        plan for plan in (plan_bought_by(s.plan_id) for s in found) if plan is not None
    ]


def _seats_given(user_id: str, model_registry: Any) -> List[Any]:
    db = EntitlementSeatModel.DB(model_registry.DB.manager.Base)
    found = (
        db.list(
            requester_id=_root(),
            model_registry=model_registry,
            filters=[db.user_id == user_id, db.deleted_at.is_(None)],
            return_type="dto",
            override_dto=EntitlementSeatModel,
        )
        or []
    )
    return sorted(found, key=lambda seat: (seat.created_at, seat.id))


def _seats_held(user_id: str, model_registry: Any) -> List[Any]:
    db = EntitlementSeatModel.DB(model_registry.DB.manager.Base)
    return (
        db.list(
            requester_id=_root(),
            model_registry=model_registry,
            filters=[db.seat_user_id == user_id, db.deleted_at.is_(None)],
            return_type="dto",
            override_dto=EntitlementSeatModel,
        )
        or []
    )


def _default_plans() -> List[Plan]:
    """The plan everyone has without paying, when ENTITLEMENT_DEFAULT_PLAN names
    one (e.g. the top plan for a test phase); none otherwise."""
    named = (env("ENTITLEMENT_DEFAULT_PLAN") or "").strip()
    plan = PLANS.get(named) if named else None
    return [] if plan is None else [plan]


def own_plan(user_id: str, model_registry: Any) -> Plan:
    """The plan ``user_id`` pays for, or has by default (the free plan when none)."""
    return best([*_subscription_plans(user_id, model_registry), *_default_plans()])


def entitlement(user_id: str, model_registry: Any) -> EntitlementResponse:
    """``user_id``'s plan, and where it comes from."""
    plan = own_plan(user_id, model_registry)
    via: Optional[str] = None
    for seat in _seats_held(user_id, model_registry):
        giver = str(seat.user_id)
        giver_plan = own_plan(giver, model_registry)
        honoured = [s.id for s in _seats_given(giver, model_registry)][
            : giver_plan.seats
        ]
        if seat.id in honoured and giver_plan.rank > plan.rank:
            plan, via = giver_plan, giver
    return EntitlementResponse(
        plan=plan.name,
        features=sorted(plan.features),
        limits=dict(plan.limits),
        seats=plan.seats if via is None else 0,
        seats_given=len(_seats_given(user_id, model_registry)) if via is None else 0,
        via_seat_from=via,
    )


def require_feature(user_id: str, model_registry: Any, feature: str) -> None:
    """Refuse (402) unless ``user_id``'s plan unlocks ``feature``; the answer
    names the plans that do."""
    if feature in entitlement(user_id, model_registry).features:
        return
    raise HTTPException(
        status_code=PAYMENT_REQUIRED,
        detail={"feature": feature, "upgrade_to": plans_with_feature(feature)},
    )


def require_within_limit(
    user_id: str, model_registry: Any, name: str, count: int
) -> None:
    """Refuse (402) when ``count`` (what the user would have after this
    action) is over ``user_id``'s ``name`` limit."""
    limits = entitlement(user_id, model_registry).limits
    allowed = limits.get(name, 0)
    if allowed is None or count <= allowed:
        return
    raise HTTPException(
        status_code=PAYMENT_REQUIRED,
        detail={
            "limit": name,
            "allowed": allowed,
            "upgrade_to": plans_allowing(name, count),
        },
    )


class EntitlementSeatManager(AbstractBLLManager, RouterMixin):
    _model = EntitlementSeatModel
    prefix: ClassVar[Optional[str]] = ENTITLEMENT_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Entitlements"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    @custom_route(
        method="GET",
        path="",
        output_model=EntitlementResponse,
        authentication_type="jwt",
        openapi_tags=("Entitlements",),
        summary="Your plan: its features, limits and seats",
        expose_in=(ExposeIn.REST,),
    )
    def current_route(self) -> EntitlementResponse:
        return entitlement(str(self.requester.id), self.model_registry)

    @custom_route(
        method="GET",
        path="/seats",
        output_model=SeatList,
        authentication_type="jwt",
        openapi_tags=("Entitlements",),
        summary="The seats you give, oldest first",
        expose_in=(ExposeIn.REST,),
    )
    def seats_route(self) -> SeatList:
        return SeatList(
            seats=[
                _seat_entry(seat)
                for seat in _seats_given(str(self.requester.id), self.model_registry)
            ]
        )

    @custom_route(
        method="POST",
        path="/seats",
        input_model=SeatRequest,
        output_model=SeatEntry,
        authentication_type="jwt",
        openapi_tags=("Entitlements",),
        summary="Share your plan with another account, if it has a seat left",
        expose_in=(ExposeIn.REST,),
    )
    def give_seat_route(self, body: SeatRequest) -> SeatEntry:
        giver = str(self.requester.id)
        if body.user_id == giver:
            raise HTTPException(status_code=422, detail="A seat is for someone else")
        plan = own_plan(giver, self.model_registry)
        given = _seats_given(giver, self.model_registry)
        if any(seat.seat_user_id == body.user_id for seat in given):
            raise HTTPException(
                status_code=409, detail="That account already has your seat"
            )
        if len(given) >= plan.seats:
            raise HTTPException(
                status_code=PAYMENT_REQUIRED,
                detail={"limit": "seats", "allowed": plan.seats, "upgrade_to": []},
            )
        return _seat_entry(self.create(user_id=giver, seat_user_id=body.user_id))

    @custom_route(
        method="DELETE",
        path="/seats/{seat_id}",
        output_model=SeatEntry,
        authentication_type="jwt",
        openapi_tags=("Entitlements",),
        summary="Take back a seat you gave",
        expose_in=(ExposeIn.REST,),
    )
    def take_back_seat_route(self, seat_id: str) -> SeatEntry:
        given = {
            seat.id: seat
            for seat in _seats_given(str(self.requester.id), self.model_registry)
        }
        seat = given.get(seat_id)
        if seat is None:
            raise HTTPException(status_code=404, detail="Seat not found")
        self.delete(id=seat_id)
        return _seat_entry(seat)


EntitlementSeatModel.Manager = EntitlementSeatManager

ALL_MODELS: List[type] = [EntitlementSeatModel]

__all__ = [
    "EntitlementSeatModel",
    "EntitlementSeatManager",
    "entitlement",
    "own_plan",
    "require_feature",
    "require_within_limit",
    "ALL_MODELS",
]
