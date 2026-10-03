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


def _server_side(requester_id: str) -> bool:
    from zephyrex.database.StaticPermissions import is_root_id, is_system_id

    return is_root_id(requester_id) or is_system_id(requester_id)


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
        cls._users(requester_id)
        if not _server_side(requester_id):
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

    # Item 16 — federation matrix descriptors. The framework's programmatic
    # test generator picks these up at collection time and emits one
    # ``Test_Federation_EXT_Payment_<Type>_Matrix`` class per declared
    # external type. The schema snapshot is loaded lazily so a missing
    # snapshot doesn't break extension import.
    @classmethod
    def federation_matrix_fixtures(cls):
        """Return :class:`FederationFixture` instances for full schema coverage.

        Every resource in every provider's REST API gets a fixture. The
        generator turns each into a ``Test_Federation_EXT_Payment_<Provider>_<Type>_Matrix``
        class exercising the REST→REST and REST→GQL quadrants (the two
        that apply when the upstream is a REST API). The GQL→GQL and
        GQL→REST quadrants only apply to providers whose upstream actually
        speaks GraphQL — none of the payment providers do.

        MCP coverage is automatic: the federation bootstrap projects
        each REST type onto a local FastAPI endpoint, and
        ``MCPBridge.mount_mcp`` exposes every endpoint as an MCP tool
        from the OpenAPI schema.

        Providers and their complete API surfaces:

        * **Stripe** (37): Balance, BalanceTransaction, Charge, Customer,
          Dispute, Event, File, Mandate, PaymentIntent, SetupIntent, Payout,
          Refund, Token, PaymentMethod, Product, Price, Coupon,
          PromotionCode, TaxCode, TaxRate, ShippingRate, CheckoutSession,
          PaymentLink, CreditNote, Invoice, InvoiceItem, Plan, Quote,
          Subscription, SubscriptionItem, SubscriptionSchedule, Account,
          ApplicationFee, Transfer, TransferReversal, WebhookEndpoint
        * **Square** (31): Payment, Refund, Dispute, Checkout, Terminal,
          Invoice, Card, Subscription, BankAccount, Payout, Device, Order,
          OrderCustomAttribute, CatalogObject, InventoryCount, Booking,
          Vendor, CashDrawerShift, Customer, CustomerGroup,
          CustomerSegment, LoyaltyProgram, LoyaltyAccount, GiftCard,
          GiftCardActivity, LaborShift, TeamMember, Merchant, Location,
          WebhookSubscription, Event
        * **PayPal** (16): Order, Payment, Capture, Authorization, Refund,
          PaymentMethodToken, CatalogProduct, BillingPlan,
          BillingSubscription, Invoice, Dispute, Payout, ReferencedPayout,
          TransactionEvent, WebhookEvent, Identity
        * **Moneris** (15): Customer, Payment, Refund, Subscription, Order,
          PaymentMethod, Validation, Authentication, Merchant, Dispute,
          KonekPaymentConsent, McpRate, KountInquiry,
          OriginalCreditTransaction, Payout
        * **Helcim** (19): Customer, CustomerCard, CustomerBankAccount,
          Purchase, Preauth, Capture, Verify, CardRefund, Reverse, Invoice,
          CardBatch, CardTransaction, AchTransaction, AchBatch,
          PaymentPlan, Subscription, AddOn, CheckoutSession, Device

        All fixtures use deterministic in-process upstreams so the matrix
        runs in CI without credentials.
        """

        from zephyrex.extensions.AbstractFederationMatrixTest import (
            FederationFixture,
        )
        from zephyrex.lib.Environment import env

        def _emit(provider_label, cred_env, types_dict):
            out = []
            cred_check = lambda e=cred_env: bool(env(e))
            for type_name, cfg in types_dict.items():
                spec, seed = cls._make_spec_and_seed(type_name, cfg)
                tl = type_name.lower()
                base = f"http://{provider_label.lower()}-test"
                out.append(
                    FederationFixture(
                        name=f"EXT_Payment.{provider_label}.{type_name}",
                        upstream_kind="rest",
                        transport=cls._build_in_process_transport(
                            spec,
                            seed,
                            cfg["path"],
                            base,
                        ),
                        sample_id=cfg["sample_id"],
                        type_name=type_name,
                        sdl_or_spec=spec,
                        operations_supported=["get", "list"],
                        crud_map={"get": f"get_{tl}", "list": f"list_{tl}"},
                        requires_credentials=False,
                        credentials_present=cred_check,
                    )
                )
            return out

        fixtures = []

        # =================================================================
        # Stripe — 37 resources
        # =================================================================
        fixtures.extend(
            _emit(
                "Stripe",
                "STRIPE_API_KEY",
                {
                    "Balance": {
                        "schema": {
                            "object": "string",
                            "available": "integer",
                            "pending": "integer",
                        },
                        "path": "/v1/balance",
                        "seed": {
                            "bal_1": {
                                "id": "bal_1",
                                "object": "balance",
                                "available": 50000,
                                "pending": 1000,
                            }
                        },
                        "sample_id": "bal_1",
                    },
                    "BalanceTransaction": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "type": "string",
                            "status": "string",
                        },
                        "path": "/v1/balance_transactions",
                        "seed": {
                            "txn_1": {
                                "id": "txn_1",
                                "amount": 5000,
                                "currency": "usd",
                                "type": "charge",
                                "status": "available",
                            }
                        },
                        "sample_id": "txn_1",
                    },
                    "Charge": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "paid": "boolean",
                            "customer": "string",
                        },
                        "path": "/v1/charges",
                        "seed": {
                            "ch_1": {
                                "id": "ch_1",
                                "amount": 2000,
                                "currency": "usd",
                                "status": "succeeded",
                                "paid": True,
                                "customer": "cus_1",
                            }
                        },
                        "sample_id": "ch_1",
                    },
                    "Customer": {
                        "schema": {
                            "id": "string",
                            "email": "string",
                            "name": "string",
                            "description": "string",
                            "balance": "integer",
                        },
                        "path": "/v1/customers",
                        "seed": {
                            "cus_1": {
                                "id": "cus_1",
                                "email": "test@x.com",
                                "name": "Test",
                                "description": "A customer",
                                "balance": 0,
                            }
                        },
                        "sample_id": "cus_1",
                    },
                    "Dispute": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "reason": "string",
                            "charge": "string",
                        },
                        "path": "/v1/disputes",
                        "seed": {
                            "dp_1": {
                                "id": "dp_1",
                                "amount": 1500,
                                "currency": "usd",
                                "status": "needs_response",
                                "reason": "fraudulent",
                                "charge": "ch_1",
                            }
                        },
                        "sample_id": "dp_1",
                    },
                    "Event": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "created": "integer",
                            "livemode": "boolean",
                        },
                        "path": "/v1/events",
                        "seed": {
                            "evt_1": {
                                "id": "evt_1",
                                "type": "charge.succeeded",
                                "created": 1700000000,
                                "livemode": False,
                            }
                        },
                        "sample_id": "evt_1",
                    },
                    "File": {
                        "schema": {
                            "id": "string",
                            "purpose": "string",
                            "size": "integer",
                            "type": "string",
                        },
                        "path": "/v1/files",
                        "seed": {
                            "file_1": {
                                "id": "file_1",
                                "purpose": "dispute_evidence",
                                "size": 12345,
                                "type": "pdf",
                            }
                        },
                        "sample_id": "file_1",
                    },
                    "Mandate": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "type": "string",
                            "customer_acceptance": "string",
                        },
                        "path": "/v1/mandates",
                        "seed": {
                            "mandate_1": {
                                "id": "mandate_1",
                                "status": "active",
                                "type": "multi_use",
                                "customer_acceptance": "online",
                            }
                        },
                        "sample_id": "mandate_1",
                    },
                    "PaymentIntent": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "customer": "string",
                            "payment_method": "string",
                        },
                        "path": "/v1/payment_intents",
                        "seed": {
                            "pi_1": {
                                "id": "pi_1",
                                "amount": 5000,
                                "currency": "usd",
                                "status": "succeeded",
                                "customer": "cus_1",
                                "payment_method": "pm_1",
                            }
                        },
                        "sample_id": "pi_1",
                    },
                    "SetupIntent": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "usage": "string",
                            "customer": "string",
                            "payment_method": "string",
                        },
                        "path": "/v1/setup_intents",
                        "seed": {
                            "seti_1": {
                                "id": "seti_1",
                                "status": "succeeded",
                                "usage": "off_session",
                                "customer": "cus_1",
                                "payment_method": "pm_1",
                            }
                        },
                        "sample_id": "seti_1",
                    },
                    "Payout": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "arrival_date": "integer",
                        },
                        "path": "/v1/payouts",
                        "seed": {
                            "po_1": {
                                "id": "po_1",
                                "amount": 10000,
                                "currency": "usd",
                                "status": "paid",
                                "arrival_date": 1700000000,
                            }
                        },
                        "sample_id": "po_1",
                    },
                    "Refund": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "charge": "string",
                            "reason": "string",
                        },
                        "path": "/v1/refunds",
                        "seed": {
                            "re_1": {
                                "id": "re_1",
                                "amount": 1000,
                                "currency": "usd",
                                "status": "succeeded",
                                "charge": "ch_1",
                                "reason": "requested_by_customer",
                            }
                        },
                        "sample_id": "re_1",
                    },
                    "Token": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "used": "boolean",
                            "livemode": "boolean",
                        },
                        "path": "/v1/tokens",
                        "seed": {
                            "tok_1": {
                                "id": "tok_1",
                                "type": "card",
                                "used": False,
                                "livemode": False,
                            }
                        },
                        "sample_id": "tok_1",
                    },
                    "PaymentMethod": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "customer": "string",
                            "created": "integer",
                        },
                        "path": "/v1/payment_methods",
                        "seed": {
                            "pm_1": {
                                "id": "pm_1",
                                "type": "card",
                                "customer": "cus_1",
                                "created": 1700000000,
                            }
                        },
                        "sample_id": "pm_1",
                    },
                    "Product": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "active": "boolean",
                            "description": "string",
                            "default_price": "string",
                        },
                        "path": "/v1/products",
                        "seed": {
                            "prod_1": {
                                "id": "prod_1",
                                "name": "Widget",
                                "active": True,
                                "description": "A widget",
                                "default_price": "price_1",
                            }
                        },
                        "sample_id": "prod_1",
                    },
                    "Price": {
                        "schema": {
                            "id": "string",
                            "unit_amount": "integer",
                            "currency": "string",
                            "product": "string",
                            "active": "boolean",
                            "type": "string",
                        },
                        "path": "/v1/prices",
                        "seed": {
                            "price_1": {
                                "id": "price_1",
                                "unit_amount": 2000,
                                "currency": "usd",
                                "product": "prod_1",
                                "active": True,
                                "type": "one_time",
                            }
                        },
                        "sample_id": "price_1",
                    },
                    "Coupon": {
                        "schema": {
                            "id": "string",
                            "percent_off": "number",
                            "duration": "string",
                            "valid": "boolean",
                        },
                        "path": "/v1/coupons",
                        "seed": {
                            "cpn_1": {
                                "id": "cpn_1",
                                "percent_off": 25.0,
                                "duration": "repeating",
                                "valid": True,
                            }
                        },
                        "sample_id": "cpn_1",
                    },
                    "PromotionCode": {
                        "schema": {
                            "id": "string",
                            "code": "string",
                            "active": "boolean",
                            "coupon": "string",
                        },
                        "path": "/v1/promotion_codes",
                        "seed": {
                            "promo_1": {
                                "id": "promo_1",
                                "code": "SAVE25",
                                "active": True,
                                "coupon": "cpn_1",
                            }
                        },
                        "sample_id": "promo_1",
                    },
                    "TaxCode": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "description": "string",
                        },
                        "path": "/v1/tax_codes",
                        "seed": {
                            "txcd_1": {
                                "id": "txcd_1",
                                "name": "General - Tangible Goods",
                                "description": "Physical goods",
                            }
                        },
                        "sample_id": "txcd_1",
                    },
                    "TaxRate": {
                        "schema": {
                            "id": "string",
                            "display_name": "string",
                            "percentage": "number",
                            "inclusive": "boolean",
                            "active": "boolean",
                        },
                        "path": "/v1/tax_rates",
                        "seed": {
                            "txr_1": {
                                "id": "txr_1",
                                "display_name": "Sales Tax",
                                "percentage": 8.25,
                                "inclusive": False,
                                "active": True,
                            }
                        },
                        "sample_id": "txr_1",
                    },
                    "ShippingRate": {
                        "schema": {
                            "id": "string",
                            "display_name": "string",
                            "active": "boolean",
                            "type": "string",
                        },
                        "path": "/v1/shipping_rates",
                        "seed": {
                            "shr_1": {
                                "id": "shr_1",
                                "display_name": "Standard",
                                "active": True,
                                "type": "fixed_amount",
                            }
                        },
                        "sample_id": "shr_1",
                    },
                    "CheckoutSession": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "mode": "string",
                            "currency": "string",
                            "amount_total": "integer",
                            "customer": "string",
                        },
                        "path": "/v1/checkout/sessions",
                        "seed": {
                            "cs_1": {
                                "id": "cs_1",
                                "status": "complete",
                                "mode": "payment",
                                "currency": "usd",
                                "amount_total": 5000,
                                "customer": "cus_1",
                            }
                        },
                        "sample_id": "cs_1",
                    },
                    "PaymentLink": {
                        "schema": {
                            "id": "string",
                            "active": "boolean",
                            "url": "string",
                        },
                        "path": "/v1/payment_links",
                        "seed": {
                            "plink_1": {
                                "id": "plink_1",
                                "active": True,
                                "url": "https://pay.stripe.com/test",
                            }
                        },
                        "sample_id": "plink_1",
                    },
                    "CreditNote": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "status": "string",
                            "invoice": "string",
                        },
                        "path": "/v1/credit_notes",
                        "seed": {
                            "cn_1": {
                                "id": "cn_1",
                                "amount": 500,
                                "currency": "usd",
                                "status": "issued",
                                "invoice": "in_1",
                            }
                        },
                        "sample_id": "cn_1",
                    },
                    "Invoice": {
                        "schema": {
                            "id": "string",
                            "amount_due": "integer",
                            "currency": "string",
                            "status": "string",
                            "customer": "string",
                            "subscription": "string",
                        },
                        "path": "/v1/invoices",
                        "seed": {
                            "in_1": {
                                "id": "in_1",
                                "amount_due": 5000,
                                "currency": "usd",
                                "status": "paid",
                                "customer": "cus_1",
                                "subscription": "sub_1",
                            }
                        },
                        "sample_id": "in_1",
                    },
                    "InvoiceItem": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "customer": "string",
                            "description": "string",
                        },
                        "path": "/v1/invoiceitems",
                        "seed": {
                            "ii_1": {
                                "id": "ii_1",
                                "amount": 1000,
                                "currency": "usd",
                                "customer": "cus_1",
                                "description": "Extra charge",
                            }
                        },
                        "sample_id": "ii_1",
                    },
                    "Plan": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "interval": "string",
                            "product": "string",
                            "active": "boolean",
                        },
                        "path": "/v1/plans",
                        "seed": {
                            "plan_1": {
                                "id": "plan_1",
                                "amount": 2000,
                                "currency": "usd",
                                "interval": "month",
                                "product": "prod_1",
                                "active": True,
                            }
                        },
                        "sample_id": "plan_1",
                    },
                    "Quote": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_total": "integer",
                            "currency": "string",
                            "customer": "string",
                        },
                        "path": "/v1/quotes",
                        "seed": {
                            "qt_1": {
                                "id": "qt_1",
                                "status": "draft",
                                "amount_total": 10000,
                                "currency": "usd",
                                "customer": "cus_1",
                            }
                        },
                        "sample_id": "qt_1",
                    },
                    "Subscription": {
                        "schema": {
                            "id": "string",
                            "customer": "string",
                            "status": "string",
                            "current_period_end": "integer",
                            "cancel_at_period_end": "boolean",
                        },
                        "path": "/v1/subscriptions",
                        "seed": {
                            "sub_1": {
                                "id": "sub_1",
                                "customer": "cus_1",
                                "status": "active",
                                "current_period_end": 1700000000,
                                "cancel_at_period_end": False,
                            }
                        },
                        "sample_id": "sub_1",
                    },
                    "SubscriptionItem": {
                        "schema": {
                            "id": "string",
                            "subscription": "string",
                            "price": "string",
                            "quantity": "integer",
                        },
                        "path": "/v1/subscription_items",
                        "seed": {
                            "si_1": {
                                "id": "si_1",
                                "subscription": "sub_1",
                                "price": "price_1",
                                "quantity": 1,
                            }
                        },
                        "sample_id": "si_1",
                    },
                    "SubscriptionSchedule": {
                        "schema": {
                            "id": "string",
                            "customer": "string",
                            "status": "string",
                            "subscription": "string",
                        },
                        "path": "/v1/subscription_schedules",
                        "seed": {
                            "sub_sched_1": {
                                "id": "sub_sched_1",
                                "customer": "cus_1",
                                "status": "active",
                                "subscription": "sub_1",
                            }
                        },
                        "sample_id": "sub_sched_1",
                    },
                    "Account": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "country": "string",
                            "email": "string",
                            "charges_enabled": "boolean",
                            "payouts_enabled": "boolean",
                        },
                        "path": "/v1/accounts",
                        "seed": {
                            "acct_1": {
                                "id": "acct_1",
                                "type": "standard",
                                "country": "US",
                                "email": "acct@x.com",
                                "charges_enabled": True,
                                "payouts_enabled": True,
                            }
                        },
                        "sample_id": "acct_1",
                    },
                    "ApplicationFee": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "charge": "string",
                            "refunded": "boolean",
                        },
                        "path": "/v1/application_fees",
                        "seed": {
                            "fee_1": {
                                "id": "fee_1",
                                "amount": 100,
                                "currency": "usd",
                                "charge": "ch_1",
                                "refunded": False,
                            }
                        },
                        "sample_id": "fee_1",
                    },
                    "Transfer": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "destination": "string",
                            "reversed": "boolean",
                        },
                        "path": "/v1/transfers",
                        "seed": {
                            "tr_1": {
                                "id": "tr_1",
                                "amount": 5000,
                                "currency": "usd",
                                "destination": "acct_1",
                                "reversed": False,
                            }
                        },
                        "sample_id": "tr_1",
                    },
                    "TransferReversal": {
                        "schema": {
                            "id": "string",
                            "amount": "integer",
                            "currency": "string",
                            "transfer": "string",
                        },
                        "path": "/v1/transfers/tr_1/reversals",
                        "seed": {
                            "trr_1": {
                                "id": "trr_1",
                                "amount": 5000,
                                "currency": "usd",
                                "transfer": "tr_1",
                            }
                        },
                        "sample_id": "trr_1",
                    },
                    "WebhookEndpoint": {
                        "schema": {
                            "id": "string",
                            "url": "string",
                            "status": "string",
                            "enabled_events": "string",
                        },
                        "path": "/v1/webhook_endpoints",
                        "seed": {
                            "we_1": {
                                "id": "we_1",
                                "url": "https://example.com/hook",
                                "status": "enabled",
                                "enabled_events": "charge.succeeded",
                            }
                        },
                        "sample_id": "we_1",
                    },
                },
            )
        )

        # =================================================================
        # Square — 31 resources
        # =================================================================
        fixtures.extend(
            _emit(
                "Square",
                "SQUARE_ACCESS_TOKEN",
                {
                    "Payment": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "source_type": "string",
                            "total_money_amount": "integer",
                            "total_money_currency": "string",
                        },
                        "path": "/v2/payments",
                        "seed": {
                            "sq_pay": {
                                "id": "sq_pay",
                                "status": "COMPLETED",
                                "source_type": "CARD",
                                "total_money_amount": 5000,
                                "total_money_currency": "USD",
                            }
                        },
                        "sample_id": "sq_pay",
                    },
                    "Refund": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "payment_id": "string",
                            "amount_money_amount": "integer",
                        },
                        "path": "/v2/refunds",
                        "seed": {
                            "sq_ref": {
                                "id": "sq_ref",
                                "status": "COMPLETED",
                                "payment_id": "sq_pay",
                                "amount_money_amount": 1000,
                            }
                        },
                        "sample_id": "sq_ref",
                    },
                    "Dispute": {
                        "schema": {
                            "id": "string",
                            "state": "string",
                            "reason": "string",
                            "disputed_payment_id": "string",
                        },
                        "path": "/v2/disputes",
                        "seed": {
                            "sq_dsp": {
                                "id": "sq_dsp",
                                "state": "INQUIRY_EVIDENCE_REQUIRED",
                                "reason": "AMOUNT_DIFFERS",
                                "disputed_payment_id": "sq_pay",
                            }
                        },
                        "sample_id": "sq_dsp",
                    },
                    "Checkout": {
                        "schema": {
                            "id": "string",
                            "checkout_page_url": "string",
                            "order_id": "string",
                        },
                        "path": "/v2/online-checkout/payment-links",
                        "seed": {
                            "sq_chk": {
                                "id": "sq_chk",
                                "checkout_page_url": "https://square.link/test",
                                "order_id": "sq_ord",
                            }
                        },
                        "sample_id": "sq_chk",
                    },
                    "Terminal": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "device_id": "string",
                        },
                        "path": "/v2/terminals/checkouts",
                        "seed": {
                            "sq_term": {
                                "id": "sq_term",
                                "status": "COMPLETED",
                                "device_id": "sq_dev",
                            }
                        },
                        "sample_id": "sq_term",
                    },
                    "Invoice": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "order_id": "string",
                            "scheduled_at": "string",
                        },
                        "path": "/v2/invoices",
                        "seed": {
                            "sq_inv": {
                                "id": "sq_inv",
                                "status": "PAID",
                                "order_id": "sq_ord",
                                "scheduled_at": "2025-01-01T00:00:00Z",
                            }
                        },
                        "sample_id": "sq_inv",
                    },
                    "Card": {
                        "schema": {
                            "id": "string",
                            "card_brand": "string",
                            "last_4": "string",
                            "exp_month": "integer",
                            "exp_year": "integer",
                            "customer_id": "string",
                        },
                        "path": "/v2/cards",
                        "seed": {
                            "sq_card": {
                                "id": "sq_card",
                                "card_brand": "VISA",
                                "last_4": "4242",
                                "exp_month": 12,
                                "exp_year": 2026,
                                "customer_id": "sq_cust",
                            }
                        },
                        "sample_id": "sq_card",
                    },
                    "Subscription": {
                        "schema": {
                            "id": "string",
                            "customer_id": "string",
                            "plan_variation_id": "string",
                            "status": "string",
                        },
                        "path": "/v2/subscriptions",
                        "seed": {
                            "sq_sub": {
                                "id": "sq_sub",
                                "customer_id": "sq_cust",
                                "plan_variation_id": "plan_var_1",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "sq_sub",
                    },
                    "BankAccount": {
                        "schema": {
                            "id": "string",
                            "account_number_suffix": "string",
                            "country": "string",
                            "currency": "string",
                            "status": "string",
                        },
                        "path": "/v2/bank-accounts",
                        "seed": {
                            "sq_ba": {
                                "id": "sq_ba",
                                "account_number_suffix": "6789",
                                "country": "US",
                                "currency": "USD",
                                "status": "VERIFICATION_IN_PROGRESS",
                            }
                        },
                        "sample_id": "sq_ba",
                    },
                    "Payout": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_money_amount": "integer",
                            "amount_money_currency": "string",
                        },
                        "path": "/v2/payouts",
                        "seed": {
                            "sq_po": {
                                "id": "sq_po",
                                "status": "PAID",
                                "amount_money_amount": 50000,
                                "amount_money_currency": "USD",
                            }
                        },
                        "sample_id": "sq_po",
                    },
                    "Device": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "status": "string",
                            "product_type": "string",
                        },
                        "path": "/v2/devices",
                        "seed": {
                            "sq_dev": {
                                "id": "sq_dev",
                                "name": "Counter POS",
                                "status": "ACTIVE",
                                "product_type": "TERMINAL_API",
                            }
                        },
                        "sample_id": "sq_dev",
                    },
                    "Order": {
                        "schema": {
                            "id": "string",
                            "state": "string",
                            "total_money_amount": "integer",
                            "total_money_currency": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/orders",
                        "seed": {
                            "sq_ord": {
                                "id": "sq_ord",
                                "state": "COMPLETED",
                                "total_money_amount": 5000,
                                "total_money_currency": "USD",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_ord",
                    },
                    "OrderCustomAttribute": {
                        "schema": {
                            "id": "string",
                            "key": "string",
                            "value": "string",
                            "order_id": "string",
                        },
                        "path": "/v2/orders/custom-attributes",
                        "seed": {
                            "sq_oca": {
                                "id": "sq_oca",
                                "key": "delivery_note",
                                "value": "Leave at door",
                                "order_id": "sq_ord",
                            }
                        },
                        "sample_id": "sq_oca",
                    },
                    "CatalogObject": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "name": "string",
                            "is_deleted": "boolean",
                        },
                        "path": "/v2/catalog/object",
                        "seed": {
                            "sq_cat": {
                                "id": "sq_cat",
                                "type": "ITEM",
                                "name": "Coffee",
                                "is_deleted": False,
                            }
                        },
                        "sample_id": "sq_cat",
                    },
                    "InventoryCount": {
                        "schema": {
                            "id": "string",
                            "catalog_object_id": "string",
                            "state": "string",
                            "quantity": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/inventory",
                        "seed": {
                            "sq_inv_c": {
                                "id": "sq_inv_c",
                                "catalog_object_id": "sq_cat",
                                "state": "IN_STOCK",
                                "quantity": "42",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_inv_c",
                    },
                    "Booking": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "start_at": "string",
                            "customer_id": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/bookings",
                        "seed": {
                            "sq_bk": {
                                "id": "sq_bk",
                                "status": "ACCEPTED",
                                "start_at": "2025-01-15T10:00:00Z",
                                "customer_id": "sq_cust",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_bk",
                    },
                    "Vendor": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "status": "string",
                            "account_number": "string",
                        },
                        "path": "/v2/vendors",
                        "seed": {
                            "sq_vnd": {
                                "id": "sq_vnd",
                                "name": "Acme Supply Co",
                                "status": "ACTIVE",
                                "account_number": "V-100",
                            }
                        },
                        "sample_id": "sq_vnd",
                    },
                    "CashDrawerShift": {
                        "schema": {
                            "id": "string",
                            "state": "string",
                            "opened_at": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/cash-drawers/shifts",
                        "seed": {
                            "sq_cds": {
                                "id": "sq_cds",
                                "state": "CLOSED",
                                "opened_at": "2025-01-15T08:00:00Z",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_cds",
                    },
                    "Customer": {
                        "schema": {
                            "id": "string",
                            "email_address": "string",
                            "given_name": "string",
                            "family_name": "string",
                            "phone_number": "string",
                        },
                        "path": "/v2/customers",
                        "seed": {
                            "sq_cust": {
                                "id": "sq_cust",
                                "email_address": "test@x.com",
                                "given_name": "Test",
                                "family_name": "User",
                                "phone_number": "+15551234567",
                            }
                        },
                        "sample_id": "sq_cust",
                    },
                    "CustomerGroup": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "created_at": "string",
                        },
                        "path": "/v2/customers/groups",
                        "seed": {
                            "sq_cg": {
                                "id": "sq_cg",
                                "name": "VIP",
                                "created_at": "2025-01-01T00:00:00Z",
                            }
                        },
                        "sample_id": "sq_cg",
                    },
                    "CustomerSegment": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "created_at": "string",
                        },
                        "path": "/v2/customers/segments",
                        "seed": {
                            "sq_seg": {
                                "id": "sq_seg",
                                "name": "Repeat Buyers",
                                "created_at": "2025-01-01T00:00:00Z",
                            }
                        },
                        "sample_id": "sq_seg",
                    },
                    "LoyaltyProgram": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "terminology_one": "string",
                        },
                        "path": "/v2/loyalty/programs",
                        "seed": {
                            "sq_lp": {
                                "id": "sq_lp",
                                "status": "ACTIVE",
                                "terminology_one": "Point",
                            }
                        },
                        "sample_id": "sq_lp",
                    },
                    "LoyaltyAccount": {
                        "schema": {
                            "id": "string",
                            "program_id": "string",
                            "balance": "integer",
                            "customer_id": "string",
                        },
                        "path": "/v2/loyalty/accounts",
                        "seed": {
                            "sq_la": {
                                "id": "sq_la",
                                "program_id": "sq_lp",
                                "balance": 500,
                                "customer_id": "sq_cust",
                            }
                        },
                        "sample_id": "sq_la",
                    },
                    "GiftCard": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "state": "string",
                            "balance_money_amount": "integer",
                        },
                        "path": "/v2/gift-cards",
                        "seed": {
                            "sq_gc": {
                                "id": "sq_gc",
                                "type": "DIGITAL",
                                "state": "ACTIVE",
                                "balance_money_amount": 5000,
                            }
                        },
                        "sample_id": "sq_gc",
                    },
                    "GiftCardActivity": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "gift_card_id": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/gift-cards/activities",
                        "seed": {
                            "sq_gca": {
                                "id": "sq_gca",
                                "type": "ACTIVATE",
                                "gift_card_id": "sq_gc",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_gca",
                    },
                    "LaborShift": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "team_member_id": "string",
                            "start_at": "string",
                            "location_id": "string",
                        },
                        "path": "/v2/labor/shifts",
                        "seed": {
                            "sq_ls": {
                                "id": "sq_ls",
                                "status": "CLOSED",
                                "team_member_id": "sq_tm",
                                "start_at": "2025-01-15T09:00:00Z",
                                "location_id": "loc_1",
                            }
                        },
                        "sample_id": "sq_ls",
                    },
                    "TeamMember": {
                        "schema": {
                            "id": "string",
                            "given_name": "string",
                            "family_name": "string",
                            "email_address": "string",
                            "status": "string",
                        },
                        "path": "/v2/team-members",
                        "seed": {
                            "sq_tm": {
                                "id": "sq_tm",
                                "given_name": "Alice",
                                "family_name": "Smith",
                                "email_address": "alice@x.com",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "sq_tm",
                    },
                    "Merchant": {
                        "schema": {
                            "id": "string",
                            "business_name": "string",
                            "country": "string",
                            "currency": "string",
                            "status": "string",
                        },
                        "path": "/v2/merchants",
                        "seed": {
                            "sq_mrc": {
                                "id": "sq_mrc",
                                "business_name": "Test Shop",
                                "country": "US",
                                "currency": "USD",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "sq_mrc",
                    },
                    "Location": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "status": "string",
                            "country": "string",
                            "currency": "string",
                        },
                        "path": "/v2/locations",
                        "seed": {
                            "loc_1": {
                                "id": "loc_1",
                                "name": "Main Store",
                                "status": "ACTIVE",
                                "country": "US",
                                "currency": "USD",
                            }
                        },
                        "sample_id": "loc_1",
                    },
                    "WebhookSubscription": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "enabled": "boolean",
                            "notification_url": "string",
                        },
                        "path": "/v2/webhooks/subscriptions",
                        "seed": {
                            "sq_wh": {
                                "id": "sq_wh",
                                "name": "Payment Events",
                                "enabled": True,
                                "notification_url": "https://example.com/hook",
                            }
                        },
                        "sample_id": "sq_wh",
                    },
                    "Event": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "merchant_id": "string",
                            "created_at": "string",
                        },
                        "path": "/v2/events",
                        "seed": {
                            "sq_evt": {
                                "id": "sq_evt",
                                "type": "payment.completed",
                                "merchant_id": "sq_mrc",
                                "created_at": "2025-01-15T12:00:00Z",
                            }
                        },
                        "sample_id": "sq_evt",
                    },
                },
            )
        )

        # =================================================================
        # PayPal — 16 resources
        # =================================================================
        fixtures.extend(
            _emit(
                "PayPal",
                "PAYPAL_CLIENT_ID",
                {
                    "Order": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "intent": "string",
                            "create_time": "string",
                        },
                        "path": "/v2/checkout/orders",
                        "seed": {
                            "pp_ord": {
                                "id": "pp_ord",
                                "status": "COMPLETED",
                                "intent": "CAPTURE",
                                "create_time": "2025-01-15T10:00:00Z",
                            }
                        },
                        "sample_id": "pp_ord",
                    },
                    "Payment": {
                        "schema": {
                            "id": "string",
                            "state": "string",
                            "intent": "string",
                            "cart": "string",
                        },
                        "path": "/v1/payments/payment",
                        "seed": {
                            "pp_pay": {
                                "id": "pp_pay",
                                "state": "approved",
                                "intent": "sale",
                                "cart": "cart_1",
                            }
                        },
                        "sample_id": "pp_pay",
                    },
                    "Capture": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_value": "string",
                            "amount_currency_code": "string",
                        },
                        "path": "/v2/payments/captures",
                        "seed": {
                            "pp_cap": {
                                "id": "pp_cap",
                                "status": "COMPLETED",
                                "amount_value": "50.00",
                                "amount_currency_code": "USD",
                            }
                        },
                        "sample_id": "pp_cap",
                    },
                    "Authorization": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_value": "string",
                            "amount_currency_code": "string",
                        },
                        "path": "/v2/payments/authorizations",
                        "seed": {
                            "pp_auth": {
                                "id": "pp_auth",
                                "status": "CREATED",
                                "amount_value": "75.00",
                                "amount_currency_code": "USD",
                            }
                        },
                        "sample_id": "pp_auth",
                    },
                    "Refund": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_value": "string",
                            "amount_currency_code": "string",
                        },
                        "path": "/v2/payments/refunds",
                        "seed": {
                            "pp_ref": {
                                "id": "pp_ref",
                                "status": "COMPLETED",
                                "amount_value": "25.00",
                                "amount_currency_code": "USD",
                            }
                        },
                        "sample_id": "pp_ref",
                    },
                    "PaymentMethodToken": {
                        "schema": {
                            "id": "string",
                            "payment_source_type": "string",
                            "customer_id": "string",
                        },
                        "path": "/v3/vault/payment-tokens",
                        "seed": {
                            "pp_pmt": {
                                "id": "pp_pmt",
                                "payment_source_type": "CARD",
                                "customer_id": "pp_cust",
                            }
                        },
                        "sample_id": "pp_pmt",
                    },
                    "CatalogProduct": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "type": "string",
                            "description": "string",
                        },
                        "path": "/v1/catalogs/products",
                        "seed": {
                            "pp_prod": {
                                "id": "pp_prod",
                                "name": "Premium Plan",
                                "type": "SERVICE",
                                "description": "Premium monthly plan",
                            }
                        },
                        "sample_id": "pp_prod",
                    },
                    "BillingPlan": {
                        "schema": {
                            "id": "string",
                            "product_id": "string",
                            "name": "string",
                            "status": "string",
                        },
                        "path": "/v1/billing/plans",
                        "seed": {
                            "pp_plan": {
                                "id": "pp_plan",
                                "product_id": "pp_prod",
                                "name": "Monthly",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "pp_plan",
                    },
                    "BillingSubscription": {
                        "schema": {
                            "id": "string",
                            "plan_id": "string",
                            "status": "string",
                            "subscriber_email": "string",
                        },
                        "path": "/v1/billing/subscriptions",
                        "seed": {
                            "pp_sub": {
                                "id": "pp_sub",
                                "plan_id": "pp_plan",
                                "status": "ACTIVE",
                                "subscriber_email": "test@x.com",
                            }
                        },
                        "sample_id": "pp_sub",
                    },
                    "Invoice": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_value": "string",
                            "amount_currency_code": "string",
                        },
                        "path": "/v2/invoicing/invoices",
                        "seed": {
                            "pp_inv": {
                                "id": "pp_inv",
                                "status": "PAID",
                                "amount_value": "100.00",
                                "amount_currency_code": "USD",
                            }
                        },
                        "sample_id": "pp_inv",
                    },
                    "Dispute": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "reason": "string",
                            "dispute_amount_value": "string",
                        },
                        "path": "/v1/customer/disputes",
                        "seed": {
                            "pp_dsp": {
                                "id": "pp_dsp",
                                "status": "OPEN",
                                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                                "dispute_amount_value": "50.00",
                            }
                        },
                        "sample_id": "pp_dsp",
                    },
                    "Payout": {
                        "schema": {
                            "id": "string",
                            "batch_status": "string",
                            "sender_batch_id": "string",
                        },
                        "path": "/v1/payments/payouts",
                        "seed": {
                            "pp_po": {
                                "id": "pp_po",
                                "batch_status": "SUCCESS",
                                "sender_batch_id": "batch_1",
                            }
                        },
                        "sample_id": "pp_po",
                    },
                    "ReferencedPayout": {
                        "schema": {
                            "id": "string",
                            "processing_state_status": "string",
                            "payout_amount_value": "string",
                        },
                        "path": "/v1/payments/referenced-payouts",
                        "seed": {
                            "pp_rpo": {
                                "id": "pp_rpo",
                                "processing_state_status": "SUCCESS",
                                "payout_amount_value": "200.00",
                            }
                        },
                        "sample_id": "pp_rpo",
                    },
                    "TransactionEvent": {
                        "schema": {
                            "id": "string",
                            "transaction_id": "string",
                            "transaction_event_code": "string",
                            "transaction_amount_value": "string",
                        },
                        "path": "/v1/reporting/transactions",
                        "seed": {
                            "pp_txe": {
                                "id": "pp_txe",
                                "transaction_id": "txn_1",
                                "transaction_event_code": "T0006",
                                "transaction_amount_value": "50.00",
                            }
                        },
                        "sample_id": "pp_txe",
                    },
                    "WebhookEvent": {
                        "schema": {
                            "id": "string",
                            "event_type": "string",
                            "resource_type": "string",
                            "create_time": "string",
                        },
                        "path": "/v1/notifications/webhooks-events",
                        "seed": {
                            "pp_whe": {
                                "id": "pp_whe",
                                "event_type": "PAYMENT.CAPTURE.COMPLETED",
                                "resource_type": "capture",
                                "create_time": "2025-01-15T12:00:00Z",
                            }
                        },
                        "sample_id": "pp_whe",
                    },
                    "Identity": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "email": "string",
                            "payer_id": "string",
                        },
                        "path": "/v1/identity/openidconnect/userinfo",
                        "seed": {
                            "pp_id": {
                                "id": "pp_id",
                                "name": "Test User",
                                "email": "test@x.com",
                                "payer_id": "PAYER123",
                            }
                        },
                        "sample_id": "pp_id",
                    },
                },
            )
        )

        # =================================================================
        # Moneris — 15 resources
        # =================================================================
        fixtures.extend(
            _emit(
                "Moneris",
                "MONERIS_STORE_ID",
                {
                    "Customer": {
                        "schema": {
                            "id": "string",
                            "email": "string",
                            "firstName": "string",
                            "lastName": "string",
                            "phone": "string",
                        },
                        "path": "/customers",
                        "seed": {
                            "mn_cust": {
                                "id": "mn_cust",
                                "email": "test@x.com",
                                "firstName": "Test",
                                "lastName": "User",
                                "phone": "+15551234567",
                            }
                        },
                        "sample_id": "mn_cust",
                    },
                    "Payment": {
                        "schema": {
                            "id": "string",
                            "paymentStatus": "string",
                            "orderId": "string",
                            "amount_amount": "integer",
                            "amount_currency": "string",
                        },
                        "path": "/payments",
                        "seed": {
                            "mn_pay": {
                                "id": "mn_pay",
                                "paymentStatus": "SUCCEEDED",
                                "orderId": "ord-1",
                                "amount_amount": 17500,
                                "amount_currency": "CAD",
                            }
                        },
                        "sample_id": "mn_pay",
                    },
                    "Refund": {
                        "schema": {
                            "id": "string",
                            "refundStatus": "string",
                            "paymentId": "string",
                            "refundAmount_amount": "integer",
                            "reason": "string",
                        },
                        "path": "/refunds",
                        "seed": {
                            "mn_ref": {
                                "id": "mn_ref",
                                "refundStatus": "SUCCEEDED",
                                "paymentId": "mn_pay",
                                "refundAmount_amount": 5000,
                                "reason": "Defective",
                            }
                        },
                        "sample_id": "mn_ref",
                    },
                    "Subscription": {
                        "schema": {
                            "id": "string",
                            "customerId": "string",
                            "status": "string",
                            "planId": "string",
                        },
                        "path": "/subscriptions",
                        "seed": {
                            "mn_sub": {
                                "id": "mn_sub",
                                "customerId": "mn_cust",
                                "status": "ACTIVE",
                                "planId": "plan_1",
                            }
                        },
                        "sample_id": "mn_sub",
                    },
                    "Order": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "totalAmount_amount": "integer",
                            "totalAmount_currency": "string",
                        },
                        "path": "/orders",
                        "seed": {
                            "mn_ord": {
                                "id": "mn_ord",
                                "status": "CONFIRMED",
                                "totalAmount_amount": 25000,
                                "totalAmount_currency": "CAD",
                            }
                        },
                        "sample_id": "mn_ord",
                    },
                    "PaymentMethod": {
                        "schema": {
                            "id": "string",
                            "type": "string",
                            "cardBrand": "string",
                            "lastFour": "string",
                            "expiryMonth": "integer",
                            "expiryYear": "integer",
                        },
                        "path": "/payment-methods",
                        "seed": {
                            "mn_pm": {
                                "id": "mn_pm",
                                "type": "CARD",
                                "cardBrand": "VISA",
                                "lastFour": "4242",
                                "expiryMonth": 12,
                                "expiryYear": 2026,
                            }
                        },
                        "sample_id": "mn_pm",
                    },
                    "Validation": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "cardBrand": "string",
                            "lastFour": "string",
                        },
                        "path": "/validations",
                        "seed": {
                            "mn_val": {
                                "id": "mn_val",
                                "status": "APPROVED",
                                "cardBrand": "VISA",
                                "lastFour": "4242",
                            }
                        },
                        "sample_id": "mn_val",
                    },
                    "Authentication": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "threeDSecureVersion": "string",
                            "eci": "string",
                        },
                        "path": "/authentications",
                        "seed": {
                            "mn_auth": {
                                "id": "mn_auth",
                                "status": "COMPLETED",
                                "threeDSecureVersion": "2.2.0",
                                "eci": "05",
                            }
                        },
                        "sample_id": "mn_auth",
                    },
                    "Merchant": {
                        "schema": {
                            "id": "string",
                            "businessName": "string",
                            "status": "string",
                            "country": "string",
                        },
                        "path": "/merchants",
                        "seed": {
                            "mn_mrc": {
                                "id": "mn_mrc",
                                "businessName": "Test Store",
                                "status": "ACTIVE",
                                "country": "CA",
                            }
                        },
                        "sample_id": "mn_mrc",
                    },
                    "Dispute": {
                        "schema": {
                            "id": "string",
                            "caseId": "string",
                            "status": "string",
                            "amount": "integer",
                        },
                        "path": "/disputes",
                        "seed": {
                            "mn_dsp": {
                                "id": "mn_dsp",
                                "caseId": "CASE-001",
                                "status": "OPEN",
                                "amount": 5000,
                            }
                        },
                        "sample_id": "mn_dsp",
                    },
                    "KonekPaymentConsent": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "customerId": "string",
                            "consentType": "string",
                        },
                        "path": "/konek/payment-consents",
                        "seed": {
                            "mn_kpc": {
                                "id": "mn_kpc",
                                "status": "ACTIVE",
                                "customerId": "mn_cust",
                                "consentType": "RECURRING",
                            }
                        },
                        "sample_id": "mn_kpc",
                    },
                    "McpRate": {
                        "schema": {
                            "id": "string",
                            "baseCurrency": "string",
                            "foreignCurrency": "string",
                            "exchangeRate": "number",
                        },
                        "path": "/mcp/rates",
                        "seed": {
                            "mn_mcp": {
                                "id": "mn_mcp",
                                "baseCurrency": "CAD",
                                "foreignCurrency": "USD",
                                "exchangeRate": 0.74,
                            }
                        },
                        "sample_id": "mn_mcp",
                    },
                    "KountInquiry": {
                        "schema": {
                            "id": "string",
                            "score": "integer",
                            "decision": "string",
                            "orderId": "string",
                        },
                        "path": "/kount/inquiries",
                        "seed": {
                            "mn_ki": {
                                "id": "mn_ki",
                                "score": 25,
                                "decision": "APPROVE",
                                "orderId": "mn_ord",
                            }
                        },
                        "sample_id": "mn_ki",
                    },
                    "OriginalCreditTransaction": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_amount": "integer",
                            "amount_currency": "string",
                        },
                        "path": "/original-credit-transactions",
                        "seed": {
                            "mn_oct": {
                                "id": "mn_oct",
                                "status": "SUCCEEDED",
                                "amount_amount": 10000,
                                "amount_currency": "CAD",
                            }
                        },
                        "sample_id": "mn_oct",
                    },
                    "Payout": {
                        "schema": {
                            "id": "string",
                            "status": "string",
                            "amount_amount": "integer",
                            "amount_currency": "string",
                        },
                        "path": "/payouts",
                        "seed": {
                            "mn_po": {
                                "id": "mn_po",
                                "status": "COMPLETED",
                                "amount_amount": 50000,
                                "amount_currency": "CAD",
                            }
                        },
                        "sample_id": "mn_po",
                    },
                },
            )
        )

        # =================================================================
        # Helcim — 19 resources
        # =================================================================
        fixtures.extend(
            _emit(
                "Helcim",
                "HELCIM_API_TOKEN",
                {
                    "Customer": {
                        "schema": {
                            "id": "string",
                            "contactEmail": "string",
                            "contactName": "string",
                            "contactPhone": "string",
                        },
                        "path": "/customers",
                        "seed": {
                            "hc_cust": {
                                "id": "hc_cust",
                                "contactEmail": "test@x.com",
                                "contactName": "Test User",
                                "contactPhone": "+15551234567",
                            }
                        },
                        "sample_id": "hc_cust",
                    },
                    "CustomerCard": {
                        "schema": {
                            "id": "string",
                            "customerId": "string",
                            "cardToken": "string",
                            "cardType": "string",
                            "lastFour": "string",
                            "expiryDate": "string",
                        },
                        "path": "/customers/cards",
                        "seed": {
                            "hc_cc": {
                                "id": "hc_cc",
                                "customerId": "hc_cust",
                                "cardToken": "tok_1",
                                "cardType": "Visa",
                                "lastFour": "4242",
                                "expiryDate": "1226",
                            }
                        },
                        "sample_id": "hc_cc",
                    },
                    "CustomerBankAccount": {
                        "schema": {
                            "id": "string",
                            "customerId": "string",
                            "bankAccountToken": "string",
                            "bankName": "string",
                            "transitNumber": "string",
                        },
                        "path": "/customers/bank-accounts",
                        "seed": {
                            "hc_ba": {
                                "id": "hc_ba",
                                "customerId": "hc_cust",
                                "bankAccountToken": "ba_tok_1",
                                "bankName": "TD",
                                "transitNumber": "12345",
                            }
                        },
                        "sample_id": "hc_ba",
                    },
                    "Purchase": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "amount": "number",
                            "currency": "string",
                            "status": "string",
                            "type": "string",
                        },
                        "path": "/payment/purchase",
                        "seed": {
                            "hc_pur": {
                                "id": "hc_pur",
                                "transactionId": 100001,
                                "amount": 50.0,
                                "currency": "CAD",
                                "status": "APPROVED",
                                "type": "purchase",
                            }
                        },
                        "sample_id": "hc_pur",
                    },
                    "Preauth": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "amount": "number",
                            "currency": "string",
                            "status": "string",
                            "type": "string",
                        },
                        "path": "/payment/preauth",
                        "seed": {
                            "hc_pre": {
                                "id": "hc_pre",
                                "transactionId": 100002,
                                "amount": 75.0,
                                "currency": "CAD",
                                "status": "APPROVED",
                                "type": "preauth",
                            }
                        },
                        "sample_id": "hc_pre",
                    },
                    "Capture": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "amount": "number",
                            "currency": "string",
                            "status": "string",
                            "preAuthTransactionId": "integer",
                        },
                        "path": "/payment/capture",
                        "seed": {
                            "hc_cap": {
                                "id": "hc_cap",
                                "transactionId": 100003,
                                "amount": 75.0,
                                "currency": "CAD",
                                "status": "APPROVED",
                                "preAuthTransactionId": 100002,
                            }
                        },
                        "sample_id": "hc_cap",
                    },
                    "Verify": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "status": "string",
                            "cardType": "string",
                        },
                        "path": "/payment/verify",
                        "seed": {
                            "hc_ver": {
                                "id": "hc_ver",
                                "transactionId": 100004,
                                "status": "APPROVED",
                                "cardType": "Visa",
                            }
                        },
                        "sample_id": "hc_ver",
                    },
                    "CardRefund": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "amount": "number",
                            "currency": "string",
                            "status": "string",
                            "originalTransactionId": "integer",
                        },
                        "path": "/payment/refund",
                        "seed": {
                            "hc_ref": {
                                "id": "hc_ref",
                                "transactionId": 100005,
                                "amount": 25.0,
                                "currency": "CAD",
                                "status": "APPROVED",
                                "originalTransactionId": 100001,
                            }
                        },
                        "sample_id": "hc_ref",
                    },
                    "Reverse": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "amount": "number",
                            "status": "string",
                            "originalTransactionId": "integer",
                        },
                        "path": "/payment/reverse",
                        "seed": {
                            "hc_rev": {
                                "id": "hc_rev",
                                "transactionId": 100006,
                                "amount": 50.0,
                                "status": "APPROVED",
                                "originalTransactionId": 100001,
                            }
                        },
                        "sample_id": "hc_rev",
                    },
                    "Invoice": {
                        "schema": {
                            "id": "string",
                            "invoiceNumber": "string",
                            "customerId": "string",
                            "status": "string",
                            "amount": "number",
                            "currency": "string",
                        },
                        "path": "/invoices",
                        "seed": {
                            "hc_inv": {
                                "id": "hc_inv",
                                "invoiceNumber": "INV-001",
                                "customerId": "hc_cust",
                                "status": "PAID",
                                "amount": 100.0,
                                "currency": "CAD",
                            }
                        },
                        "sample_id": "hc_inv",
                    },
                    "CardBatch": {
                        "schema": {
                            "id": "string",
                            "batchNumber": "integer",
                            "status": "string",
                            "totalAmount": "number",
                            "transactionCount": "integer",
                        },
                        "path": "/card-batches",
                        "seed": {
                            "hc_cb": {
                                "id": "hc_cb",
                                "batchNumber": 42,
                                "status": "CLOSED",
                                "totalAmount": 5000.0,
                                "transactionCount": 25,
                            }
                        },
                        "sample_id": "hc_cb",
                    },
                    "CardTransaction": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "type": "string",
                            "amount": "number",
                            "status": "string",
                            "dateCreated": "string",
                        },
                        "path": "/card-transactions",
                        "seed": {
                            "hc_ct": {
                                "id": "hc_ct",
                                "transactionId": 100001,
                                "type": "purchase",
                                "amount": 50.0,
                                "status": "APPROVED",
                                "dateCreated": "2025-01-15",
                            }
                        },
                        "sample_id": "hc_ct",
                    },
                    "AchTransaction": {
                        "schema": {
                            "id": "string",
                            "transactionId": "integer",
                            "type": "string",
                            "amount": "number",
                            "status": "string",
                            "bankName": "string",
                        },
                        "path": "/ach-transactions",
                        "seed": {
                            "hc_ach": {
                                "id": "hc_ach",
                                "transactionId": 200001,
                                "type": "withdraw",
                                "amount": 500.0,
                                "status": "APPROVED",
                                "bankName": "TD",
                            }
                        },
                        "sample_id": "hc_ach",
                    },
                    "AchBatch": {
                        "schema": {
                            "id": "string",
                            "batchNumber": "integer",
                            "status": "string",
                            "totalAmount": "number",
                        },
                        "path": "/ach-batches",
                        "seed": {
                            "hc_ab": {
                                "id": "hc_ab",
                                "batchNumber": 10,
                                "status": "PENDING",
                                "totalAmount": 2500.0,
                            }
                        },
                        "sample_id": "hc_ab",
                    },
                    "PaymentPlan": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "amount": "number",
                            "frequency": "string",
                            "status": "string",
                        },
                        "path": "/payment-plans",
                        "seed": {
                            "hc_pp": {
                                "id": "hc_pp",
                                "name": "Monthly Premium",
                                "amount": 29.99,
                                "frequency": "MONTHLY",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "hc_pp",
                    },
                    "Subscription": {
                        "schema": {
                            "id": "string",
                            "customerId": "string",
                            "paymentPlanId": "string",
                            "status": "string",
                            "nextPaymentDate": "string",
                        },
                        "path": "/subscriptions",
                        "seed": {
                            "hc_sub": {
                                "id": "hc_sub",
                                "customerId": "hc_cust",
                                "paymentPlanId": "hc_pp",
                                "status": "ACTIVE",
                                "nextPaymentDate": "2025-02-15",
                            }
                        },
                        "sample_id": "hc_sub",
                    },
                    "AddOn": {
                        "schema": {
                            "id": "string",
                            "name": "string",
                            "amount": "number",
                            "frequency": "string",
                        },
                        "path": "/add-ons",
                        "seed": {
                            "hc_ao": {
                                "id": "hc_ao",
                                "name": "Extra Storage",
                                "amount": 9.99,
                                "frequency": "MONTHLY",
                            }
                        },
                        "sample_id": "hc_ao",
                    },
                    "CheckoutSession": {
                        "schema": {
                            "id": "string",
                            "secretToken": "string",
                            "amount": "number",
                            "currency": "string",
                        },
                        "path": "/helcim-pay/initialize",
                        "seed": {
                            "hc_cs": {
                                "id": "hc_cs",
                                "secretToken": "sec_tok_1",
                                "amount": 50.0,
                                "currency": "CAD",
                            }
                        },
                        "sample_id": "hc_cs",
                    },
                    "Device": {
                        "schema": {
                            "id": "string",
                            "terminalId": "string",
                            "name": "string",
                            "status": "string",
                        },
                        "path": "/devices",
                        "seed": {
                            "hc_dev": {
                                "id": "hc_dev",
                                "terminalId": "T-001",
                                "name": "Counter Terminal",
                                "status": "ACTIVE",
                            }
                        },
                        "sample_id": "hc_dev",
                    },
                },
            )
        )

        return fixtures

    @staticmethod
    def _make_spec_and_seed(type_name, cfg):
        """Build an OpenAPI spec dict and seed data for one federated type."""
        properties = {}
        for field_name, field_type in cfg["schema"].items():
            properties[field_name] = {"type": field_type}
        spec = {
            "components": {
                "schemas": {
                    type_name: {
                        "type": "object",
                        "required": ["id"],
                        "properties": properties,
                    }
                }
            },
            "paths": {
                f"{cfg['path']}/{{id}}": {
                    "get": {
                        "operationId": f"get_{type_name.lower()}",
                        "parameters": [{"name": "id", "in": "path"}],
                    }
                },
                cfg["path"]: {"get": {"operationId": f"list_{type_name.lower()}"}},
            },
        }
        return spec, cfg["seed"]

    @staticmethod
    def _build_in_process_transport(spec, seed, base_path, base_url):
        """Build a deterministic in-process REST upstream for matrix tests."""

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from zephyrex.extensions.federation.BLL_Federation_REST import (
            RESTUpstreamTransport,
            openapi_to_pydantic_models,
        )

        app = FastAPI()

        @app.get(f"{base_path}/{{item_id}}")
        async def _get(item_id: str):
            return seed.get(item_id)

        @app.get(base_path)
        async def _list():
            return list(seed.values())

        sync_client = TestClient(app, base_url=base_url)

        class _SyncHTTP:
            def get(self, url, **kw):
                return sync_client.get(url, params=kw.get("params") or None).json()

            def post(self, url, **kw):
                return sync_client.post(url, json=kw.get("json")).json()

            def put(self, url, **kw):
                return sync_client.put(url, json=kw.get("json")).json()

            def patch(self, url, **kw):
                return sync_client.patch(url, json=kw.get("json")).json()

            def delete(self, url, **kw):
                return sync_client.delete(url).json()

        operations = openapi_to_pydantic_models(spec).operations
        return RESTUpstreamTransport(
            _SyncHTTP(), base_url=base_url, operations=operations
        )
