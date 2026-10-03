# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the payment extension records: each user's customer link, the
payments and subscriptions made for them, and the login check on their
subscriptions.

- ``users.external_payment_id`` / ``users.payment_instance_id``: the
  user's customer record at a provider and the account holding it. The
  server writes them (``link_customer``); a user who could set their own
  could name another customer, read that customer's details and pass as
  that customer's subscriber. A link counts only with both set.
- ``payments`` and ``payment_subscriptions``: owned by the user they were
  made for (the requester, whatever the caller names; ROOT and SYSTEM may
  name another), never moved by an update, read-only over REST. The
  provider is the source of truth; a row mirrors its answer each time the
  record is made, read, captured, refunded or cancelled, or named by a
  verified notification.
- ``require_active_subscription``: the login check. A user with
  subscriptions is refused (402) when none of them is active, each read
  again from its provider (the last record where the provider cannot be
  reached in time).
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
)

from fastapi import HTTPException
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ExternalErrors import BaseExternalError
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    HookContext,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
    _cache_sync_run,
    hook_bll,
)
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel
from zephyrex.pydantic2.sqlalchemy import extension_model

if TYPE_CHECKING:
    from zephyrex.extensions.payment.EXT_Payment import EXT_Payment

logger = logging.getLogger(__name__)

READ_ONLY = [RouteType.GET, RouteType.LIST, RouteType.SEARCH]
# The user columns only the server writes.
LINK_FIELDS = ("external_payment_id", "payment_instance_id")
# What a read copies from the provider's answer onto a record.
PAYMENT_MIRRORED = (
    "external_id",
    "status",
    "provider_status",
    "amount",
    "currency",
    "amount_refunded",
    "customer_id",
)
SUBSCRIPTION_MIRRORED = (
    "status",
    "active",
    "plan_id",
    "customer_id",
    "current_period_end",
    "cancel_at_period_end",
)
# How long a login waits on the providers for the subscription check.
LOGIN_CHECK_TIMEOUT_SECONDS = 20.0


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


@extension_model(UserModel)
class Payment_UserModel(BaseModel):
    """A user's customer record at a payment provider, set by the server."""

    external_payment_id: Optional[str] = Field(
        None, description="The user's customer id at the payment provider"
    )
    payment_instance_id: Optional[str] = Field(
        None, description="The payment account (provider instance) holding it"
    )

    class Update(BaseModel):
        external_payment_id: Optional[str] = Field(
            None, description="Server-set: the customer id at the provider"
        )
        payment_instance_id: Optional[str] = Field(
            None, description="Server-set: the account holding the customer"
        )

    class Search(BaseModel):
        external_payment_id: Optional[str] = Field(
            None, description="The user's customer id at the payment provider"
        )


@hook_bll(UserManager.update, timing="before", priority=5)
def link_is_server_set(context: HookContext) -> None:
    """Only ROOT and SYSTEM write a user's customer link."""
    named = sorted(set(LINK_FIELDS) & set(context.kwargs))
    if named and not _server_side(context.manager.requester.id):
        raise HTTPException(
            status_code=403,
            detail=f"Only the server sets {', '.join(named)}",
        )


def linked_customer(user: Any) -> Optional[Tuple[str, str]]:
    """``(instance id, customer id)`` of the user's customer record, when
    the server linked one."""
    instance_id = getattr(user, "payment_instance_id", None)
    customer_id = getattr(user, "external_payment_id", None)
    if instance_id and customer_id:
        return str(instance_id), str(customer_id)
    return None


def link_customer(user_id: str, instance_id: str, customer_id: str) -> None:
    """Record ``customer_id`` at ``instance_id`` as ``user_id``'s."""
    from zephyrex.extensions.payment.EXT_Payment import EXT_Payment

    users = EXT_Payment.as_requester(UserManager, str(env("ROOT_ID")))
    users.update(
        user_id, external_payment_id=customer_id, payment_instance_id=instance_id
    )


class ProviderFields(RouteModel):
    """Where a record came from at the provider, and when it was read."""

    provider: str = Field(..., description="The provider (stripe, square, …)")
    provider_instance_id: str = Field(..., description="The account it is on")
    external_id: str = Field(..., description="The provider's id for it")
    refreshed_at: datetime = Field(..., description="When the provider was last read")


class PaymentModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    ProviderFields,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PaymentManager"]]
    status: str = Field(
        ..., description="pending, authorized, succeeded, failed, canceled, refunded"
    )
    provider_status: Optional[str] = Field(
        None, description="The provider's own status"
    )
    amount: str = Field(..., description="The amount (decimal, major units)")
    currency: str = Field(..., description="ISO 4217 currency code")
    amount_refunded: Optional[str] = Field(None, description="Refunded so far")
    customer_id: Optional[str] = Field(None, description="The provider's customer")
    description: Optional[str] = Field(None, description="What it is for")

    table_comment: ClassVar[str] = (
        "Payments (a local mirror of each provider's payment)"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
        provider: str
        provider_instance_id: str
        external_id: str
        status: str
        provider_status: Optional[str] = None
        amount: str
        currency: str
        amount_refunded: Optional[str] = None
        customer_id: Optional[str] = None
        description: Optional[str] = None
        refreshed_at: datetime

    class Update(BaseModel):
        external_id: Optional[str] = None
        status: Optional[str] = None
        provider_status: Optional[str] = None
        amount: Optional[str] = None
        currency: Optional[str] = None
        amount_refunded: Optional[str] = None
        customer_id: Optional[str] = None
        refreshed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        external_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        currency: Optional[StringSearchModel] = None


class PaymentSubscriptionModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    ProviderFields,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PaymentSubscriptionManager"]]
    plan_id: Optional[str] = Field(None, description="The plan subscribed to")
    customer_id: Optional[str] = Field(None, description="The provider's customer")
    status: str = Field(..., description="The provider's status for it")
    active: bool = Field(..., description="Whether it is paid up now")
    current_period_end: Optional[datetime] = Field(
        None, description="When the period paid for ends"
    )
    cancel_at_period_end: bool = Field(
        False, description="Whether it ends when the period does"
    )

    table_comment: ClassVar[str] = (
        "Subscriptions (a local mirror of each provider's subscription)"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
        provider: str
        provider_instance_id: str
        external_id: str
        plan_id: Optional[str] = None
        customer_id: Optional[str] = None
        status: str
        active: bool
        current_period_end: Optional[datetime] = None
        cancel_at_period_end: bool = False
        refreshed_at: datetime

    class Update(BaseModel):
        plan_id: Optional[str] = None
        customer_id: Optional[str] = None
        status: Optional[str] = None
        active: Optional[bool] = None
        current_period_end: Optional[datetime] = None
        cancel_at_period_end: Optional[bool] = None
        refreshed_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        external_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None


class MirrorManager(AbstractBLLManager):
    """A record owned by the user it was made for, mirroring the provider."""

    mirrored: ClassVar[Tuple[str, ...]] = ()

    def create(self, **kwargs: Any) -> Any:
        """Records are the requester's; ROOT and SYSTEM may name the owner."""
        requester_id = self.requester.id

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            if not _server_side(requester_id) or not fields.get("user_id"):
                fields["user_id"] = requester_id
            return fields

        return super().create(**_each(kwargs, owned))

    def update(self, id: str, **kwargs: Any) -> Any:
        """An update never moves a record to another owner or team."""
        kwargs.pop("user_id", None)
        kwargs.pop("team_id", None)
        return super().update(id, **kwargs)

    def record(self, answer: Dict[str, Any], **extra: Any) -> Any:
        """A new record of the provider's ``answer``."""
        return self.create(
            provider=answer["provider"],
            provider_instance_id=answer["provider_instance_id"],
            external_id=answer["external_id"],
            **{key: answer.get(key) for key in self.mirrored if key != "external_id"},
            **extra,
            refreshed_at=datetime.now(UTC),
        )

    def mirror(self, record: Any, answer: Dict[str, Any]) -> Any:
        """``record`` (which the requester could read) updated to the
        provider's ``answer``. The mirror is the server's to write, so it
        is written as ROOT: a record ROOT made for a user is otherwise
        closed to that user's writes."""
        server = type(self)(
            model_registry=self.model_registry, requester_id=str(env("ROOT_ID"))
        )
        return server.update(
            record.id,
            **{key: answer.get(key) for key in self.mirrored if key in answer},
            refreshed_at=datetime.now(UTC),
        )


class PaymentManager(MirrorManager, RouterMixin):
    _model = PaymentModel
    mirrored: ClassVar[Tuple[str, ...]] = PAYMENT_MIRRORED
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY


class PaymentSubscriptionManager(MirrorManager, RouterMixin):
    _model = PaymentSubscriptionModel
    mirrored: ClassVar[Tuple[str, ...]] = SUBSCRIPTION_MIRRORED
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY


async def refreshed(
    subscriptions: PaymentSubscriptionManager, records: Sequence[Any]
) -> List[Any]:
    """Each subscription read again from its provider and recorded; the
    last record where the provider cannot answer."""
    from zephyrex.extensions.payment.EXT_Payment import EXT_Payment

    current = []
    for record in records:
        try:
            answer = await EXT_Payment.refresh_record("subscription", record)
        except (BaseExternalError, HTTPException) as exc:
            logger.warning(
                "Subscription %s could not be read from its provider: %s",
                record.id,
                exc,
            )
            current.append(record)
            continue
        current.append(subscriptions.mirror(record, answer))
    return current


async def refresh_named(
    extension: Type["EXT_Payment"], event: Dict[str, Any]
) -> Optional[str]:
    """The id of the record a verified notification names, read again from
    the provider; None when no record is kept of it."""
    kind, object_id = event.get("kind"), event.get("object_id")
    if kind is None or not object_id:
        return None
    manager_class: Type[MirrorManager] = (
        PaymentManager if kind == "payment" else PaymentSubscriptionManager
    )
    manager = extension.as_requester(manager_class, str(env("ROOT_ID")))
    records = manager.list(
        provider_instance_id=event["provider_instance_id"], external_id=object_id
    )
    if not records:
        return None
    record = records[0]
    manager.mirror(record, await extension.refresh_record(kind, record))
    return str(record.id)


async def current_within(
    subscriptions: PaymentSubscriptionManager, records: Sequence[Any], seconds: float
) -> List[Any]:
    """:func:`refreshed`, or the last records when the providers take
    longer than ``seconds``."""
    try:
        return await asyncio.wait_for(refreshed(subscriptions, records), seconds)
    except asyncio.TimeoutError:
        logger.warning("Subscriptions were not read in time; using the last records")
        return list(records)


def require_active_subscription(user_id: str, model_registry: Any) -> None:
    """Refuse (402) a user whose subscriptions are all inactive: the check a
    login makes once the credentials are proven. Users with none, ROOT and
    SYSTEM are not checked; DISABLE_SUBSCRIPTION_VALIDATION=true turns it
    off. ``UserManager.login`` is a static method, which manager hooks
    cannot reach, so core calls this through its login hook."""
    if str(env("DISABLE_SUBSCRIPTION_VALIDATION") or "").lower() == "true":
        return
    if not user_id or _server_side(str(user_id)):
        return
    subscriptions = PaymentSubscriptionManager(
        model_registry=model_registry, requester_id=str(env("ROOT_ID"))
    )
    records = subscriptions.list(user_id=user_id)
    if not records:
        return
    current = _cache_sync_run(
        current_within(subscriptions, records, LOGIN_CHECK_TIMEOUT_SECONDS),
        timeout=None,
    )
    if not any(record.active for record in current):
        raise HTTPException(
            status_code=402,
            detail="Your subscription is inactive. Please update your payment method.",
        )
