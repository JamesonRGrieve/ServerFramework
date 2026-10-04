# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking payments through Stripe, Square, PayPal, Helcim and Moneris.

Each provider instance is one merchant account: its credentials are
write-only settings (with the provider's environment variables as the
fallback a single-account deployment uses). The abilities act for the
user named by ``requester_id``:

- ``customer_create`` / ``customer_get``: the user's customer record at a
  provider, linked to the user (``external_payment_id`` and
  ``payment_instance_id``, written by the server only);
- ``payment_create`` / ``payment_get`` / ``payment_list``: a charge for
  the user, recorded in ``payments`` (owned by the user) and mirrored
  from the provider on every read;
- ``payment_capture`` / ``payment_refund``: the merchant's side, for
  ROOT or SYSTEM only (a payer never refunds their own payment);
- ``subscription_create`` / ``subscription_get`` / ``subscription_list``
  / ``subscription_cancel`` / ``subscription_status``: the user's
  subscriptions, recorded in ``payment_subscriptions``
  (``BLL_Payment.require_active_subscription`` is the login check
  refusing a user whose subscriptions are all inactive);
- ``webhook_process``: a provider's signed notification, verified as that
  provider signs it, refreshing the payment or subscription it names.

What a provider's API cannot do is refused with the reason. A call that
moves money is never failed over to another account once it may have
reached the first: an unclear outcome is reported, not retried
elsewhere, so a payment cannot be taken twice.
"""

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import (
    Any,
    ClassVar,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Union,
)

from fastapi import HTTPException

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

PAYMENT_REQUEST_TIMEOUT_SECONDS = 30.0
# A notification older (or newer) than this is refused: a captured one
# cannot be replayed later.
WEBHOOK_TOLERANCE_SECONDS = 300
MAX_AMOUNT = Decimal("1000000")
MAX_TEXT = 500
MAX_TRIAL_DAYS = 730
PAYMENT_STATUSES = (
    "pending",
    "authorized",
    "succeeded",
    "failed",
    "canceled",
    "refunded",
)
_CURRENCY = re.compile(r"^[A-Za-z]{3}$")
_IP = re.compile(r"^[0-9A-Fa-f:.]{2,45}$")


def checked_amount(value: Any, what: str = "amount") -> Decimal:
    """A positive, finite amount in major units (``"12.50"``)."""
    if isinstance(value, (bool, float)):
        raise InvalidInputExternalError(f"{what} is a decimal string or an integer")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InvalidInputExternalError(f"{what} is not an amount") from None
    if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
        raise InvalidInputExternalError(
            f"{what} must be more than 0, at most {MAX_AMOUNT}"
        )
    return amount


def checked_currency(value: Any) -> str:
    if not isinstance(value, str) or not _CURRENCY.match(value):
        raise InvalidInputExternalError("currency is a three-letter ISO 4217 code")
    return value.upper()


def checked_text(value: Optional[str], what: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise InvalidInputExternalError(
            f"{what} is text of at most {MAX_TEXT} characters"
        )
    return value


def checked_ip(value: Optional[str]) -> Optional[str]:
    if value is not None and not _IP.match(value):
        raise InvalidInputExternalError("customer_ip is an IP address")
    return value


def checked_trial(days: Optional[int]) -> Optional[int]:
    if days is not None and (isinstance(days, bool) or not 1 <= days <= MAX_TRIAL_DAYS):
        raise InvalidInputExternalError(f"trial_days is 1-{MAX_TRIAL_DAYS}")
    return days


def checked_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Up to 20 short string pairs: what every provider can carry."""
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping) or len(metadata) > 20:
        raise InvalidInputExternalError("metadata is at most 20 key/value pairs")
    checked: Dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not 0 < len(key) <= 40 or key == "user_id":
            raise InvalidInputExternalError(
                "a metadata key is 1-40 characters, not user_id"
            )
        checked[key] = str(checked_text(str(value), "a metadata value"))
    return checked


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class PaymentRequest:
    """A charge for ``user_id``, in major units of ``currency``."""

    amount: Decimal
    currency: str
    user_id: str
    idempotency_key: str
    customer_id: Optional[str] = None
    payment_method_id: Optional[str] = None
    description: Optional[str] = None
    capture: bool = True
    customer_ip: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MoneyAction:
    """A capture or refund of a payment in ``currency``: all of what is
    left when ``amount`` is None."""

    currency: str
    idempotency_key: str
    amount: Optional[Decimal] = None
    reason: Optional[str] = None
    customer_ip: Optional[str] = None


@dataclass(frozen=True)
class CustomerRequest:
    email: str
    user_id: str
    idempotency_key: str
    name: Optional[str] = None


@dataclass(frozen=True)
class SubscriptionRequest:
    plan_id: str
    user_id: str
    idempotency_key: str
    customer_id: Optional[str] = None
    trial_days: Optional[int] = None
    metadata: Dict[str, str] = field(default_factory=dict)


def quoted_values(text: str, name: str) -> List[str]:
    """Every string value of a ``"name": "…"`` member in JSON ``text``,
    whole or cut short."""
    pattern = re.compile(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(name))
    values = []
    for match in pattern.finditer(text):
        try:
            values.append(str(json.loads(f'"{match.group(1)}"')))
        except ValueError:
            values.append(match.group(1))
    return values


def lowered(headers: Mapping[str, str]) -> Dict[str, str]:
    return {str(name).lower(): str(value) for name, value in headers.items()}


def one_matches(expected: str, candidates: Iterable[str]) -> bool:
    """Whether any candidate equals ``expected``, each compared in constant
    time (every candidate is compared)."""
    found = False
    for candidate in candidates:
        found |= hmac.compare_digest(expected.encode(), candidate.encode())
    return found


def hmac_sha256(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha256).digest()


def fresh(timestamp: Any, now: float) -> bool:
    """Whether a signed timestamp (Unix seconds) is within the tolerance."""
    try:
        moment = int(str(timestamp))
    except ValueError:
        return False
    return abs(now - moment) <= WEBHOOK_TOLERANCE_SECONDS


class AbstractPaymentProvider(AbstractStaticProvider):
    """A payment platform; each instance is one merchant account. What a
    platform's API cannot do keeps the refusing defaults below, and is left
    out of its ``_abilities`` so a rotation never picks it for that."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    http_timeout_seconds: ClassVar[float] = PAYMENT_REQUEST_TIMEOUT_SECONDS
    # Whether a subscription here belongs to a customer record made first.
    subscriptions_need_customer: ClassVar[bool] = True

    # ISO 4217 currencies whose minor-unit exponent is NOT the default of 2.
    # Everything not listed here has 2 decimal places. Used by the money
    # conversion SSOT below so a JPY amount is not multiplied by 100 and a BHD
    # amount is not truncated to 2 places.
    _CURRENCY_MINOR_UNITS: ClassVar[Dict[str, int]] = {
        # zero-decimal
        "JPY": 0,
        "KRW": 0,
        "VND": 0,
        "CLP": 0,
        "ISK": 0,
        "PYG": 0,
        "RWF": 0,
        "UGX": 0,
        "XOF": 0,
        "XAF": 0,
        "XPF": 0,
        "BIF": 0,
        "DJF": 0,
        "GNF": 0,
        "KMF": 0,
        # three-decimal
        "BHD": 3,
        "KWD": 3,
        "OMR": 3,
        "TND": 3,
        "JOD": 3,
        "IQD": 3,
        "LYD": 3,
    }

    @classmethod
    def _currency_exponent(cls, currency: str) -> int:
        return cls._CURRENCY_MINOR_UNITS.get(currency.upper(), 2)

    @classmethod
    def to_minor_units(cls, amount: Union[Decimal, int, str], currency: str) -> int:
        """Major-unit amount → integer minor units, currency-exponent aware and
        rounded HALF_UP. ``$1.15 → 115``, ``¥100 → 100``, ``1.234 BHD → 1234``.
        Replaces per-provider ``int(amount * 100)`` (which truncated and assumed
        2 decimals for every currency)."""
        scaled = Decimal(str(amount)) * (10 ** cls._currency_exponent(currency))
        return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))

    @classmethod
    def from_minor_units(cls, units: Union[int, str], currency: str) -> Decimal:
        """Inverse of :meth:`to_minor_units`: integer minor units → major-unit
        ``Decimal``. ``115 → Decimal('1.15')``, ``100 JPY → Decimal('100')``."""
        return Decimal(int(units)) / Decimal(10) ** cls._currency_exponent(currency)

    @classmethod
    def format_amount(cls, amount: Union[Decimal, int, str], currency: str) -> str:
        """Major-unit amount as a decimal string with the currency's minor-unit
        places, rounded HALF_UP. ``$1.15 → "1.15"``, ``¥100 → "100"``,
        ``1.2345 BHD → "1.234"``. For APIs (e.g. PayPal) that want a decimal
        string rather than integer minor units — replaces ``f"{amount:.2f}"``
        (which fixed 2 places for every currency)."""
        exp = cls._currency_exponent(currency)
        quantum = Decimal(1).scaleb(-exp)  # 10**-exp, e.g. Decimal("0.01")
        quantized = Decimal(str(amount)).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{quantized:.{exp}f}"

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def required(cls, instance: ProviderInstanceModel, key: str) -> str:
        """A setting the call cannot go without: an account missing it is
        unconfigured, so the rotation moves on to the next one."""
        value = cls.setting(instance, key)
        if not value:
            raise TransientExternalError(
                f"{cls.friendly_name} {key} not configured", provider=cls.name
            )
        return str(value)

    @classmethod
    def endpoint(cls, instance: ProviderInstanceModel, path: str) -> str:
        """``path`` on the account's API address (its ``api_base``)."""
        base = cls.required(instance, "api_base").rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise InvalidInputExternalError(
                f"{cls.friendly_name} api_base is an http(s) address", provider=cls.name
            )
        return base + path

    @classmethod
    def cannot(cls, what: str) -> PermanentExternalError:
        return PermanentExternalError(
            f"{cls.friendly_name}'s API cannot {what}", provider=cls.name
        )

    # The JSON string fields a refusal's body gives its reason in.
    refusal_fields: ClassVar[Tuple[str, ...]] = ()

    @classmethod
    def refusal_detail(cls, payload: Any) -> str:
        """The platform's own reason in a refusal's body, where it gives
        one. Read as text, since the error body is cut short."""
        return "; ".join(
            value
            for name in cls.refusal_fields
            for value in quoted_values(str(payload or ""), name)
            if value
        )

    @classmethod
    async def call(
        cls, method: str, url: str, *, moves_money: bool = False, **options: Any
    ) -> Any:
        """One request through ``http()``: the decoded answer, or a typed
        error carrying the platform's reason. A request that ``moves_money``
        and gets no clear answer may still have been carried out, so it is
        a permanent failure here (never retried on another account)."""
        try:
            return await cls.http().request(method, url, **options)
        except TransientExternalError as exc:
            if not moves_money:
                raise
            raise PermanentExternalError(
                f"{cls.friendly_name} gave no clear answer; whether the request "
                "took effect is unknown, so it is not retried elsewhere",
                provider=cls.name,
                cause=exc,
            ) from exc
        except (InvalidInputExternalError, AuthExternalError) as exc:
            detail = cls.refusal_detail(exc.upstream_payload)
            if not detail:
                raise
            raise type(exc)(
                f"{cls.friendly_name} refused the request: {detail}",
                provider=cls.name,
                upstream_status=exc.upstream_status,
                upstream_payload=exc.upstream_payload,
                cause=exc,
            ) from exc

    @classmethod
    def payment_answer(
        cls,
        instance: ProviderInstanceModel,
        external_id: Any,
        *,
        status: str,
        provider_status: Any,
        amount: Union[Decimal, int, str],
        currency: str,
        customer_id: Optional[str] = None,
        amount_refunded: Optional[Union[Decimal, int, str]] = None,
        client_secret: Optional[str] = None,
        approval_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A payment in the shared shape: money as decimal strings."""
        code = currency.upper()
        return {
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
            "external_id": str(external_id),
            "status": status if status in PAYMENT_STATUSES else "pending",
            "provider_status": str(provider_status or ""),
            "amount": cls.format_amount(amount, code),
            "currency": code,
            "customer_id": customer_id or None,
            "amount_refunded": (
                None
                if amount_refunded is None
                else cls.format_amount(amount_refunded, code)
            ),
            "client_secret": client_secret,
            "approval_url": approval_url,
        }

    @classmethod
    def refund_answer(
        cls,
        instance: ProviderInstanceModel,
        refund_id: Any,
        *,
        payment_id: str,
        status: Any,
        amount: Union[Decimal, int, str],
        currency: str,
    ) -> Dict[str, Any]:
        return {
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
            "refund_id": str(refund_id),
            "payment_id": payment_id,
            "status": str(status or "").lower(),
            "amount": cls.format_amount(amount, currency),
            "currency": currency.upper(),
        }

    @classmethod
    def customer_answer(
        cls,
        instance: ProviderInstanceModel,
        customer_id: Any,
        *,
        email: Optional[str],
        name: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
            "customer_id": str(customer_id),
            "email": email or None,
            "name": name or None,
        }

    @classmethod
    def subscription_answer(
        cls,
        instance: ProviderInstanceModel,
        external_id: Any,
        *,
        status: Any,
        active: bool,
        plan_id: Optional[str],
        customer_id: Optional[str] = None,
        current_period_end: Optional[datetime] = None,
        cancel_at_period_end: bool = False,
        approval_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
            "external_id": str(external_id),
            "status": str(status or "").lower(),
            "active": bool(active),
            "plan_id": plan_id,
            "customer_id": customer_id or None,
            "current_period_end": current_period_end,
            "cancel_at_period_end": bool(cancel_at_period_end),
            "approval_url": approval_url,
        }

    @classmethod
    def event_answer(
        cls,
        instance: ProviderInstanceModel,
        event_id: Any,
        *,
        event_type: Any,
        kind: Optional[str],
        object_id: Any,
    ) -> Dict[str, Any]:
        """A verified notification: what it names (a ``payment`` or a
        ``subscription``, by its provider id), never what it claims about
        it; the record is read again from the provider."""
        return {
            "provider": cls.name,
            "provider_instance_id": str(instance.id),
            "event_id": str(event_id) if event_id is not None else None,
            "event_type": str(event_type or ""),
            "kind": kind if kind in ("payment", "subscription") else None,
            "object_id": str(object_id) if object_id else None,
        }

    # The operations. A platform overrides what its API offers and names it
    # in ``_abilities``.

    @classmethod
    async def create_payment(
        cls, instance: ProviderInstanceModel, request: PaymentRequest
    ) -> Dict[str, Any]:
        raise cls.cannot("take a payment")

    @classmethod
    async def get_payment(
        cls, instance: ProviderInstanceModel, payment_id: str
    ) -> Dict[str, Any]:
        raise cls.cannot("read a payment")

    @classmethod
    async def capture_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        raise cls.cannot("capture an authorized payment")

    @classmethod
    async def refund_payment(
        cls, instance: ProviderInstanceModel, payment_id: str, action: MoneyAction
    ) -> Dict[str, Any]:
        raise cls.cannot("refund a payment")

    @classmethod
    async def create_customer(
        cls, instance: ProviderInstanceModel, request: CustomerRequest
    ) -> Dict[str, Any]:
        raise cls.cannot("keep customer records")

    @classmethod
    async def get_customer(
        cls, instance: ProviderInstanceModel, customer_id: str
    ) -> Dict[str, Any]:
        raise cls.cannot("keep customer records")

    @classmethod
    async def create_subscription(
        cls, instance: ProviderInstanceModel, request: SubscriptionRequest
    ) -> Dict[str, Any]:
        raise cls.cannot("subscribe a customer to a plan")

    @classmethod
    async def get_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str
    ) -> Dict[str, Any]:
        raise cls.cannot("read a subscription")

    @classmethod
    async def cancel_subscription(
        cls, instance: ProviderInstanceModel, subscription_id: str, at_period_end: bool
    ) -> Dict[str, Any]:
        raise cls.cannot("cancel a subscription")

    @classmethod
    async def verify_webhook(
        cls, instance: ProviderInstanceModel, payload: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        raise cls.cannot("sign notifications")


def _row(model: Any) -> Dict[str, Any]:
    dumped: Dict[str, Any] = model.model_dump(mode="json")
    return dumped


class EXT_Payment(AbstractStaticExtension):
    name: ClassVar[str] = "payment"
    friendly_name: ClassVar[str] = "Payments"
    version: ClassVar[str] = "2.0.0"
    description: ClassVar[str] = (
        "Payments, customers, subscriptions and signed notifications through "
        "Stripe, Square, PayPal, Helcim and Moneris"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "customer_create",
        "customer_get",
        "payment_create",
        "payment_get",
        "payment_list",
        "payment_capture",
        "payment_refund",
        "subscription_create",
        "subscription_get",
        "subscription_list",
        "subscription_cancel",
        "subscription_status",
        "webhook_process",
    }

    # Who the abilities act for, and through which records.

    @classmethod
    def _users(cls, requester_id: str) -> Any:
        from zephyrex.logic.BLL_Auth import UserManager

        return cls.as_requester(UserManager, requester_id)

    @classmethod
    def _payments(cls, requester_id: str) -> Any:
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager

        return cls.as_requester(PaymentManager, requester_id)

    @classmethod
    def _subscriptions(cls, requester_id: str) -> Any:
        from zephyrex.extensions.payment.BLL_Payment import PaymentSubscriptionManager

        return cls.as_requester(PaymentSubscriptionManager, requester_id)

    @classmethod
    def _merchant(cls, requester_id: str) -> str:
        """The merchant's side (captures, refunds) is the server's: a payer
        must never refund or capture their own payment."""
        from zephyrex.logic.AbstractLogicManager.ownership import server_side

        cls._users(requester_id)
        if not server_side(requester_id):
            raise HTTPException(
                status_code=403, detail="Only ROOT or SYSTEM act for the merchant"
            )
        from zephyrex.lib.Environment import env

        return str(env("ROOT_ID"))

    @classmethod
    def _link(cls, requester_id: str) -> Tuple[Any, Optional[Tuple[str, str]]]:
        """The requester's user record, and their customer link (instance
        and customer id) where the server made one."""
        from zephyrex.extensions.payment.BLL_Payment import linked_customer

        users = cls._users(requester_id)
        user = users.get(id=requester_id)
        return user, linked_customer(user)

    # Customers.

    @classmethod
    @ability("customer_create")
    async def customer_create(
        cls, requester_id: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
        """The requester's customer record at a provider, made (and linked
        to them) when they have none: under their own email, never one the
        caller names."""
        from zephyrex.extensions.payment.BLL_Payment import link_customer

        user, link = cls._link(requester_id)
        if link is not None:
            found: Dict[str, Any] = await cls.rotate_on_instance(
                link[0], "get_customer", link[1]
            )
            return found
        request = CustomerRequest(
            email=user.email,
            user_id=requester_id,
            idempotency_key=new_idempotency_key(),
            name=checked_text(name, "name") or user.display_name,
        )
        made: Dict[str, Any] = await cls.rotate_capable(
            "customer_create", "create_customer", request
        )
        link_customer(requester_id, made["provider_instance_id"], made["customer_id"])
        return made

    @classmethod
    @ability("customer_get")
    async def customer_get(cls, requester_id: str) -> Dict[str, Any]:
        """The requester's customer record, as the provider has it."""
        _, link = cls._link(requester_id)
        if link is None:
            raise HTTPException(status_code=404, detail="No customer record yet")
        found: Dict[str, Any] = await cls.rotate_on_instance(
            link[0], "get_customer", link[1]
        )
        return found

    # Payments.

    @classmethod
    @ability("payment_create")
    async def payment_create(
        cls,
        requester_id: str,
        amount: Union[Decimal, int, str],
        currency: str,
        payment_method_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        capture: bool = True,
        customer_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Charge the requester ``amount`` (major units) of ``currency``,
        with their payment method (a card token, a Square source) where the
        provider takes one, or to be approved by the payer (Stripe's
        ``client_secret``, PayPal's ``approval_url``). ``capture=False``
        only authorizes. On the account holding the requester's customer
        record when they have one, else the first account that can.

        The record (owned by the requester) is the answer, with the
        provider's one-time ``client_secret``/``approval_url`` beside it."""
        request = PaymentRequest(
            amount=checked_amount(amount),
            currency=checked_currency(currency),
            user_id=requester_id,
            idempotency_key=new_idempotency_key(),
            payment_method_id=checked_text(payment_method_id, "payment_method_id"),
            description=checked_text(description, "description"),
            capture=bool(capture),
            customer_ip=checked_ip(customer_ip),
            metadata=checked_metadata(metadata),
        )
        payments = cls._payments(requester_id)
        _, link = cls._link(requester_id)
        answer: Dict[str, Any]
        if link is not None:
            answer = await cls.rotate_on_instance(
                link[0],
                "create_payment",
                replace(request, customer_id=link[1]),
            )
        else:
            answer = await cls.rotate_capable(
                "payment_create", "create_payment", request
            )
        record = payments.record(answer, description=request.description)
        return {
            **_row(record),
            "client_secret": answer.get("client_secret"),
            "approval_url": answer.get("approval_url"),
        }

    @classmethod
    @ability("payment_get")
    async def payment_get(cls, requester_id: str, payment_id: str) -> Dict[str, Any]:
        """One of the requester's payments, read again from the provider."""
        payments = cls._payments(requester_id)
        record = payments.get(id=payment_id)
        answer = await cls.rotate_on_instance(
            record.provider_instance_id, "get_payment", record.external_id
        )
        return _row(payments.mirror(record, answer))

    @classmethod
    @ability("payment_list")
    async def payment_list(cls, requester_id: str) -> List[Dict[str, Any]]:
        """The requester's payments as last recorded."""
        return [_row(record) for record in cls._payments(requester_id).list()]

    @classmethod
    def _merchant_record(cls, requester_id: str, payment_id: str) -> Tuple[Any, Any]:
        from zephyrex.extensions.payment.BLL_Payment import PaymentManager

        payments = cls.as_requester(PaymentManager, cls._merchant(requester_id))
        return payments, payments.get(id=payment_id)

    @classmethod
    @ability("payment_capture")
    async def payment_capture(
        cls,
        requester_id: str,
        payment_id: str,
        amount: Optional[Union[Decimal, int, str]] = None,
        customer_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture an authorized payment (all of it, or ``amount``):
        ROOT or SYSTEM only."""
        payments, record = cls._merchant_record(requester_id, payment_id)
        action = MoneyAction(
            currency=record.currency,
            idempotency_key=new_idempotency_key(),
            amount=None if amount is None else checked_amount(amount),
            customer_ip=checked_ip(customer_ip),
        )
        answer = await cls.rotate_on_instance(
            record.provider_instance_id, "capture_payment", record.external_id, action
        )
        return _row(payments.mirror(record, answer))

    @classmethod
    @ability("payment_refund")
    async def payment_refund(
        cls,
        requester_id: str,
        payment_id: str,
        amount: Optional[Union[Decimal, int, str]] = None,
        reason: Optional[str] = None,
        customer_ip: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refund a payment (what is left of it, or ``amount``): ROOT or
        SYSTEM only. The refund, and the payment as the provider has it
        after."""
        payments, record = cls._merchant_record(requester_id, payment_id)
        action = MoneyAction(
            currency=record.currency,
            idempotency_key=new_idempotency_key(),
            amount=None if amount is None else checked_amount(amount),
            reason=checked_text(reason, "reason"),
            customer_ip=checked_ip(customer_ip),
        )
        refund: Dict[str, Any] = await cls.rotate_on_instance(
            record.provider_instance_id, "refund_payment", record.external_id, action
        )
        answer = await cls.rotate_on_instance(
            record.provider_instance_id, "get_payment", record.external_id
        )
        return {"refund": refund, "payment": _row(payments.mirror(record, answer))}

    # Subscriptions.

    @classmethod
    @ability("subscription_create")
    async def subscription_create(
        cls,
        requester_id: str,
        plan_id: str,
        trial_days: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Subscribe the requester to ``plan_id`` (a Stripe price, a Square
        plan variation, a PayPal plan): on the account holding their
        customer record, or where the provider needs none (PayPal, whose
        ``approval_url`` the payer then follows)."""
        subscriptions = cls._subscriptions(requester_id)
        _, link = cls._link(requester_id)
        request = SubscriptionRequest(
            plan_id=str(checked_text(plan_id, "plan_id") or ""),
            user_id=requester_id,
            idempotency_key=new_idempotency_key(),
            customer_id=None if link is None else link[1],
            trial_days=checked_trial(trial_days),
            metadata=checked_metadata(metadata),
        )
        if not request.plan_id:
            raise InvalidInputExternalError("plan_id names the plan")
        answer: Dict[str, Any]
        if link is not None:
            answer = await cls.rotate_on_instance(
                link[0], "create_subscription", request
            )
        else:
            customerless = [
                provider.name
                for provider in cls.providers
                if issubclass(provider, AbstractPaymentProvider)
                and "subscription_create" in provider._abilities
                and not provider.subscriptions_need_customer
            ]
            try:
                answer = await cls.rotate_among(
                    customerless, "create_subscription", request
                )
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                raise HTTPException(
                    status_code=409,
                    detail="Make a customer record first (customer_create)",
                ) from exc
        record = subscriptions.record(answer)
        return {**_row(record), "approval_url": answer.get("approval_url")}

    @classmethod
    @ability("subscription_get")
    async def subscription_get(
        cls, requester_id: str, subscription_id: str
    ) -> Dict[str, Any]:
        """One of the requester's subscriptions, read again from the
        provider."""
        subscriptions = cls._subscriptions(requester_id)
        record = subscriptions.get(id=subscription_id)
        answer = await cls.rotate_on_instance(
            record.provider_instance_id, "get_subscription", record.external_id
        )
        return _row(subscriptions.mirror(record, answer))

    @classmethod
    @ability("subscription_list")
    async def subscription_list(cls, requester_id: str) -> List[Dict[str, Any]]:
        return [_row(record) for record in cls._subscriptions(requester_id).list()]

    @classmethod
    @ability("subscription_cancel")
    async def subscription_cancel(
        cls, requester_id: str, subscription_id: str, at_period_end: bool = True
    ) -> Dict[str, Any]:
        """Cancel one of the requester's subscriptions, at the end of the
        period paid for or (``at_period_end=False``) now."""
        subscriptions = cls._subscriptions(requester_id)
        record = subscriptions.get(id=subscription_id)
        answer = await cls.rotate_on_instance(
            record.provider_instance_id,
            "cancel_subscription",
            record.external_id,
            bool(at_period_end),
        )
        return _row(subscriptions.mirror(record, answer))

    @classmethod
    @ability("subscription_status")
    async def subscription_status(cls, requester_id: str) -> Dict[str, Any]:
        """Whether any of the requester's subscriptions is active, each read
        again from its provider (the last record where one cannot be)."""
        from zephyrex.extensions.payment.BLL_Payment import refreshed

        subscriptions = cls._subscriptions(requester_id)
        records = await refreshed(subscriptions, subscriptions.list())
        return {
            "active": any(record.active for record in records),
            "subscriptions": [_row(record) for record in records],
        }

    # Notifications.

    @classmethod
    @ability("webhook_process")
    async def webhook_process(
        cls,
        instance: str,
        payload: Union[bytes, str],
        headers: Mapping[str, str],
    ) -> Dict[str, Any]:
        """A notification posted to the account ``instance`` (its id or
        name), as received: the raw body and its headers. Refused unless it
        verifies as the provider signs it; the payment or subscription it
        names is then read again from the provider and its record updated
        (``refreshed``: that record's id)."""
        from zephyrex.extensions.payment.BLL_Payment import refresh_named

        body = payload.encode() if isinstance(payload, str) else bytes(payload)
        event: Dict[str, Any] = await cls.rotate_on_instance(
            instance, "verify_webhook", body, lowered(headers)
        )
        return {**event, "refreshed": await refresh_named(cls, event)}

    @classmethod
    async def refresh_record(cls, kind: str, record: Any) -> Dict[str, Any]:
        """The provider's answer for a recorded payment or subscription."""
        method = "get_payment" if kind == "payment" else "get_subscription"
        answer: Dict[str, Any] = await cls.rotate_on_instance(
            record.provider_instance_id, method, record.external_id
        )
        return answer
