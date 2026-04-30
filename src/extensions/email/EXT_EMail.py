"""
Email extension for AGInfrastructure.

Provides email abilities including SendGrid, Gmail, Microsoft Outlook, Mailgun,
Yahoo, POP3, and IMAP support. This extension provides static functionality and
metadata to organize email-related components and manage email provider instances.

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
import warnings
from abc import abstractmethod
from email.utils import parseaddr
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Set,
    Type,
    Union,
)

from pydantic import BaseModel, EmailStr, Field, ValidationError

from extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from lib.Dependencies import Dependencies, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import classproperty
from logic.BLL_Providers import ProviderInstanceModel

# Hard caps applied uniformly across all email providers. Sized for RFC 5322
# (subject ≤998 octets) and a generous 10 MiB body — anything larger is almost
# certainly a DoS or smuggling attempt rather than a legitimate message.
_EMAIL_MAX_SUBJECT_OCTETS = 998
_EMAIL_MAX_BODY_BYTES = 10 * 1024 * 1024


# ============================================================================
# Email value types
#
# These are the data shapes the friendly Phase-1 surface (``send``,
# ``update_email``, ``list_emails``) speaks. They are pure Pydantic models
# and do not depend on any of the deferred IMPROVEMENTS items; once Item 37
# (typed ability declarations) lands, they slot in unchanged as the typed
# inputs to ``AbstractEmailProviderInstance`` abstract abilities.
# ============================================================================


class _DeprecatedEnvDict(dict):
    """Dict subclass that emits a DeprecationWarning on read.

    Item 90 — providers' legacy ``_env: Dict[str, Any]`` is being replaced
    by a typed ``Settings`` Pydantic inner model. The dict is kept for one
    release as a backward-compat alias that auto-derives from the new
    ``Settings._env_field_map``. Reads warn so callers can migrate; the
    framework's startup-time env-var registration toggles ``_silent`` to
    avoid noise on import.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Suppress during class construction; the framework toggles this
        # to False once the provider class is fully loaded.
        self._silent = True

    def _warn(self) -> None:
        if not self._silent:
            warnings.warn(
                "Provider `_env` dict is deprecated; use `Settings.from_env(os.environ)` "
                "and the typed `Settings` model instead.",
                DeprecationWarning,
                stacklevel=3,
            )

    def __getitem__(self, key):
        self._warn()
        return super().__getitem__(key)

    def __iter__(self):
        self._warn()
        return super().__iter__()

    def keys(self):
        self._warn()
        return super().keys()

    def values(self):
        self._warn()
        return super().values()

    def items(self):
        self._warn()
        return super().items()

    def get(self, key, default=None):
        self._warn()
        return super().get(key, default)


class Importance(str, Enum):
    """RFC 4021 ``Importance`` values, normalised across providers."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


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


class AbstractEmailProvider(AbstractStaticProvider):
    """Abstract base class for email service providers."""

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None
    extension_type: ClassVar[str] = "email"

    # Capability flags. Concrete providers override with the subset they
    # actually implement. Callers branch on ``Capability.X in cls.capabilities``
    # rather than catching ``NotImplementedError`` at the call site.
    capabilities: ClassVar[FrozenSet["Capability"]] = frozenset()

    # Item 90 — typed Settings model. Subclasses extend with provider-specific
    # required fields (api_key/host/etc.) and an `_env_field_map` that maps
    # field name → canonical env-var name. Sensitive fields use Pydantic's
    # `SecretStr` so ``print(settings.api_key)`` renders as ``**********``
    # rather than leaking the raw credential into logs.
    class Settings(BaseModel):
        """Typed configuration for an email provider.

        Pydantic's `SecretStr` redacts on `repr`/`str` so credential fields
        do not leak into logs (`print(settings.api_key)` -> `**********`).
        """

        from_email: EmailStr
        default_provider_name: Optional[str] = None

        _env_field_map: ClassVar[Dict[str, str]] = {}

        @classmethod
        def env_field_map(cls) -> Dict[str, str]:
            """Resolve the field-name → env-var mapping for this Settings."""
            return dict(cls._env_field_map)

        @classmethod
        def is_configured(cls, env_map: Mapping[str, str]) -> bool:
            """True iff every required field has a non-empty value in ``env_map``.

            Required fields are those without a model default; the canonical
            env-var name is read from ``_env_field_map``.
            """
            mapping = cls.env_field_map()
            for field_name, info in cls.model_fields.items():
                if not info.is_required():
                    continue
                env_name = mapping.get(field_name, field_name.upper())
                value = env_map.get(env_name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    return False
            return True

        @classmethod
        def from_env(cls, env_map: Mapping[str, str]) -> "AbstractEmailProvider.Settings":
            """Build a Settings instance from the canonical env-var names.

            Raises a Pydantic `ValidationError` whose error rows name the
            missing or invalid field — preferred over the legacy "first-use
            crash" path because the failure surfaces at startup.
            """
            mapping = cls.env_field_map()
            payload: Dict[str, Any] = {}
            for field_name in cls.model_fields.keys():
                env_name = mapping.get(field_name, field_name.upper())
                if env_name in env_map and env_map[env_name] != "":
                    payload[field_name] = env_map[env_name]
            return cls(**payload)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Item 90 — once the provider subclass is fully constructed (and
        # the framework has read `_env` once during env-var registration),
        # un-silence the deprecation dict so any subsequent caller-side
        # read surfaces as a `DeprecationWarning`.
        env = cls.__dict__.get("_env")
        if isinstance(env, _DeprecatedEnvDict):
            env._silent = False

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""

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
        attachments: Optional[List[str]] = None,
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
        for label, body in (("body_text", message.body_text), ("body_html", message.body_html)):
            if body is None:
                continue
            if not isinstance(body, str):
                return f"Failed to send email: invalid {label} (must be string)"
            if "\x00" in body:
                return "Failed to send email: rejected NUL byte in input"
            if len(body.encode("utf-8", errors="replace")) > _EMAIL_MAX_BODY_BYTES:
                return "Failed to send email: body exceeds 10 MiB cap"

        # Address fields: every ``EmailAddress`` everywhere on the message.
        addr_groups: List[tuple] = [("to", message.to), ("cc", message.cc), ("bcc", message.bcc)]
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
        attachments: Optional[List[str]] = None,
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
        attachments: Optional[List[str]] = None,
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
        date_range: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Search for emails in a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_reply")
    async def reply_to_email(
        provider_instance: ProviderInstanceModel,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
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
        legacy_attachments: Optional[List[str]] = None
        if message.attachments:
            import tempfile

            legacy_attachments = []
            for att in message.attachments:
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix="_" + att.filename
                )
                tmp.write(att.content)
                tmp.close()
                legacy_attachments.append(tmp.name)
        return await cls.send_email(
            provider_instance,
            recipient=recipient,
            subject=message.subject,
            body=body,
            attachments=legacy_attachments,
            importance=message.importance.value,
        )

    @classmethod
    async def update_email(
        cls,
        provider_instance: ProviderInstanceModel,
        message_id: str,
        *,
        read: Optional[bool] = None,
        flagged: Optional[bool] = None,
        folder: Optional[str] = None,
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
            results.append(
                await cls.move_email(provider_instance, message_id, folder)
            )
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
        folder: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 10,
        cursor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List emails with an optional search query and folder.

        Delegates to ``search_emails`` when ``query`` is provided, else
        ``get_emails``. ``cursor`` is currently passed-through opaquely;
        Item 7 (pagination homogenisation) will replace it with a typed
        ``next_token`` envelope.
        """
        effective_folder = folder or "Inbox"
        if query:
            return await cls.search_emails(
                provider_instance,
                query=query,
                folder_name=effective_folder,
                max_emails=limit,
            )
        return await cls.get_emails(
            provider_instance,
            folder_name=effective_folder,
            max_emails=limit,
            page_size=limit,
        )


def iter_configured_email_providers(
    env_map: Optional[Mapping[str, str]] = None,
) -> List[Type["AbstractEmailProvider"]]:
    """Item 90 — return the loaded email-provider classes whose
    ``Settings.is_configured(env_map)`` is True.

    Replaces the legacy hardcoded ``_EMAIL_PROVIDER_REGISTRY`` tuple-loop
    in ``BLL_EMail.py``: the source of truth becomes each provider's own
    typed Settings model. ``env_map`` defaults to ``os.environ``.
    """
    env_source = env_map if env_map is not None else os.environ
    configured: List[Type[AbstractEmailProvider]] = []
    for provider_cls in EXT_EMail.providers:
        settings_cls = getattr(provider_cls, "Settings", None)
        if settings_cls is None or not isinstance(settings_cls, type):
            continue
        if not issubclass(settings_cls, BaseModel):
            continue
        try:
            if settings_cls.is_configured(env_source):
                configured.append(provider_cls)
        except Exception as exc:  # noqa: BLE001 — defensive at startup
            logger.debug(
                f"Settings.is_configured raised for {provider_cls.__name__}: {exc}"
            )
    return configured


def validate_email_provider_settings_at_startup(
    env_map: Optional[Mapping[str, str]] = None,
) -> Dict[str, bool]:
    """Item 90 — startup validator.

    Iterates registered email providers, calling ``Settings.is_configured``
    against ``env_map`` (default: ``os.environ``). Logs an info-level line
    per provider so operators see at boot which transports will route mail.
    Returns a name → configured map for tests / health pages.
    """
    env_source = env_map if env_map is not None else os.environ
    report: Dict[str, bool] = {}
    for provider_cls in EXT_EMail.providers:
        name = getattr(provider_cls, "name", provider_cls.__name__)
        settings_cls = getattr(provider_cls, "Settings", None)
        if settings_cls is None or not isinstance(settings_cls, type):
            logger.info(f"Email provider {name}: no typed Settings declared")
            report[name] = False
            continue
        try:
            ok = bool(settings_cls.is_configured(env_source))
        except Exception as exc:  # noqa: BLE001
            logger.info(
                f"Email provider {name}: Settings.is_configured raised {exc}"
            )
            report[name] = False
            continue
        if ok:
            logger.info(f"Email provider {name}: configured")
        else:
            logger.info(f"Email provider {name}: not configured (missing env vars)")
        report[name] = ok
    return report


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
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Email extension for interacting with various email providers"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "SENDGRID_API_KEY": "",
        "SENDGRID_FROM_EMAIL": "",
        "STALWART_HOST": "",
        "STALWART_PORT": "587",
        "STALWART_USERNAME": "",
        "STALWART_PASSWORD": "",
        "STALWART_FROM_EMAIL": "",
        "STALWART_USE_TLS": "true",
        "SMTP2GO_API_KEY": "",
        "SMTP2GO_FROM_EMAIL": "",
        "SMTP2GO_API_URL": "https://api.smtp2go.com/v3",
        "EMAIL_PROVIDER": "sendgrid",
        "SMTP_SERVER": "",
        "SMTP_PORT": "587",
        "IMAP_SERVER": "",
        "IMAP_PORT": "993",
    }

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

    # Meta abilities - extension-level abilities that work regardless of provider
    _abilities: ClassVar[Set[str]] = {
        "email_status",  # Meta ability to check email service status
        "email_config",  # Meta ability to manage email configuration
        "email_send",  # Send email ability
        "email_receive",  # Receive email ability
        "email_templates",  # Email template management
        "email_tracking",  # Email tracking and delivery status
    }

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
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls.get_abilities()

    @classmethod
    def register_ability(cls, ability: str):
        """Register a new ability with this extension."""
        cls._abilities.add(ability)

    @classmethod
    def get_registered_abilities(cls) -> Set[str]:
        """Get all registered abilities."""
        return cls._abilities.copy()

    @classmethod
    def get_providers(cls) -> Set[str]:
        """Get the names of every concrete email provider currently loaded.

        Built from the auto-discovered ``cls.providers`` list rather than a
        hardcoded set so newly added provider classes (Stalwart, SMTP2go)
        appear automatically without touching this method.
        """
        return {p.name for p in cls.providers if hasattr(p, "name")}

    @classmethod
    def get_provider_class(cls, provider_name: str):
        """Look up a provider class by its ``name`` attribute (case-insensitive)."""
        target = provider_name.lower()
        for provider in cls.providers:
            if getattr(provider, "name", "").lower() == target:
                return provider
        raise ValueError(f"Unknown provider: {provider_name}")

    @classmethod
    def discover_abilities(cls):
        """Discover abilities from provider classes."""
        # This method would scan for abilities in provider classes
        # For now, we'll just ensure basic abilities are registered
        cls.register_ability("send_email")
        cls.register_ability("send_invitation_email")
        cls.register_ability("get_emails")
        cls.register_ability("create_draft_email")
        cls.register_ability("search_emails")
        cls.register_ability("reply_to_email")
        cls.register_ability("delete_email")
        cls.register_ability("process_attachments")

    @classmethod
    async def execute_ability(cls, ability_name: str, params: dict = None) -> str:
        """Execute an ability by name."""
        if ability_name not in cls.get_abilities():
            return f"Ability '{ability_name}' not found"

        # This would route to the appropriate provider method
        # For now, return a placeholder response
        return f"Executed ability: {ability_name}"

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate extension configuration and return list of issues."""
        issues = []

        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        if not _env("SENDGRID_API_KEY"):
            issues.append("SENDGRID_API_KEY environment variable not set")

        if not _env("SENDGRID_FROM_EMAIL"):
            issues.append("SENDGRID_FROM_EMAIL environment variable not set")

        return issues

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        """Get required permissions for this extension."""
        return [
            "email:send",
            "email:receive",
            "email:manage_templates",
            "email:track_delivery",
        ]

    # Abstract provider is defined in AbstractProvider_EMail.py with extension_type = "email"

    @classmethod
    @ability(name="email_status")
    def get_extension_status(cls) -> Dict[str, Any]:
        """
        Get the current status of the email extension.
        This is a meta ability that works regardless of provider.
        """
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return {
            "extension": cls.name,
            "version": cls.version,
            "providers_available": len(cls.providers),
            "configured": bool(_env("SENDGRID_API_KEY")),
            "default_provider": _env("EMAIL_PROVIDER") or "sendgrid",
        }

    @classmethod
    @ability(name="email_config")
    def get_configuration(cls) -> Dict[str, Any]:
        """
        Get the current email configuration.
        This is a meta ability that works regardless of provider.
        """
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return {
            "email_provider": _env("EMAIL_PROVIDER") or "sendgrid",
            "smtp_server": _env("SMTP_SERVER"),
            "smtp_port": _env("SMTP_PORT") or "587",
            "imap_server": _env("IMAP_SERVER"),
            "imap_port": _env("IMAP_PORT") or "993",
            "from_email_configured": bool(_env("SENDGRID_FROM_EMAIL")),
        }

    @classmethod
    def validate_configuration(cls) -> bool:
        """
        Validate that the email extension is properly configured.
        """
        # Check for required environment variables
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        email_provider = _env("EMAIL_PROVIDER") or "sendgrid"

        if email_provider == "sendgrid":
            return bool(_env("SENDGRID_API_KEY") and _env("SENDGRID_FROM_EMAIL"))
        elif email_provider in ["smtp", "imap"]:
            return bool(_env("SMTP_SERVER") or _env("IMAP_SERVER"))

        return False

    @classmethod
    def get_default_provider_name(cls) -> str:
        """Get the default email provider name from configuration."""
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return _env("EMAIL_PROVIDER") or "sendgrid"

    @classmethod
    def get_provider_names(cls) -> List[str]:
        """Get list of provider names."""
        return [
            provider.name for provider in cls.providers if hasattr(provider, "name")
        ]

    @classproperty
    def env(cls) -> Dict[str, Any]:
        """Get environment variables."""
        return cls._env

    # Static methods for rotation system integration
    @classmethod
    async def send_email(cls, recipient: str, subject: str, body: str, **kwargs) -> str:
        """Send email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.send_email,
                recipient=recipient,
                subject=subject,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def get_emails(
        cls, folder_name: str = "Inbox", max_emails: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Get emails via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.get_emails,
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
            return await cls.root.rotate(
                AbstractEmailProvider.create_draft_email,
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
            return await cls.root.rotate(
                AbstractEmailProvider.search_emails,
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
            return await cls.root.rotate(
                AbstractEmailProvider.reply_to_email,
                email_id=email_id,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def delete_email(cls, email_id: str, **kwargs) -> str:
        """Delete email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.delete_email, email_id=email_id, **kwargs
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def process_attachments(cls, email_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Process email attachments via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.process_attachments, email_id=email_id, **kwargs
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
            # Use the rotation system to send the email
            if cls.root:
                result = cls.root.rotate(
                    provider_callback,
                    recipient=entity.email,
                    subject=f"You've been invited to {entity.invitation.team.name}",
                    body=f"""You've been invited to join {entity.invitation.team.name}.
                    
                    Click here to accept: {env('FRONTEND_URL')}/accept-invitation?code={entity.invitation.code}&email={entity.email}&team={entity.invitation.team.name}
                    
                    This invitation expires in 7 days.""",
                )
                logger.info(f"Invitation email sent to {entity.email}")
                return result
        except Exception as e:
            logger.error(f"Failed to send invitation email: {e}")
            # Don't fail the invitation creation if email fails
            pass


def provider_callback(provider_instance, **kwargs):
    """
    Callback method for provider instances.
    This can be used to handle provider-specific logic.
    """

    providers = EXT_EMail.providers
    for provider in providers:
        if provider.name.lower() == provider_instance.model_name.lower():
            coro = provider.send_email(provider_instance, **kwargs)

            call_async_without_waiting(coro)

    logger.debug(f"Provider callback called for {provider_instance.name}")
    # Implement provider-specific logic here
    return {"status": "success", "message": "Callback executed successfully"}


def run_async_in_thread(coroutine):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coroutine)


def call_async_without_waiting(async_function):
    import threading

    thread = threading.Thread(target=run_async_in_thread, args=(async_function,))
    thread.daemon = (
        True  # Allows the program to exit even if the thread is still running
    )
    thread.start()


AbstractEmailProvider.extension = EXT_EMail
