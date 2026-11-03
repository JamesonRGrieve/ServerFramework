# SPDX-License-Identifier: AGPL-3.0-or-later
"""The plans an app sells, and what each unlocks.

An app registers its plans in code (:func:`register_plan`): each has a
rank (the higher, the better), the features it unlocks, its limits (a
count, or ``None`` for unlimited) and how many seats its subscriber may
share it with. Which payment-provider plans (Stripe prices, Square plan
variations, …) buy a plan is deployment configuration: the comma-separated
ids in ``ENTITLEMENT_<PLAN>_PLAN_IDS``.

A plan can also be sold once rather than by subscription: an app
registers a :class:`Product` (:func:`register_product`) naming the plan it
buys and its price. The price lives in code, never in the request, so a
client cannot choose what it pays.

A product can instead sell units of something the app counts (seats in a
group, credits) rather than a plan: it names no plan, a ``max_quantity``
above one, and usually a subject the units are bought for, which
``authorize_subject`` checks the buyer may buy for (the group's owner,
say). The app asks :func:`~zephyrex.extensions.entitlements.BLL_Entitlements.units_bought`
how many units are paid for.

The lowest-ranked plan is everyone's without paying for it (the free
plan); with no plan registered, there is an empty one.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional

from zephyrex.lib.Environment import env

FREE = "free"


@dataclass(frozen=True)
class Plan:
    name: str
    rank: int
    features: FrozenSet[str] = frozenset()
    # A limit's count; None is unlimited. A limit a plan does not name is 0.
    limits: Mapping[str, Optional[int]] = field(default_factory=dict)
    seats: int = 0

    @property
    def plan_ids_setting(self) -> str:
        return f"ENTITLEMENT_{self.name.upper()}_PLAN_IDS"

    def limit(self, name: str) -> Optional[int]:
        return self.limits.get(name, 0)


# Whether buyer ``user_id`` may buy units for ``subject_id``:
# (user_id, subject_id, model_registry) -> bool.
SubjectCheck = Callable[[str, str, Any], bool]


@dataclass(frozen=True)
class Product:
    """Something sold once: a successful payment for it grants ``plan`` for
    good, or, with no plan, buys units the app counts."""

    id: str
    # The plan it grants; None for a product that sells units.
    plan: Optional[str]
    # Decimal major units, e.g. "19.00": the price of one unit.
    amount: str
    # ISO 4217, e.g. "USD".
    currency: str
    description: str
    # How many units one purchase may buy; a plan is bought one at a time.
    max_quantity: int = 1
    # Units bought for a subject (a group, a project) need it named, and
    # this check passed, at purchase; None sells them for nothing in
    # particular.
    authorize_subject: Optional[SubjectCheck] = field(
        default=None, compare=False, repr=False
    )

    def __post_init__(self) -> None:
        try:
            positive = Decimal(self.amount) > 0
        except InvalidOperation:
            positive = False
        if not positive:
            raise ValueError(f"Product {self.id!r} needs a positive decimal amount")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError(f"Product {self.id!r} needs an ISO 4217 currency code")
        if self.max_quantity < 1:
            raise ValueError(f"Product {self.id!r} needs a max_quantity of at least 1")
        if self.plan is not None and self.max_quantity != 1:
            raise ValueError(
                f"Product {self.id!r} grants a plan, so sells one at a time"
            )
        if self.plan is not None and self.authorize_subject is not None:
            raise ValueError(
                f"Product {self.id!r} grants a plan to its buyer, not a subject"
            )


PLANS: Dict[str, Plan] = {}
PRODUCTS: Dict[str, Product] = {}


def register_plan(plan: Plan) -> None:
    """Offer ``plan``. Registering a name twice differently is a programming
    error, so it raises rather than silently replacing the first."""
    existing = PLANS.get(plan.name)
    if existing is not None and existing != plan:
        raise ValueError(f"Plan {plan.name!r} is already registered differently")
    PLANS[plan.name] = plan


def register_product(product: Product) -> None:
    """Sell ``product``. Its plan, if it names one, must already be registered,
    and a product id registered twice differently is a programming error."""
    if product.plan is not None and product.plan not in PLANS:
        raise ValueError(
            f"Product {product.id!r} buys unregistered plan {product.plan!r}"
        )
    existing = PRODUCTS.get(product.id)
    if existing is not None and existing != product:
        raise ValueError(f"Product {product.id!r} is already registered differently")
    PRODUCTS[product.id] = product


def plan_bought_with(product_id: Optional[str]) -> Optional[Plan]:
    """The plan a product buys, if ``product_id`` names a registered product
    that grants one."""
    product = PRODUCTS.get(product_id or "")
    if product is None or product.plan is None:
        return None
    return PLANS.get(product.plan)


def default_plan() -> Plan:
    """Everyone's plan without paying: the lowest-ranked one."""
    if not PLANS:
        return Plan(name=FREE, rank=0)
    return min(PLANS.values(), key=lambda plan: plan.rank)


def provider_plan_ids(plan: Plan) -> FrozenSet[str]:
    """The payment-provider plan ids that buy ``plan`` here."""
    configured = str(env(plan.plan_ids_setting) or "")
    return frozenset(part.strip() for part in configured.split(",") if part.strip())


def plan_bought_by(provider_plan_id: Optional[str]) -> Optional[Plan]:
    """The plan a provider plan id buys, if any."""
    if not provider_plan_id:
        return None
    for plan in PLANS.values():
        if provider_plan_id in provider_plan_ids(plan):
            return plan
    return None


def best(plans: Iterable[Plan]) -> Plan:
    """The highest-ranked of ``plans``, or the default plan."""
    return max([default_plan(), *plans], key=lambda plan: plan.rank)


def plans_with_feature(feature: str) -> List[str]:
    """The plans unlocking ``feature``, cheapest first: what to upgrade to."""
    return [
        p.name
        for p in sorted(PLANS.values(), key=lambda p: p.rank)
        if feature in p.features
    ]


def plans_allowing(name: str, count: int) -> List[str]:
    """The plans whose ``name`` limit admits ``count``, cheapest first."""
    return [
        p.name
        for p in sorted(PLANS.values(), key=lambda p: p.rank)
        if p.limit(name) is None or count <= (p.limit(name) or 0)
    ]
