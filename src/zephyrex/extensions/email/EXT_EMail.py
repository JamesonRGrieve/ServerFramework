# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Email extension for AGInfrastructure.

Provides email abilities including SendGrid, Gmail, Microsoft Outlook, Mailgun,
Yahoo, POP3, and IMAP support. This extension provides static functionality and
metadata to organize email-related components and manage email provider instances.

Inbound mail enters through two sources, both feeding the hook point in
InboundEmail: a signed endpoint mail servers POST raw messages to
(InboundEndpoint), and a poller reading configured IMAP mailboxes
(SVC_InboundIMAP).

The extension focuses on:
- Email sending and receiving abilities
- Email template management
- Email tracking and delivery status
- Provider instance management for email services
- Integration hooks for authentication workflows

Component loading (DB, BLL, EP) is handled automatically by the import system
based on file naming conventions.
"""

import os
from abc import abstractmethod
from datetime import datetime
from email.utils import parseaddr
from enum import Enum
from urllib.parse import urlencode
from typing import (
    Any,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Type,
)

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticProvider,
    InstanceSetting,
    ability,
)
from zephyrex.extensions.AbstractExternalModel import idempotent
from zephyrex.extensions.email.InboundEndpoint import (
    MIN_SIGNING_SECRET_LENGTH,
    SIGNING_SECRET_SETTING,
)
from zephyrex.extensions.email.EmailErrors import (
    EmailValidationError,
    extract_status_code,
    map_validation_error,
)
from zephyrex.extensions.ExternalErrors import (
    TransientExternalError,
    map_upstream_status,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.pydantic2.registry import classproperty
from zephyrex.logic.BLL_Providers import OPERATOR_SCOPES, ProviderInstanceModel

# Hard caps applied uniformly across all email providers. Sized for RFC 5322
# (subject ≤998 octets) and a generous 10 MiB body — anything larger is almost
# certainly a DoS or smuggling attempt rather than a legitimate message.
_EMAIL_MAX_SUBJECT_OCTETS = 998
_EMAIL_MAX_BODY_BYTES = 10 * 1024 * 1024

# The setting every provider sends from.
FROM_EMAIL_SETTING = "from_email"


def from_email_setting(env_var: str) -> InstanceSetting:
    """The sender address a provider's instance sends from, defaulting for
    the operator's instances to ``env_var``."""
    return InstanceSetting(
        FROM_EMAIL_SETTING, "The address mail is sent from", env=env_var
    )


def speaks_for_operator(instance: ProviderInstanceModel) -> bool:
    """Whether ``instance`` is the operator's own: in the root or system
    scope, or owned by ROOT or SYSTEM and no team. The second covers the
    ``Root_<Provider>`` instances the framework seeds from the environment,
    which carry the default scope but belong to no user. A user's or team's
    instance is never the operator's, whoever created it."""
    if instance.scope in OPERATOR_SCOPES:
        return True
    owner = instance.user_id or instance.created_by_user_id
    return instance.team_id is None and server_side(owner)


# ============================================================================
# Email value types
#
# These are the data shapes the friendly Phase-1 surface (``send``,
# ``update_email``, ``list_emails``) speaks. They are pure Pydantic models
# and do not depend on any of the deferred IMPROVEMENTS items; once Item 37
# (typed ability declarations) lands, they slot in unchanged as the typed
# inputs to ``AbstractEmailProviderInstance`` abstract abilities.
# ============================================================================


class Importance(str, Enum):
    """RFC 4021 ``Importance`` values, normalised across providers."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


# ----------------------------------------------------------------------
# Item 94 — canonical email-delivery event + hook fan-out.
# ----------------------------------------------------------------------


class EmailDeliveryEvent(BaseModel):
    """Provider-normalised inbound delivery event.

    Webhook handlers in concrete providers translate the upstream's wire
    payload (SendGrid Event Webhook, SMTP2go bounce-activity, Stalwart
    custom hooks) into this canonical shape and fan it through
    `dispatch_email_delivery_event`. Downstream consumers (Item 67's
    suppression-list maintainer, bounce metrics, inbound-parse routing)
    bind to the canonical model so they don't have to re-discriminate
    per provider.
    """

    message_id: str = Field("", description="Provider's `X-Message-Id` or equivalent.")
    provider: str = Field(..., description="Provider short name (e.g. `sendgrid`).")
    event_type: str = Field(
        ...,
        description=(
            "One of `bounce`, `delivered`, `open`, `click`, `spam_report`, "
            "`unsubscribe`, `dropped`, `processed`."
        ),
    )
    recipient: str = Field("", description="The email address the event is about.")
    timestamp: Optional[float] = Field(
        None, description="Unix epoch seconds at which the event was emitted upstream."
    )
    raw: Dict[str, Any] = Field(
        default_factory=dict,
        description="Raw upstream payload, preserved for diagnostic / debugging use.",
    )


# Process-local subscriber list. Webhook handlers call
# `dispatch_email_delivery_event(event)` which fans into every registered
# subscriber. Item 94 spec calls for "the same hook bus that internal
# `Email_*Manager` mutations fire"; the framework has no central typed
# hook bus today, so this list-based fan-out is the minimum that keeps
# the extension self-contained without forcing a cross-cutting refactor.
_EMAIL_DELIVERY_SUBSCRIBERS: List[Any] = []


def subscribe_email_delivery(callback: Any) -> None:
    """Register a callback for `EmailDeliveryEvent` fan-out.

    Callbacks may be sync or async; async callbacks are awaited by
    `dispatch_email_delivery_event`. Idempotent: re-registering the same
    callback object no-ops.
    """
    if callback in _EMAIL_DELIVERY_SUBSCRIBERS:
        return
    _EMAIL_DELIVERY_SUBSCRIBERS.append(callback)


def unsubscribe_email_delivery(callback: Any) -> None:
    """Remove a previously-registered subscriber. No-op when not present."""
    if callback in _EMAIL_DELIVERY_SUBSCRIBERS:
        _EMAIL_DELIVERY_SUBSCRIBERS.remove(callback)


def list_email_delivery_subscribers() -> List[Any]:
    """Return the current subscriber list (copy)."""
    return list(_EMAIL_DELIVERY_SUBSCRIBERS)


async def dispatch_email_delivery_event(event: "EmailDeliveryEvent") -> None:
    """Fan an `EmailDeliveryEvent` to every registered subscriber.

    Subscribers may be sync or async. Failures in one subscriber are
    logged and do not block subsequent subscribers — webhook delivery
    must remain idempotent for upstream retries.
    """
    import inspect

    for cb in list(_EMAIL_DELIVERY_SUBSCRIBERS):
        result = cb(event)
        if inspect.isawaitable(result):
            await result


class Capability(str, Enum):
    """Coarse-grained ability flags. A provider declares the subset it
    actually implements; callers branch on capability rather than catching
    ``NotImplementedError``."""

    SEND = "send"
    BULK_SEND = "bulk_send"
    LIST = "list"
    SEARCH = "search"
    READ = "read"
    REPLY = "reply"
    UPDATE = "update"
    ATTACHMENTS = "attachments"
    THREADS = "threads"
    TEMPLATES = "templates"
    VALIDATE_ADDRESS = "validate_address"
    SUPPRESSIONS = "suppressions"
    INBOUND_WEBHOOK = "inbound_webhook"
    # Item 95 — additional ladder capabilities. STATS exposes
    # `get_stats`; MESSAGES exposes `list_messages`. TEMPLATES already
    # gates `send_with_template`; SUPPRESSIONS gates the
    # `*_suppression*` trio.
    STATS = "stats"
    MESSAGES = "messages"


class EmailAddress(BaseModel):
    """A single RFC 5322 mailbox: address + optional display name."""

    address: str = Field(..., description="The address part (local@domain).")
    name: Optional[str] = Field(None, description="Display name, if any.")

    def format(self) -> str:
        """Render as a string suitable for ``To``/``From``/``Cc`` headers."""
        if self.name:
            # We do not quote the display name here; ``_validate_message`` has
            # already rejected CRLF and NUL, so a bare name is safe in headers.
            return f"{self.name} <{self.address}>"
        return self.address


class Attachment(BaseModel):
    """An email attachment carried inline as bytes."""

    filename: str = Field(..., description="Suggested filename for recipient.")
    content: bytes = Field(..., description="Raw attachment bytes.")
    content_type: Optional[str] = Field(
        None, description="MIME type; guessed from filename if omitted."
    )

    @classmethod
    def from_path(cls, path: str) -> "Attachment":
        """Load an attachment from a local filesystem path."""
        import mimetypes

        with open(path, "rb") as fh:
            content = fh.read()
        guessed = mimetypes.guess_type(path)[0]
        return cls(
            filename=os.path.basename(path),
            content=content,
            content_type=guessed,
        )


class EmailMessage(BaseModel):
    """The full payload of a single outbound email.

    Carries every field the friendly ``send(message)`` API accepts. Concrete
    providers translate the relevant subset for their transport; fields a
    given transport cannot honour (e.g. ``cc`` on a single-recipient SMTP
    relay) are surfaced as a ``NotSupportedError`` rather than silently
    dropped.
    """

    to: List[EmailAddress] = Field(..., min_length=1)
    subject: str = Field(...)
    body_text: Optional[str] = Field(None)
    body_html: Optional[str] = Field(None)
    cc: List[EmailAddress] = Field(default_factory=list)
    bcc: List[EmailAddress] = Field(default_factory=list)
    reply_to: Optional[EmailAddress] = Field(None)
    from_: Optional[EmailAddress] = Field(
        None,
        alias="from",
        description="Sender; falls back to the provider's default from-address.",
    )
    attachments: List[Attachment] = Field(default_factory=list)
    headers: Dict[str, str] = Field(default_factory=dict)
    importance: Importance = Field(Importance.NORMAL)
    template_id: Optional[str] = Field(None)
    template_vars: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# Item 97 — persistent per-class transport for the send path. Cached BY PROVIDER
# CLASS so the TokenBucket survives across send() calls: a bucket (or wrapper)
# rebuilt per call would reset its burst allowance every time and throttle
# nothing. Keyed by the concrete provider class so each provider gets its own
# rate limit.
_SEND_HTTP_CLIENTS: Dict[type, Any] = {}
_SEND_RATE_BUCKETS: Dict[type, Any] = {}


class AbstractEmailProvider(AbstractStaticProvider):
    """Abstract base class for email service providers."""

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None
    extension_type: ClassVar[str] = "email"

    # Capability flags. Concrete providers override with the subset they
    # actually implement. Callers branch on ``Capability.X in cls.capabilities``
    # rather than catching ``NotImplementedError`` at the call site.
    capabilities: ClassVar[FrozenSet["Capability"]] = frozenset()

    # Any email provider instance in the root or system scope can be an
    # inbound endpoint a mail server POSTs messages to (see InboundEndpoint).
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            SIGNING_SECRET_SETTING,
            "Signs mail a mail server POSTs to /v1/email/inbound/{this "
            "instance's id}: HMAC-SHA256, at least "
            f"{MIN_SIGNING_SECRET_LENGTH} characters. Honoured on root- and "
            "system-scoped instances only",
            secret=True,
        ),
    )
    # Whether the inbound poller reads this provider's instances' mailboxes
    # over IMAP (see SVC_InboundIMAP).
    polls_imap: ClassVar[bool] = False

    @classmethod
    def _send_rate_bucket(cls) -> Any:
        """Persistent per-class ``TokenBucket`` built once from the declared
        ``rate_limit`` (``None`` when the provider declares none).

        Caching the bucket across ``send`` calls is what lets it actually
        throttle a 429 burst — a bucket rebuilt per call would reset its
        allowance every time. Used directly by SMTP send paths (Stalwart) and
        as the wrapper's limiter for HTTP send paths.
        """
        if cls not in _SEND_RATE_BUCKETS:
            from zephyrex.extensions.RateLimit import RateLimit, TokenBucket

            rl = getattr(cls, "rate_limit", None)
            _SEND_RATE_BUCKETS[cls] = (
                TokenBucket(rl) if isinstance(rl, RateLimit) else None
            )
        return _SEND_RATE_BUCKETS[cls]

    @classmethod
    def _send_http_client(cls) -> Any:
        """Persistent per-class ``ProviderHTTPClient`` for HTTP send paths.

        Gives ``send`` the shared client's SSRF guard, TLS/timeout policy and
        connection pooling, plus trace propagation, log redaction, Retry-After
        retry, and — via the persistent :meth:`_send_rate_bucket` — real
        rate-limit throttling. Per-request auth is passed as an explicit header
        at the call site, so the cached client holds no credential.
        """
        if cls not in _SEND_HTTP_CLIENTS:
            from zephyrex.lib.ProviderHTTPClient import (
                ClientPolicy,
                ProviderHTTPClient,
            )

            _SEND_HTTP_CLIENTS[cls] = ProviderHTTPClient(
                policy=ClientPolicy(timeout=30.0),
                rate_limit=cls._send_rate_bucket(),
                provider_name=getattr(cls, "name", cls.__name__),
                provider=cls,
            )
        return _SEND_HTTP_CLIENTS[cls]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # Each instance's settings are the provider's configuration; a
        # setting's environment variable is the default for the operator's
        # instances only (``resolve_setting``). The framework seeds the
        # operator's ``Root_<Provider>`` instance, and its place in the root
        # rotation, from the variables ``_env`` names, so ``_env`` is derived
        # from the declared settings rather than declared beside them.
        cls._env = {
            declared.env: declared.default or ""
            for declared in cls.instance_settings
            if declared.env
        }
        super().__init_subclass__(**kwargs)

    @classmethod
    def resolve_setting(
        cls,
        instance: Optional[ProviderInstanceModel],
        key: str,
        env_var: Optional[str] = None,
        *,
        field: Optional[str] = None,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """The instance's own value, else (for the operator's instances, and
        environment-only lookups) the environment's, else the default. A
        user's or team's instance never sends with the operator's
        credentials or from the operator's address."""
        if instance is not None and not speaks_for_operator(instance):
            env_var = None
        return super().resolve_setting(
            instance, key, env_var, field=field, default=default
        )

    @classmethod
    def destination(
        cls, instance: Optional[ProviderInstanceModel], address: str
    ) -> str:
        """``address`` (a URL, or a mail server's ``host[:port]``) once
        ``instance`` may reach it. The operator's instances reach anywhere (a
        self-hosted mail server is often on the private network); a user's or
        team's may not reach the server's own network, the SSRF guard's
        refusal (``SSRFGuardError``, a ValueError) says so. An empty address
        is returned as is: it reaches nothing."""
        if address and instance is not None and not speaks_for_operator(instance):
            from zephyrex.lib.ProviderHTTPClient import validate_outbound_url

            # The guard reads a URL; a bare mail-server address is given a
            # scheme only so its host and port parse.
            validate_outbound_url(address if "://" in address else f"http://{address}")
        return address

    @classmethod
    def mail_server(
        cls, instance: Optional[ProviderInstanceModel], host: str, port: int
    ) -> str:
        """The mail server ``host:port`` once ``instance`` may reach it
        (:meth:`destination`)."""
        bracketed = f"[{host}]" if ":" in host else host
        return cls.destination(instance, f"{bracketed}:{port}")

    @classmethod
    def setting_flag(cls, instance: Optional[ProviderInstanceModel], key: str) -> bool:
        """The declared on/off setting ``key``: on unless it reads ``false``."""
        return (cls.setting(instance, key) or "").strip().lower() != "false"

    @classmethod
    def setting_port(cls, instance: Optional[ProviderInstanceModel], key: str) -> int:
        """The declared port setting ``key``; ValueError when it is not a
        number."""
        return int(cls.setting(instance, key) or "")

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""

    # Item 94 — webhook signature verification.
    #
    # Providers that emit inbound webhooks (SendGrid Event Webhook, SMTP2go
    # bounce-activity, Stalwart custom hooks) MUST implement this; the
    # `extensions.webhooks` mount calls it before dispatching to any handler
    # and rejects with 401 on failure. Providers that don't expose webhooks
    # leave the default in place; the webhook router treats the absence
    # of a matching `(extension, provider)` registration as "no handlers
    # configured" and returns 200 + warn instead of invoking this.
    @classmethod
    def verify_signature(cls, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify an incoming webhook signature.

        Webhook-emitting providers must override; the default raises
        `NotImplementedError` so a misconfigured provider fails closed.
        """
        raise NotImplementedError(
            f"{cls.__name__}.verify_signature must be implemented by "
            "webhook-emitting providers."
        )

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get information about the email extension."""

        friendly_name = (
            getattr(cls.extension, "friendly_name", "Email")
            if cls.extension
            else "Email"
        )
        return {
            "name": friendly_name,
            "description": f"Email extension for {cls.get_platform_name()}",
        }

    @classmethod
    def _validate_send_inputs(
        cls,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] | None = None,
    ) -> Optional[str]:
        """
        Reject inputs that could be used to mount header-injection, SSRF,
        traversal, or DoS attacks against the underlying transport. Returns
        ``None`` when inputs are safe, otherwise an error string suitable
        for returning directly from ``send_email``. Concrete providers must
        call this as the first statement in their ``send_email`` body.
        """
        if not isinstance(recipient, str) or not recipient:
            return "Failed to send email: invalid recipient (empty)"
        if not isinstance(subject, str):
            return "Failed to send email: invalid subject (must be string)"
        if not isinstance(body, str):
            return "Failed to send email: invalid body (must be string)"

        # CRLF injection: anything that lets a caller break out of the
        # recipient or subject header line and graft a Bcc:/Reply-To: header.
        for label, value in (("recipient", recipient), ("subject", subject)):
            if "\r" in value or "\n" in value:
                return f"Failed to send email: rejected CRLF in {label}"

        # NUL byte: silently truncates strings in C-backed transports
        # (libmagic, smtplib parsers) and can be used to smuggle content.
        if "\x00" in recipient or "\x00" in subject or "\x00" in body:
            return "Failed to send email: rejected NUL byte in input"

        # RFC 5322 §2.1.1 caps a single header line at 998 octets. Anything
        # longer is either a bug or a payload trying to wrap a header.
        if len(subject.encode("utf-8")) > _EMAIL_MAX_SUBJECT_OCTETS:
            return "Failed to send email: subject exceeds 998 octets"

        if len(body.encode("utf-8", errors="replace")) > _EMAIL_MAX_BODY_BYTES:
            return "Failed to send email: body exceeds 10 MiB cap"

        # parseaddr yields ('', '') for malformed input; a valid address
        # must contain a single '@' and a non-empty local + domain part.
        _, addr = parseaddr(recipient)
        if not addr or "@" not in addr or addr.count("@") != 1:
            return "Failed to send email: invalid recipient address"
        local, _, domain = addr.partition("@")
        if not local or not domain or "." not in domain:
            return "Failed to send email: invalid recipient address"

        # Cyrillic / Greek homograph guard: refuse non-ASCII in the local part
        # and in the domain. Legitimate IDN domains should be Punycode-encoded
        # by the caller before reaching this layer.
        try:
            addr.encode("ascii")
        except UnicodeEncodeError:
            return "Failed to send email: non-ASCII recipient (suspected homograph)"

        if attachments:
            if not isinstance(attachments, (list, tuple)):
                return "Failed to send email: attachments must be a list"
            for path in attachments:
                if not isinstance(path, str) or not path:
                    return "Failed to send email: invalid attachment path"
                if "\x00" in path:
                    return "Failed to send email: rejected NUL byte in attachment path"
                if not os.path.isabs(path):
                    return "Failed to send email: attachment path must be absolute"
                # Path traversal: even on an absolute path a '..' segment can
                # walk to a parent directory the operator did not intend.
                normalized = os.path.normpath(path)
                if ".." in normalized.split(os.sep):
                    return "Failed to send email: rejected path traversal in attachment"

        return None

    @classmethod
    def _validate_message(cls, message: "EmailMessage") -> Optional[str]:
        """Apply the same denial rules as ``_validate_send_inputs`` against
        the typed ``EmailMessage`` shape, including ``cc`` / ``bcc`` /
        ``reply_to`` / ``from_`` and the ``headers`` dict.

        Returns ``None`` on pass, an error string on reject. The bytes-based
        attachment shape (``Attachment(filename, content)``) is validated for
        filename safety only; the framework owns the content and never lets
        the caller name a path that does not exist."""
        # Subject: CRLF / NUL / length.
        if not isinstance(message.subject, str):
            return "Failed to send email: invalid subject (must be string)"
        if "\r" in message.subject or "\n" in message.subject:
            return "Failed to send email: rejected CRLF in subject"
        if "\x00" in message.subject:
            return "Failed to send email: rejected NUL byte in input"
        if len(message.subject.encode("utf-8")) > _EMAIL_MAX_SUBJECT_OCTETS:
            return "Failed to send email: subject exceeds 998 octets"

        # Bodies: NUL + size cap.
        for label, body in (
            ("body_text", message.body_text),
            ("body_html", message.body_html),
        ):
            if body is None:
                continue
            if not isinstance(body, str):
                return f"Failed to send email: invalid {label} (must be string)"
            if "\x00" in body:
                return "Failed to send email: rejected NUL byte in input"
            if len(body.encode("utf-8", errors="replace")) > _EMAIL_MAX_BODY_BYTES:
                return "Failed to send email: body exceeds 10 MiB cap"

        # Address fields: every ``EmailAddress`` everywhere on the message.
        addr_groups: List[tuple] = [
            ("to", message.to),
            ("cc", message.cc),
            ("bcc", message.bcc),
        ]
        for label, group in addr_groups:
            for entry in group:
                err = cls._validate_email_address(entry, label)
                if err:
                    return err
        for label, single in (("reply_to", message.reply_to), ("from", message.from_)):
            if single is None:
                continue
            err = cls._validate_email_address(single, label)
            if err:
                return err

        # Custom headers: CRLF and NUL guard. Header names must be tokens.
        for h_name, h_value in (message.headers or {}).items():
            if not isinstance(h_name, str) or not isinstance(h_value, str):
                return "Failed to send email: invalid custom header"
            if "\r" in h_name or "\n" in h_name or "\r" in h_value or "\n" in h_value:
                return "Failed to send email: rejected CRLF in custom header"
            if "\x00" in h_name or "\x00" in h_value:
                return "Failed to send email: rejected NUL byte in custom header"

        # Attachments: bytes shape — validate filename safety only.
        for att in message.attachments or []:
            if not isinstance(att.filename, str) or not att.filename:
                return "Failed to send email: invalid attachment filename"
            if "\x00" in att.filename:
                return "Failed to send email: rejected NUL byte in attachment filename"
            if "\r" in att.filename or "\n" in att.filename:
                return "Failed to send email: rejected CRLF in attachment filename"
            if att.content is None:
                return "Failed to send email: attachment content missing"
            if len(att.content) > _EMAIL_MAX_BODY_BYTES:
                return "Failed to send email: attachment exceeds 10 MiB cap"

        return None

    @staticmethod
    def _validate_email_address(entry: "EmailAddress", label: str) -> Optional[str]:
        """Validate a single ``EmailAddress`` for header-injection-safe use."""
        addr = entry.address if entry else ""
        name = entry.name if entry else None

        if not isinstance(addr, str) or not addr:
            return f"Failed to send email: invalid {label} (empty address)"
        if "\r" in addr or "\n" in addr:
            return f"Failed to send email: rejected CRLF in {label}"
        if "\x00" in addr:
            return "Failed to send email: rejected NUL byte in input"
        if name is not None:
            if not isinstance(name, str):
                return f"Failed to send email: invalid {label} display name"
            if "\r" in name or "\n" in name:
                return f"Failed to send email: rejected CRLF in {label} display name"
            if "\x00" in name:
                return "Failed to send email: rejected NUL byte in input"

        # Validate the address shape itself.
        _, parsed = parseaddr(addr)
        if not parsed or "@" not in parsed or parsed.count("@") != 1:
            return f"Failed to send email: invalid {label} address"
        local, _, domain = parsed.partition("@")
        if not local or not domain or "." not in domain:
            return f"Failed to send email: invalid {label} address"
        try:
            parsed.encode("ascii")
        except UnicodeEncodeError:
            return f"Failed to send email: non-ASCII {label} (suspected homograph)"
        return None

    @staticmethod
    @abstractmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get emails from a specified folder."""

    @classmethod
    @abstractmethod
    @ability(name="email_send")
    async def send_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] | None = None,
        importance: str = "normal",
    ) -> str:
        """Send an email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_draft")
    async def create_draft_email(
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] | None = None,
        importance: str = "normal",
    ) -> str:
        """Create a draft email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        date_range: Optional[tuple] | None = None,
    ) -> List[Dict[str, Any]]:
        """Search for emails in a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_reply")
    async def reply_to_email(
        provider_instance: ProviderInstanceModel,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] | None = None,
    ) -> str:
        """Reply to a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_delete")
    async def delete_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Delete a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_move")
    async def move_email(
        provider_instance: ProviderInstanceModel, message_id: str, folder_name: str
    ) -> str:
        """Move a specific email to a different folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_mark_read")
    async def mark_email_as_read(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Mark a specific email as read."""

    @staticmethod
    @abstractmethod
    @ability(name="email_mark_unread")
    async def mark_email_as_unread(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Mark a specific email as unread."""

    @staticmethod
    @abstractmethod
    @ability(name="email_flag")
    async def flag_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Flag a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_unflag")
    async def unflag_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Remove flag from a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_threads")
    async def get_email_threads(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_threads: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get email threads from a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_thread_messages")
    async def get_thread_messages(
        provider_instance: ProviderInstanceModel, thread_id: str
    ) -> List[Dict[str, Any]]:
        """Get all messages from a specific email thread."""

    @staticmethod
    @abstractmethod
    @ability(name="email_latest")
    async def get_latest_email(
        provider_instance: ProviderInstanceModel, folder_name: str = "Inbox"
    ) -> Dict[str, Any]:
        """Get the latest email from a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_attachment")
    async def download_attachment(
        provider_instance: ProviderInstanceModel, message_id: str, attachment_id: str
    ) -> bytes:
        """Download an attachment from a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_attachments")
    async def process_attachments(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> List[str]:
        """Download attachments from a specific email."""

    @classmethod
    @abstractmethod
    def get_platform_name(cls) -> str:
        """Get the name of the email platform this provider interacts with."""

    # ------------------------------------------------------------------
    # Phase-1 friendly surface
    #
    # ``send``, ``update_email``, and ``list_emails`` collapse the legacy
    # 17-method receive/send/state-mutation surface into 3 typed methods
    # that take ``EmailMessage`` / kwargs instead of positional triples.
    # Until Item 26 (``AbstractProviderInstance`` contract) lands, these
    # remain classmethods that take ``provider_instance`` as the first
    # arg; they delegate to the existing legacy abstracts so concrete
    # providers do not need to change to satisfy them. The legacy
    # ``send_email`` / ``mark_email_as_read`` / etc. methods stay as
    # the abstract surface; this layer is purely additive.
    # ------------------------------------------------------------------

    @classmethod
    async def send(
        cls,
        provider_instance: ProviderInstanceModel,
        message: "EmailMessage",
    ) -> str:
        """Send a typed ``EmailMessage`` via this provider.

        Validates the message (CRLF / NUL / length / address shape /
        attachment safety) before any transport call. Concrete providers
        that want to use the rich ``EmailMessage`` shape (cc / bcc / from-
        name / reply-to / headers) should override this method directly;
        the default implementation flattens ``message`` into the legacy
        ``send_email(recipient, subject, body, attachments)`` positional
        contract for backwards compatibility.
        """
        validation_error = cls._validate_message(message)
        if validation_error:
            logger.error(validation_error)
            return validation_error

        # Flatten to legacy abstract: pick the first ``to`` address as the
        # recipient. ``cc`` / ``bcc`` / ``reply_to`` / ``from_`` / display
        # names / headers / template_* are dropped on the legacy path —
        # providers that need them must override ``send`` directly.
        recipient = message.to[0].format()
        body = message.body_html or message.body_text or ""
        # Convert ``Attachment`` objects to filesystem paths via temp-file
        # if any are byte-only; legacy ``send_email`` only accepts paths.
        legacy_attachments: Optional[List[str]] | None = None
        if message.attachments:
            import tempfile

            legacy_attachments = []
            for att in message.attachments:
                safe_name = os.path.basename(att.filename).replace("..", "_")
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix="_" + safe_name)
                tmp.write(att.content)
                tmp.close()
                legacy_attachments.append(tmp.name)
        try:
            return await cls.send_email(  # type: ignore[no-any-return]
                provider_instance,
                recipient=recipient,
                subject=message.subject,
                body=body,
                attachments=legacy_attachments,
                importance=message.importance.value,
            )
        finally:
            for tmp_path in legacy_attachments or []:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Item 91 — typed rotation-system send entry points.
    #
    # ``send_via_provider`` / ``send_bulk_via_provider`` are provider-neutral:
    # they validate the typed ``EmailMessage``, delegate to ``cls.send`` (which
    # each provider implements), and map any failure onto the typed external-
    # error hierarchy — attributing every upstream error to ``cls.name``.
    # Hoisted onto the base so all providers share one implementation; a
    # provider overrides only when its send path genuinely diverges beyond
    # attribution. Decorated ``@idempotent`` (a marker that survives
    # inheritance) so the rotation manager mints + persists an idempotency key.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via this provider.

        Validates the message, delegates to ``cls.send``, then maps a failure
        onto the typed error hierarchy: a status code fished out of the legacy
        envelope routes through ``map_upstream_status`` (attributed to
        ``cls.name``). The message was already validated, so a failure without
        a status is the provider's (not configured, cannot bond, transport
        down) and is transient: the rotation moves on to the next provider.
        Returns a ``SentMessage``-shaped dict on success.
        """
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        # ``send`` returns the legacy string envelope. Failures look like
        # ``"Failed to send email: <reason>"``; map them to typed errors so the
        # rotation manager can decide whether to retry.
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            status = extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(status, legacy_result, provider=cls.name)
            raise TransientExternalError(legacy_result, provider=cls.name)

        recipient = message.to[0].format() if message.to else ""
        return {
            "message_id": "",
            "provider": cls.name,
            "accepted_at": datetime.utcnow().isoformat(),
            "recipient": recipient,
            "upstream_response": {"raw": legacy_result},
        }

    @classmethod
    @idempotent
    async def send_bulk_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        messages: List[EmailMessage],
    ) -> Dict[str, Any]:
        """Send up to ``SEND_BULK_MAX_BATCH`` messages, one per recipient.

        Validates every message up-front — collecting all violations, then
        raising the first — so a batch with any unsafe member is rejected
        before any upstream call is attempted. Per-message sends are then
        attempted in order via :meth:`send_via_provider`; each row records
        success or the typed error that ``send_via_provider`` raised.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        # Validate every message up-front so a batch with any unsafe member is
        # rejected before we touch the upstream. Collect all violations, then
        # surface the first as a typed error rather than forwarding any
        # half-valid batch.
        per_item_errors: List[Optional[Exception]] = [
            map_validation_error(err) if (err := cls._validate_message(m)) else None
            for m in messages
        ]
        for e in per_item_errors:
            if e is not None:
                raise e

        results: List[Dict[str, Any]] = []
        succeeded = 0
        failed = 0
        for m in messages:
            try:
                row = await cls.send_via_provider(provider_instance, m)
                results.append({"success": True, **row})
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 — typed by send_via_provider
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                failed += 1
        return {"results": results, "succeeded": succeeded, "failed": failed}

    @classmethod
    async def update_email(
        cls,
        provider_instance: ProviderInstanceModel,
        message_id: str,
        *,
        read: Optional[bool] | None = None,
        flagged: Optional[bool] | None = None,
        folder: Optional[str] | None = None,
        deleted: bool = False,
    ) -> str:
        """Apply state changes to an existing message in one call.

        Each kwarg, when not ``None``, dispatches to the corresponding
        legacy abstract: ``read=True`` → ``mark_email_as_read``,
        ``read=False`` → ``mark_email_as_unread``, ``flagged=True`` →
        ``flag_email``, ``flagged=False`` → ``unflag_email``,
        ``folder="X"`` → ``move_email``, ``deleted=True`` →
        ``delete_email``. Multiple state changes in one call apply
        sequentially; the first error short-circuits.
        """
        results: List[str] = []
        if read is True:
            results.append(await cls.mark_email_as_read(provider_instance, message_id))
        elif read is False:
            results.append(
                await cls.mark_email_as_unread(provider_instance, message_id)
            )
        if flagged is True:
            results.append(await cls.flag_email(provider_instance, message_id))
        elif flagged is False:
            results.append(await cls.unflag_email(provider_instance, message_id))
        if folder is not None:
            results.append(await cls.move_email(provider_instance, message_id, folder))
        if deleted:
            results.append(await cls.delete_email(provider_instance, message_id))
        if not results:
            return "No state change requested"
        return "; ".join(str(r) for r in results)

    @classmethod
    async def list_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        *,
        folder: Optional[str] | None = None,
        query: Optional[str] | None = None,
        limit: int = 10,
        cursor: Optional[str] | None = None,
    ) -> List[Dict[str, Any]]:
        """List emails with an optional search query and folder.

        Delegates to ``search_emails`` when ``query`` is provided, else
        ``get_emails``. ``cursor`` is currently passed-through opaquely;
        Item 7 (pagination homogenisation) will replace it with a typed
        ``next_token`` envelope.
        """
        effective_folder = folder or "Inbox"
        if query:
            return await cls.search_emails(  # type: ignore[no-any-return]
                provider_instance,
                query=query,
                folder_name=effective_folder,
                max_emails=limit,
            )
        return await cls.get_emails(  # type: ignore[no-any-return]
            provider_instance,
            folder_name=effective_folder,
            max_emails=limit,
            page_size=limit,
        )


class EXT_EMail(AbstractStaticExtension):
    """
    Email extension for AGInfrastructure.

    This extension provides:
    - Meta abilities for email service management
    - Abstract provider interface defining email operations
    - Integration with various email service providers
    """

    # Extension metadata
    name: ClassVar[str] = "email"
    version: ClassVar[str] = "1.1.0"
    description: ClassVar[str] = (
        "Email extension for interacting with various email providers"
    )

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="sendgrid",
                friendly_name="SendGrid Python Library",
                semver=">=6.10.0",
                reason="SendGrid email provider support",
            ),
            PIP_Dependency(
                name="aiosmtplib",
                friendly_name="aiosmtplib",
                semver=">=3.0.0",
                reason="Stalwart SMTP submission transport",
            ),
            PIP_Dependency(
                name="httpx",
                friendly_name="HTTPX",
                semver=">=0.27.0",
                reason="SMTP2go HTTP API transport",
            ),
        ]
    )

    # Filled by the @ability methods below.
    _abilities: ClassVar[Set[str]] = set()

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension provides."""
        abilities = cls._abilities.copy()

        # Add provider-specific abilities
        for provider_class in cls.providers:
            if hasattr(provider_class, "_abilities"):
                abilities.update(provider_class._abilities)

        return abilities

    @classmethod
    def get_provider_class(cls, provider_name: str):
        """Look up a provider class by its ``name`` attribute (case-insensitive)."""
        target = provider_name.lower()
        for provider in cls.providers:
            if getattr(provider, "name", "").lower() == target:
                return provider
        raise ValueError(f"Unknown provider: {provider_name}")

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """The poller that reads configured IMAP mailboxes into the inbound
        hook point, started by the framework's background services."""
        from zephyrex.extensions.email.SVC_InboundIMAP import InboundIMAPService

        return [
            InboundIMAPService(requester_id=requester_id, model_registry=model_registry)
        ]

    @classmethod
    @ability(name="email_status")
    def get_extension_status(cls) -> Dict[str, Any]:
        """The extension's version and the email providers it offers.
        Mail is sent through configured provider instances, so nothing here
        reads the server's environment."""
        return {
            "extension": cls.name,
            "version": cls.version,
            "providers": sorted(cls.get_provider_names()),
        }

    @classmethod
    def get_provider_names(cls) -> List[str]:
        """Get list of provider names."""
        return [
            provider.name for provider in cls.providers if hasattr(provider, "name")
        ]

    # Static methods for rotation system integration
    @classmethod
    async def send_email(
        cls, recipient: str, subject: str, body: str
    ) -> Dict[str, Any]:
        """Send a plain-text email through the root rotation.

        Goes through ``send_via_provider``, which raises typed errors, so a
        provider that cannot send fails over to the next one. Returns the
        ``SentMessage``-shaped dict of the provider that sent it.
        """
        root = cls.root
        if root is None:
            raise HTTPException(
                status_code=503, detail="No email provider is configured"
            )
        message = EmailMessage(
            to=[EmailAddress(address=recipient)], subject=subject, body_text=body
        )
        result: Dict[str, Any] = await root.arotate(
            cls.provider_call("send_via_provider"), message=message
        )
        return result

    @classmethod
    def queue_email(cls, recipient: str, subject: str, body: str) -> None:
        """Send in the background, so the caller never waits on (or fails
        with) the mail provider; failover happens inside the task, attempt by
        attempt, and a send that fails outright is logged."""
        from zephyrex.logic.AbstractLogicManager import _fire_and_forget

        async def _deliver() -> None:
            try:
                await cls.send_email(recipient, subject, body)
            except Exception as exc:
                logger.error(f"Email {subject!r} to {recipient} was not sent: {exc}")

        _fire_and_forget(_deliver())

    @classmethod
    async def get_emails(
        cls, folder_name: str = "Inbox", max_emails: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Get emails via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("get_emails"),
                folder_name=folder_name,
                max_emails=max_emails,
                **kwargs,
            )
        return []

    @classmethod
    async def create_draft_email(
        cls, recipient: str, subject: str, body: str, **kwargs
    ) -> str:
        """Create draft email via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("create_draft_email"),
                recipient=recipient,
                subject=subject,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def search_emails(
        cls, query: str, folder_name: str = "Inbox", max_emails: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Search emails via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("search_emails"),
                query=query,
                folder_name=folder_name,
                max_emails=max_emails,
                **kwargs,
            )
        return []

    @classmethod
    async def reply_to_email(cls, email_id: str, body: str, **kwargs) -> str:
        """Reply to email via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("reply_to_email"),
                message_id=email_id,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def delete_email(cls, email_id: str, **kwargs) -> str:
        """Delete email via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("delete_email"), message_id=email_id, **kwargs
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def process_attachments(cls, email_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Process email attachments via rotation system."""
        if cls.root:
            return await cls.root.arotate(  # type: ignore[no-any-return]
                cls.provider_call("process_attachments"), message_id=email_id, **kwargs
            )
        return []

    @classmethod
    @AbstractStaticExtension.hook("bll", "invitations", "invitation", "create", "after")
    def send_invitation_email(cls, entity, **kwargs):
        """
        Hook to send invitation email after invitation is created.
        This demonstrates how extensions can hook into core functionality.
        """
        try:
            if cls.root is None:
                return
            team_name = entity.invitation.team.name
            query = urlencode(
                {
                    "code": entity.invitation.code,
                    "email": entity.email,
                    "team": team_name,
                }
            )
            body = (
                f"You've been invited to join {team_name}.\n\n"
                f"Click here to accept: {env('FRONTEND_URL')}/accept-invitation?{query}\n\n"
                "This invitation expires in 7 days."
            )
            cls.queue_email(entity.email, f"You've been invited to {team_name}", body)
            logger.info(f"Invitation email queued for {entity.email}")
        except Exception as e:
            logger.error(f"Failed to queue invitation email: {e}")


AbstractEmailProvider.extension = EXT_EMail
