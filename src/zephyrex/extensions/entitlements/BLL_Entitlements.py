# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a user may do: their plan, from their payment subscriptions and the
seats others share with them, and the checks that refuse the rest (402).

A user's plan is the best of: the default (free) plan; the plan each of
their active subscriptions buys; the plan of each product they bought once
(a succeeded payment naming a registered product, while it is not fully
refunded); and the plan of each subscriber who gave
them a seat, while that subscriber's plan still has the seat to give
(seats are honoured in the order they were given, so a downgrade drops the
newest first). Seats do not chain: a seat holder cannot give seats on.

Routes on ``/v1/entitlement``: ``GET`` the requester's plan, features,
limits and seats; ``GET``/``POST`` ``/seats`` and ``DELETE
/seats/{seat_id}`` for the seats they give; ``GET /products`` (public) what
is sold once and its price; ``POST /purchase`` to buy one, charged through
the payment extension at the registered price.

A product that sells units (seats, credits) is bought in a ``quantity``,
optionally for a ``subject_id`` its ``authorize_subject`` check lets the
buyer buy for, at its unit price times the quantity. Each purchase is
recorded against its payment (``entitlement_purchases``), and
:func:`units_bought` counts the units a subject's succeeded payments still
pay for: a partial refund withdraws its share of the units.
"""

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, ClassVar, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel
from pydantic import Field

from zephyrex.extensions.entitlements.Plans import (
    PLANS,
    PRODUCTS,
    Plan,
    best,
    plan_bought_by,
    plan_bought_with,
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
CONFLICT = 409
NOT_FOUND = 404
SERVICE_UNAVAILABLE = 503
ENTITLEMENT_PREFIX = "/v1/entitlement"
# The payment status that grants a product's plan.
SUCCEEDED = "succeeded"
MAX_PAYMENT_METHOD_ID = 512
MAX_SUBJECT_ID = 120
# One purchase's ceiling whatever a product allows, so a typo cannot charge a fortune.
MAX_QUANTITY = 10_000
UNPROCESSABLE = 422


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


class EntitlementPurchaseModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,  # → user_id: the buyer
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["EntitlementPurchaseManager"]]
    payment_id: str = Field(..., description="The payment that pays for it")
    product_id: str = Field(..., description="The registered product bought")
    quantity: int = Field(..., description="How many units were bought")
    subject_id: Optional[str] = Field(
        None, description="What the units were bought for (a group, a project)"
    )
    table_comment: ClassVar[str] = "Units of a product bought, and what for"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        payment_id: str
        product_id: str
        quantity: int
        subject_id: Optional[str] = None

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search):
        product_id: Optional[StringSearchModel] = None
        subject_id: Optional[StringSearchModel] = None


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


class ProductEntry(ResponseModel):
    id: str
    plan: Optional[str] = Field(
        None, description="The plan it grants; null sells units"
    )
    amount: str = Field(..., description="The price of one unit")
    currency: str
    description: str
    features: List[str]
    max_quantity: int = Field(1, description="How many units one purchase may buy")
    needs_subject: bool = Field(
        False, description="Whether a purchase names what the units are for"
    )


class ProductList(ResponseModel):
    products: List[ProductEntry]


class PurchaseRequest(ResponseModel):
    product_id: str = Field(..., min_length=1, max_length=120)
    # A card token or Square source from the provider's own browser SDK.
    payment_method_id: Optional[str] = Field(None, max_length=MAX_PAYMENT_METHOD_ID)
    quantity: int = Field(1, ge=1, le=MAX_QUANTITY)
    subject_id: Optional[str] = Field(None, min_length=1, max_length=MAX_SUBJECT_ID)


class PurchaseResult(ResponseModel):
    payment_id: str
    status: str
    entitlement: EntitlementResponse


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


def _not_fully_refunded(payment: Any) -> bool:
    try:
        return Decimal(payment.amount_refunded or "0") < Decimal(payment.amount)
    except InvalidOperation:
        return False


    """The plans ``user_id``'s succeeded, not fully refunded product payments buy."""
    try:
        from zephyrex.extensions.payment.BLL_Payment import PaymentModel
    except ImportError:
        return []
    db = PaymentModel.DB(model_registry.DB.manager.Base)
    found = (
        db.list(
            model_registry=model_registry,
            filters=[
                db.user_id == user_id,
                db.status == SUCCEEDED,
                db.product_id.isnot(None),
            ],
            return_type="dto",
            override_dto=PaymentModel,
        )
        or []
    )
    return [
        plan
        for plan in (
            plan_bought_with(p.product_id) for p in found if _not_fully_refunded(p)
        )
        if plan is not None
    ]


def _units_paid(quantity: int, payment: Any) -> int:
    """The units a payment for ``quantity`` still pays for: all of them while
    it is not refunded, none once fully refunded, its unrefunded share (whole
    units) in between."""
    try:
        amount = Decimal(payment.amount)
        kept = amount - Decimal(payment.amount_refunded or "0")
    except InvalidOperation:
        return 0
    if amount <= 0 or kept <= 0:
        return 0
    share = Decimal(quantity) * kept / amount
    return min(quantity, int(share.to_integral_value(rounding=ROUND_FLOOR)))


    """The units of ``product_id`` bought for ``subject_id`` (None: bought for
    nothing in particular) that succeeded payments still pay for."""
    try:
        from zephyrex.extensions.payment.BLL_Payment import PaymentModel
    except ImportError:
        return 0
    purchases_db = EntitlementPurchaseModel.DB(model_registry.DB.manager.Base)
    subject_filter = (
        purchases_db.subject_id.is_(None)
        if subject_id is None
        else purchases_db.subject_id == subject_id
    )
    purchases = (
        purchases_db.list(
            model_registry=model_registry,
            filters=[
                purchases_db.product_id == product_id,
                subject_filter,
            ],
            return_type="dto",
            override_dto=EntitlementPurchaseModel,
        )
        or []
    )
    if not purchases:
        return 0
    payments_db = PaymentModel.DB(model_registry.DB.manager.Base)
    payments = {
        str(p.id): p
        for p in (
            payments_db.list(
                model_registry=model_registry,
                filters=[
                    payments_db.id.in_([p.payment_id for p in purchases]),
                    payments_db.status == SUCCEEDED,
                ],
                return_type="dto",
                override_dto=PaymentModel,
            )
            or []
        )
    }
    return sum(
        _units_paid(int(p.quantity), payments[p.payment_id])
        for p in purchases
        if p.payment_id in payments
    )


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
    return best(
        [
            *_subscription_plans(user_id, model_registry),
            *_purchase_plans(user_id, model_registry),
            *_default_plans(),
        ]
    )


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

    @custom_route(
        method="GET",
        path="/products",
        output_model=ProductList,
        authentication_type="none",
        openapi_tags=("Entitlements",),
        summary="What is sold once, its price and what it unlocks",
        expose_in=(ExposeIn.REST,),
    )
    def products_route(self) -> ProductList:
        return ProductList(
            products=[
                ProductEntry(
                    id=product.id,
                    plan=product.plan,
                    amount=product.amount,
                    currency=product.currency,
                    description=product.description,
                    features=(
                        []
                        if product.plan is None
                        else sorted(PLANS[product.plan].features)
                    ),
                    max_quantity=product.max_quantity,
                    needs_subject=product.authorize_subject is not None,
                )
                for product in PRODUCTS.values()
            ]
        )

    @custom_route(
        method="POST",
        path="/purchase",
        input_model=PurchaseRequest,
        output_model=PurchaseResult,
        authentication_type="jwt",
        openapi_tags=("Entitlements",),
        summary="Buy a product (or units of one) at its registered price",
        expose_in=(ExposeIn.REST,),
    )
    async def purchase_route(self, body: PurchaseRequest) -> PurchaseResult:
        buyer = str(self.requester.id)
        product = PRODUCTS.get(body.product_id)
        if product is None:
            raise HTTPException(status_code=NOT_FOUND, detail="No such product")
        if body.quantity > product.max_quantity:
            raise HTTPException(
                status_code=UNPROCESSABLE,
                detail=f"At most {product.max_quantity} at a time",
            )
        check = product.authorize_subject
        if check is None and body.subject_id is not None:
            raise HTTPException(
                status_code=UNPROCESSABLE,
                detail="This product is not bought for anything",
            )
        if check is not None:
            if body.subject_id is None:
                raise HTTPException(
                    status_code=UNPROCESSABLE, detail="Name what this is bought for"
                )
            # Not yours and not there look the same, so ids cannot be probed.
            if not check(buyer, body.subject_id, self.model_registry):
                raise HTTPException(status_code=NOT_FOUND, detail="Not found")
        if (
            product.plan is not None
            and own_plan(buyer, self.model_registry).rank >= PLANS[product.plan].rank
        ):
            raise HTTPException(
                status_code=CONFLICT, detail="Your plan already includes this"
            )
        try:
            from zephyrex.extensions.payment.EXT_Payment import EXT_Payment
        except ImportError as missing:
            raise HTTPException(
                status_code=SERVICE_UNAVAILABLE,
                detail="Payments are not available here",
            ) from missing
        payment = await EXT_Payment.payment_create(
            requester_id=buyer,
            amount=str(Decimal(product.amount) * body.quantity),
            currency=product.currency,
            payment_method_id=body.payment_method_id,
            description=(
                product.description
                if body.quantity == 1
                else f"{product.description} x {body.quantity}"
            ),
            product_id=product.id,
        )
        EntitlementPurchaseManager(
            model_registry=self.model_registry, requester_id=_root()
        ).create(
            user_id=buyer,
            payment_id=str(payment["id"]),
            product_id=product.id,
            quantity=body.quantity,
            subject_id=body.subject_id,
        )
        return PurchaseResult(
            payment_id=str(payment["id"]),
            status=str(payment["status"]),
            entitlement=entitlement(buyer, self.model_registry),
        )


class EntitlementPurchaseManager(AbstractBLLManager, RouterMixin):
    """Purchase records, written by the purchase route and read through
    :func:`units_bought`; no routes of their own."""

    _model = EntitlementPurchaseModel
    prefix: ClassVar[Optional[str]] = ENTITLEMENT_PREFIX
    tags: ClassVar[Optional[List[str]]] = ["Entitlements"]
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []


EntitlementSeatModel.Manager = EntitlementSeatManager
EntitlementPurchaseModel.Manager = EntitlementPurchaseManager

ALL_MODELS: List[type] = [EntitlementSeatModel, EntitlementPurchaseModel]

__all__ = [
    "EntitlementSeatModel",
    "EntitlementSeatManager",
    "EntitlementPurchaseModel",
    "EntitlementPurchaseManager",
    "units_bought",
    "entitlement",
    "own_plan",
    "require_feature",
    "require_within_limit",
    "ALL_MODELS",
]
