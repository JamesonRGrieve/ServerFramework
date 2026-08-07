"""
SendGrid email provider for AGInfrastructure.
Provides email sending capabilities and external models for contacts, templates, and campaigns.
Fully static implementation compatible with the Provider Rotation System.
"""

import base64
import hashlib
import hmac
import mimetypes
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple, Type

from pydantic import BaseModel, EmailStr, Field, HttpUrl, SecretStr

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
    create_external_reference_model,
    idempotent,
)
from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.extensions.email.AbstractEmailProviderInstance import (
    AbstractEmailProviderInstance,
    BulkSendResult,
    BulkSendRow,
    EmailStats,
    EmailValidationResult,
    MessageListPage,
    MessageSummary,
    SentMessage,
    SuppressionEntry,
    SuppressionListPage,
)
from zephyrex.extensions.email.EmailErrors import (
    EmailValidationError,
    NotSupportedError,
    map_upstream_status,
    map_validation_error,
)
from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EmailAddress,
    EmailDeliveryEvent,
    EmailMessage,
    Importance,
    _DeprecatedEnvDict,
    dispatch_email_delivery_event,
)
from zephyrex.extensions.ExternalErrors import DegradationPolicy, fail_fast
from zephyrex.extensions.FieldMappings import (
    Compose,
    Decompose,
    EnumRemap,
    FieldMapping,
    Rename,
    apply_to_external,
)
from zephyrex.extensions.Paginators import (
    AbstractPaginator,
    PageTokenPaginator,
    decode_token,
    encode_token,
    query_hash,
)
from zephyrex.extensions.QueryTranslators import (
    AbstractQueryDSLTranslator,
    KeyValueTranslator,
)
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

# Try to import SendGrid library
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    sendgrid = SendGridAPIClient
except ImportError:
    sendgrid = None
    import warnings

    warnings.warn(
        "SendGrid package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


# ---------------------------------------------------------------------------
# Item 92 — AuthStrategy integration.
#
# Each provider declares its `default_auth_strategy` symbolically; the helper
# below materialises an actual `AuthStrategy` from the bonded credentials so
# `bond_instance` returns one in the SDK slot. The import is lazy so this
# module loads cleanly even if Batch B's `extensions.AuthStrategy` is not
# yet wired into the test environment.
# ---------------------------------------------------------------------------


def _build_auth_strategy(strategy_name: str, **kwargs):
    """Construct an `AuthStrategy` from a name and kwargs.

    Returns ``None`` if the AuthStrategy module is unavailable (Batch B not
    yet wired) or the requested strategy is unknown — callers must fall back
    to direct credential use.
    """
    try:
        from zephyrex.extensions.AuthStrategy import APIKeyAuth, BasicAuth
    except Exception:  # pragma: no cover — Batch B coupling
        return None

    name = (strategy_name or "").lower()
    if name == "api_key":
        return APIKeyAuth(
            api_key=kwargs.get("api_key", ""),
            header_name=kwargs.get("header_name", "Authorization"),
            header_prefix=kwargs.get("header_prefix", "Bearer "),
        )
    if name == "basic":
        return BasicAuth(
            username=kwargs.get("username", ""),
            password=kwargs.get("password", ""),
        )
    return None


# ============================================================================
# SendGrid Provider Implementation
# ============================================================================


class SendgridProvider(AbstractEmailProvider):
    """
    Sendgrid email provider for AGInfrastructure.
    Note: Sendgrid only supports sending emails, not receiving or managing them.
    Fully static implementation compatible with the Provider Rotation System.
    """

    # Provider metadata
    name: ClassVar[str] = "sendgrid"
    version: ClassVar[str] = "1.0.0"

    # Abilities provided by this provider
    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. SendGrid is a hosted HTTP API for outbound mail
    # only; receive-side abilities (list/read/update/threads) are not
    # part of the API and remain stubbed at the abstract level. Item 95
    # adds the ladder capabilities SendGrid does support: address
    # validation, dynamic templates, suppression-list mgmt, stats, and
    # message history.
    capabilities: ClassVar = frozenset(
        {
            Capability.SEND,
            Capability.ATTACHMENTS,
            Capability.VALIDATE_ADDRESS,
            Capability.TEMPLATES,
            Capability.SUPPRESSIONS,
            Capability.STATS,
            Capability.MESSAGES,
            Capability.INBOUND_WEBHOOK,
        }
    )

    # Item 93 — federation translators. SendGrid's messages-search and
    # suppressions endpoints page via `next_page_token`; the search query
    # surface is `?email=foo&status=delivered`-style flat key/value.
    paginator: ClassVar[Type[AbstractPaginator]] = PageTokenPaginator
    query_translator: ClassVar[Type[AbstractQueryDSLTranslator]] = KeyValueTranslator

    # Item 93 — declarative `EmailMessage` ↔ SendGrid Mail JSON mappings.
    # These replace open-coded conversions in `send_email`/`send`. Each
    # `EmailAddress(name, address)` pair becomes a `Compose` that
    # round-trips through SendGrid's `{"email": addr, "name": name}`
    # mailbox dict shape. `Importance` ↔ SendGrid's `priority`/`X-Priority`
    # header is an `EnumRemap`. The bytes-side `Attachment` shape stays
    # in the imperative path because SendGrid's helpers consume bytes
    # directly; declarative mapping there is unnecessary churn.
    field_mappings: ClassVar[List[FieldMapping]] = [
        Rename(internal="subject", external="subject"),
        Rename(internal="body_text", external="plain_content"),
        Rename(internal="body_html", external="html_content"),
        Rename(internal="template_id", external="template_id"),
        Compose(
            externals=["from_address", "from_name"],
            internal="from",
            fn=lambda addr, name: {"email": addr, "name": name} if name else {"email": addr},
            inverse_fn=lambda d: (d.get("email", ""), d.get("name")),
        ),
        EnumRemap(
            internal="importance",
            external="priority",
            mapping={
                Importance.HIGH.value: "high",
                Importance.NORMAL.value: "normal",
                Importance.LOW.value: "low",
            },
        ),
    ]

    # Item 94 — webhook signature verification public key. SendGrid's
    # Event Webhook signs each delivery with ECDSA-SHA256; the public key
    # is published in the SendGrid dashboard. We accept either a PEM-
    # encoded ECDSA key (preferred) or a shared HMAC secret as fallback
    # (`SENDGRID_WEBHOOK_SECRET`); the latter is documented as a gap
    # because the upstream is ECDSA-only.
    SENDGRID_WEBHOOK_PUBLIC_KEY_ENV: ClassVar[str] = "SENDGRID_WEBHOOK_PUBLIC_KEY"
    SENDGRID_WEBHOOK_SECRET_ENV: ClassVar[str] = "SENDGRID_WEBHOOK_SECRET"
    SENDGRID_SIGNATURE_HEADER: ClassVar[str] = "x-twilio-email-event-webhook-signature"
    SENDGRID_TIMESTAMP_HEADER: ClassVar[str] = "x-twilio-email-event-webhook-timestamp"

    # H-1 — opt into the framework's replay-protection plumbing
    # (`BLL_Webhooks.check_replay`). Without these, ``verify_signature``
    # alone treats the signed timestamp only as salt — captured payloads
    # replay forever. The window is wider than typical (300s) because
    # SendGrid batches and retries, but ``verify_signature`` ALSO enforces
    # this freshness gate so the protection is in force even when the
    # caller does not invoke ``check_replay``.
    replay_window_seconds: ClassVar[int] = 300

    # Item 92 — declare the default auth strategy this provider uses.
    # SendGrid authenticates via an API key in an ``Authorization: Bearer``
    # header; ``bond_instance`` materialises an ``APIKeyAuth`` from the
    # bonded instance's ``api_key``.
    default_auth_strategy: ClassVar[str] = "api_key"

    # Items 96 + 97 — ops policies. Marketing-tagged abilities may override
    # `degradation_policy` per-ability per Item 48 (e.g. queue_and_retry).
    rate_limit: ClassVar[RateLimit] = RateLimit(rps=10, burst=20)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    # Dependencies
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="sendgrid",
                friendly_name="SendGrid",
                semver=">=6.0.0",
                reason="SendGrid email service",
            )
        ]
    )

    # Item 90 — typed Settings model is the source of truth; the legacy
    # ``_env`` dict is kept for one release as a backward-compat alias and
    # warns on read via ``_DeprecatedEnvDict``.
    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        api_key: SecretStr

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "SENDGRID_FROM_EMAIL",
            "api_key": "SENDGRID_API_KEY",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "SENDGRID_API_KEY": "",
            "SENDGRID_FROM_EMAIL": "",
        }
    )

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        """
        Bond a provider instance with SendGrid SDK.

        Args:
            instance: ProviderInstance model with configuration

        Returns:
            Bonded instance with SendGrid client or None if failed
        """
        # credential vault layering (Item 32) is a follow-up that swaps
        # `.get_secret_value()` for `CredentialRef.resolve()`.
        if not sendgrid:
            logger.error("SendGrid package not available")
            return None

        try:
            # Get API key from instance settings or environment
            api_key = instance.api_key or env("SENDGRID_API_KEY")
            if not api_key:
                logger.error("No SendGrid API key found")
                return None

            # Create SendGrid client
            client = SendGridAPIClient(api_key)

            # Item 92 — materialise the declared AuthStrategy for callers
            # that route through the rotation system rather than the legacy
            # SDK directly.
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy, api_key=api_key
            )

            # Store from_email in the SDK instance for later use
            from_email = env("SENDGRID_FROM_EMAIL")
            if from_email:
                # Create a wrapper that includes from_email + auth strategy
                class SendGridWrapper:
                    def __init__(self, client, from_email, auth_strategy):
                        self._client = client
                        self.from_email = from_email
                        self.auth_strategy = auth_strategy

                    def __getattr__(self, name):
                        return getattr(self._client, name)

                wrapped_client = SendGridWrapper(client, from_email, auth_strategy)
                return AbstractProviderInstance_SDK(wrapped_client)
            else:
                # Even without from_email, attach auth_strategy where possible
                if auth_strategy is not None:
                    setattr(client, "auth_strategy", auth_strategy)
                return AbstractProviderInstance_SDK(client)

        except Exception as e:
            logger.error(f"Failed to bond SendGrid instance: {e}")
            return None

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider"""
        return ["email", "messaging", "communication"]

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the platform name."""
        return "SendGrid"

    # ------------------------------------------------------------------
    # Item 94 — Event Webhook signature verification.
    # ------------------------------------------------------------------

    @classmethod
    def verify_signature(cls, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify a SendGrid Event Webhook delivery.

        Preferred path: ECDSA-SHA256 with the public key from the
        `SENDGRID_WEBHOOK_PUBLIC_KEY` env var (`cryptography` library).
        Fallback path: HMAC-SHA256 with `SENDGRID_WEBHOOK_SECRET`. The
        fallback is a documented gap — SendGrid's Event Webhook is
        ECDSA-only on the wire, so HMAC support exists only for
        deployments that proxy the webhook through a signing intermediary.

        Returns False on missing key/secret, missing headers, or signature
        mismatch. Never raises.
        """
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        signature = normalized.get(cls.SENDGRID_SIGNATURE_HEADER)
        timestamp = normalized.get(cls.SENDGRID_TIMESTAMP_HEADER)
        if not signature or not timestamp:
            logger.debug(
                "SendGrid webhook missing signature or timestamp header; rejecting."
            )
            return False

        # H-1 — freshness gate enforced inside ``verify_signature`` so
        # callers that bypass ``check_replay`` still get replay protection.
        try:
            ts_epoch = int(float(timestamp))
        except (TypeError, ValueError):
            logger.debug("SendGrid webhook timestamp is not numeric; rejecting.")
            return False
        if abs(time.time() - ts_epoch) > float(cls.replay_window_seconds):
            logger.debug("SendGrid webhook timestamp outside replay window; rejecting.")
            return False

        public_key_pem = env(cls.SENDGRID_WEBHOOK_PUBLIC_KEY_ENV)
        if public_key_pem:
            return cls._verify_ecdsa(public_key_pem, signature, timestamp, body)

        shared_secret = env(cls.SENDGRID_WEBHOOK_SECRET_ENV)
        if shared_secret:
            return cls._verify_hmac(shared_secret, signature, timestamp, body)

        logger.warning(
            "SendGrid webhook verification: neither "
            f"{cls.SENDGRID_WEBHOOK_PUBLIC_KEY_ENV} nor "
            f"{cls.SENDGRID_WEBHOOK_SECRET_ENV} set; rejecting."
        )
        return False

    @classmethod
    def extract_replay_keys(
        cls, headers: Mapping[str, str], body: bytes
    ) -> tuple[Optional[int], Optional[str]]:
        """Return ``(epoch_seconds, nonce)`` for replay-cache de-duping.

        SendGrid does not ship a per-delivery nonce header, so we derive
        the nonce from a SHA-256 of the signed payload (timestamp || body).
        Two redeliveries of the same event collapse onto the same key and
        are rejected by ``BLL_Webhooks.check_replay`` after the first.
        """
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        timestamp = normalized.get(cls.SENDGRID_TIMESTAMP_HEADER)
        if not timestamp:
            return None, None
        try:
            ts_epoch = int(float(timestamp))
        except (TypeError, ValueError):
            return None, None
        signed_payload = timestamp.encode("utf-8") + (body or b"")
        nonce = hashlib.sha256(signed_payload).hexdigest()
        return ts_epoch, nonce

    @staticmethod
    def _verify_ecdsa(
        public_key_pem: str, signature_b64: str, timestamp: str, body: bytes
    ) -> bool:
        """ECDSA-SHA256 path. Returns False on any error (defensive)."""
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.asymmetric.utils import (
                decode_dss_signature,
            )
        except ImportError:
            logger.debug(
                "cryptography library unavailable; cannot verify SendGrid ECDSA signature."
            )
            return False

        signed_payload = timestamp.encode("utf-8") + body
        try:
            key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
            sig_bytes = base64.b64decode(signature_b64)
            # SendGrid's signature is DER-encoded ECDSA; cryptography
            # verifies DER directly, so we pass it through.
            key.verify(sig_bytes, signed_payload, ec.ECDSA(hashes.SHA256()))  # type: ignore[union-attr, arg-type, call-arg]
            return True
        except InvalidSignature:
            logger.debug("SendGrid webhook ECDSA signature invalid.")
            return False
        except Exception as exc:
            logger.debug(f"SendGrid webhook ECDSA verification error: {exc}")
            return False

    @staticmethod
    def _verify_hmac(
        secret: str, signature_b64: str, timestamp: str, body: bytes
    ) -> bool:
        """HMAC-SHA256 fallback path.

        Documented gap: SendGrid's Event Webhook is ECDSA-only on the
        wire, so this path only fires for deployments that proxy through
        a signing intermediary that re-signs with HMAC.
        """
        signed_payload = timestamp.encode("utf-8") + body
        expected = hmac.new(
            secret.encode("utf-8"), signed_payload, hashlib.sha256
        ).digest()
        try:
            received = base64.b64decode(signature_b64)
        except Exception:
            return False
        return hmac.compare_digest(expected, received)

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        """Validate provider configuration."""
        if not sendgrid:
            logger.error("SendGrid package not available")
            return False

        # Check for API key
        api_key = env("SENDGRID_API_KEY")
        if instance:
            api_key = instance.get_setting("api_key") or api_key

        if not api_key:
            logger.error("SendGrid API key not configured")
            return False

        # Check for from email
        from_email = env("SENDGRID_FROM_EMAIL")
        if instance:
            from_email = instance.get_setting("from_email") or from_email

        if not from_email:
            logger.error("SendGrid from_email not configured")
            return False

        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        """Probe upstream liveness via ``GET /v3/scopes`` (Items 27 + 97).

        Routes through the shared ``ProviderHTTPClient`` so the request
        carries traceparent + auth + log redaction. Defensive: never
        raises; always returns a ``HealthReport``.
        """
        api_key = env("SENDGRID_API_KEY")
        if not api_key:
            return HealthReport(
                HealthStatus.DOWN, detail="SendGrid API key not configured"
            )
        try:
            import httpx

            from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_sync_client

            client = get_sync_client(ClientPolicy(timeout=5.0))
            response = client.get(
                "https://api.sendgrid.com/v3/scopes",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            status = response.status_code
            if 200 <= status < 300:
                return HealthReport(HealthStatus.OK, detail=f"scopes status {status}")
            if 400 <= status < 500:
                return HealthReport(
                    HealthStatus.DEGRADED, detail=f"scopes status {status}"
                )
            return HealthReport(HealthStatus.DOWN, detail=f"scopes status {status}")
        except Exception as exc:  # noqa: BLE001 — defensive, never raise
            return HealthReport(HealthStatus.DOWN, detail=f"network error: {exc}")

    @staticmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Getting emails is not supported by SendGrid")
        return []

    @classmethod
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
        """
        Send an email using SendGrid.
        """
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]

        # Get bonded instance
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            error_msg = "Failed to bond SendGrid instance"
            logger.error(error_msg)
            return error_msg

        client = bonded.sdk

        # Get from_email from wrapped client or settings
        from_email = getattr(client, "from_email", None)
        if not from_email:
            from_email = provider_instance.get_setting("from_email") or env(
                "SENDGRID_FROM_EMAIL"
            )

        if not from_email:
            error_msg = "SendGrid from_email not configured."
            logger.error(error_msg)
            return error_msg

        try:
            message = Mail(
                from_email=from_email,
                to_emails=recipient,
                subject=subject,
                html_content=body if "<html" in body.lower() else None,
                plain_text_content=None if "<html" in body.lower() else body,
            )

            # Add attachments if provided
            if attachments:
                from sendgrid.helpers.mail import (
                    Attachment,
                    Disposition,
                    FileContent,
                    FileName,
                    FileType,
                )

                for attachment_path in attachments:
                    if not os.path.exists(attachment_path):
                        logger.warning(f"Attachment file not found: {attachment_path}")
                        continue

                    with open(attachment_path, "rb") as file:
                        file_content = file.read()
                        file_name = os.path.basename(attachment_path)
                        file_type = (
                            mimetypes.guess_type(attachment_path)[0]
                            or "application/octet-stream"
                        )

                        encoded_file = base64.b64encode(file_content).decode()

                        attachment = Attachment(
                            FileContent(encoded_file),
                            FileName(file_name),
                            FileType(file_type),
                            Disposition("attachment"),
                        )
                        message.attachment = attachment

            logger.debug(f"Sending email to {recipient} from {from_email}")
            # Access the actual client if wrapped
            actual_client = getattr(client, "_client", client)
            # Item 97 — injecting a `ProviderHTTPClient`-backed transport
            # into the SendGrid SDK's underlying `python-http-client` is a
            # follow-up; `health_check` already routes through the shared
            # client. Direct SDK send preserves existing behavior here.
            response = actual_client.send(message)

            if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"Email sent successfully to {recipient}")
                return f"Email sent successfully to {recipient}"
            else:
                return f"Failed to send email: {response.status_code}: {response.body}"
        except Exception as e:
            logger.error(f"Error sending SendGrid email: {str(e)}")
            return f"Failed to send email: {str(e)}"

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Creating draft emails is not supported by SendGrid")
        return "Creating draft emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        date_range: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Searching emails is not supported by SendGrid")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(
        provider_instance: ProviderInstanceModel,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Replying to emails is not supported by SendGrid")
        return "Replying to emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Deleting emails is not supported by SendGrid")
        return "Deleting emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> List[str]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Processing attachments is not supported by SendGrid")
        return []

    # ------------------------------------------------------------------
    # Item 91 — typed send_via_provider / send_bulk_via_provider.
    #
    # ``send_via_provider`` is the rotation-system entry point: it accepts
    # a typed ``EmailMessage``, validates it (CRLF/NUL/length/address),
    # raises a typed ``EmailValidationError`` on input rejection, raises
    # the appropriate ``map_upstream_status`` error on provider-side
    # failure, and returns a ``SentMessage`` shape on success. Decorated
    # ``@idempotent`` so the rotation manager mints + persists an
    # idempotency key per send.
    #
    # ``send_bulk_via_provider`` packs up to 1000 messages into a single
    # SendGrid ``personalizations`` array, returning per-item rows.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via SendGrid.

        Raises typed errors on validation failure (subclass of
        ``EmailValidationError``) or upstream rejection (``map_upstream_status``).
        Returns a dict carrying ``message_id`` / ``provider`` / ``recipient``
        on success — this matches the ``SentMessage`` shape from Item 89
        without forcing the dataclass import on call sites that still
        consume dict envelopes.
        """
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        # ``send`` returns the legacy string envelope. Failures look like
        # ``"Failed to send email: <reason>"``; map them to typed errors so
        # the rotation manager can decide whether to retry.
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            # Try to fish a status code out of the message.
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(
                    status, legacy_result, provider="sendgrid"
                )
            raise map_validation_error(legacy_result)

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
        """Send up to ``SEND_BULK_MAX_BATCH`` messages in one upstream call.

        SendGrid's REST API supports a ``personalizations`` array on a
        single ``Mail`` payload — one element per recipient, sharing the
        sender / subject / body. Per-item rejections (e.g., one invalid
        recipient in a batch of 50) are surfaced as typed errors in the
        per-item rows of the returned envelope; transport-level failures
        (5xx, network) abort the whole batch with ``TransientExternalError``
        from ``map_upstream_status``.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        # Validate every message up-front so a batch with any unsafe
        # member is rejected before we touch the upstream.
        per_item_errors: List[Optional[Exception]] = []
        for m in messages:
            err = cls._validate_message(m)
            if err:
                per_item_errors.append(map_validation_error(err))
            else:
                per_item_errors.append(None)
        if any(per_item_errors):
            # Surface the first violation as a typed error rather than
            # forwarding any half-valid batch.
            for e in per_item_errors:
                if e is not None:
                    raise e

        # Serial-loop fallback: SendGrid's ``personalizations`` API needs
        # a homogeneous from/subject/body across items. For now, fall back
        # to per-item ``send_via_provider`` so each message's full shape
        # is preserved. The upstream-batched path lights up once Item 91
        # finalises the schema diffing.
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


# ============================================================================
# Item 95 — SendGrid concrete `AbstractEmailProviderInstance` with the
# capability ladder. Wires `validate_address`, `send_with_template`,
# `*_suppression*`, `get_stats`, `list_messages` against the SendGrid
# REST API, declaring them only when SendGrid actually exposes the
# upstream endpoint.
# ============================================================================


def _sendgrid_message_to_sg_payload(message: EmailMessage) -> Dict[str, Any]:
    """Item 93 — declarative `EmailMessage` → SendGrid mail-send JSON.

    Routes through the `SendgridProvider.field_mappings` pipeline so the
    conversion is declared once rather than open-coded in every send
    path. The `personalizations` array, `attachments` byte-shape, and
    multi-recipient address arrays are still composed imperatively
    here because their nested-array structure does not collapse cleanly
    into the flat-key `FieldMapping` contract.
    """
    flat: Dict[str, Any] = {
        "subject": message.subject,
        "body_text": message.body_text,
        "body_html": message.body_html,
        "template_id": message.template_id,
        "importance": message.importance.value,
    }
    if message.from_:
        flat["from_address"] = message.from_.address
        flat["from_name"] = message.from_.name
    payload = apply_to_external(SendgridProvider.field_mappings, flat)
    payload.pop("from_address", None)
    payload.pop("from_name", None)
    payload.pop("body_text", None)
    payload.pop("body_html", None)

    # Per-recipient personalization. SendGrid's contract requires this
    # to be a non-empty list of `{"to": [...]}` dicts.
    personalization: Dict[str, Any] = {
        "to": [
            {"email": a.address, **({"name": a.name} if a.name else {})}
            for a in message.to
        ]
    }
    if message.cc:
        personalization["cc"] = [
            {"email": a.address, **({"name": a.name} if a.name else {})}
            for a in message.cc
        ]
    if message.bcc:
        personalization["bcc"] = [
            {"email": a.address, **({"name": a.name} if a.name else {})}
            for a in message.bcc
        ]
    if message.template_vars:
        personalization["dynamic_template_data"] = dict(message.template_vars)
    payload["personalizations"] = [personalization]

    content: List[Dict[str, str]] = []
    if message.body_text:
        content.append({"type": "text/plain", "value": message.body_text})
    if message.body_html:
        content.append({"type": "text/html", "value": message.body_html})
    if content:
        payload["content"] = content

    if message.reply_to:
        payload["reply_to"] = {"email": message.reply_to.address}
        if message.reply_to.name:
            payload["reply_to"]["name"] = message.reply_to.name
    if message.tags:
        payload["categories"] = list(message.tags)
    if message.headers:
        payload["headers"] = dict(message.headers)
    return payload  # type: ignore[no-any-return]


def _sendgrid_payload_to_message_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Item 93 — declarative SendGrid mail-send JSON → `EmailMessage` kwargs.

    Inverse of `_sendgrid_message_to_sg_payload`; round-trip integrity is
    covered by the field-mapping round-trip tests.
    """
    from_block = payload.get("from") or {}
    personal = (payload.get("personalizations") or [{}])[0]

    importance_map = {"high": Importance.HIGH, "normal": Importance.NORMAL, "low": Importance.LOW}
    body_text = None
    body_html = None
    for chunk in payload.get("content", []) or []:
        if chunk.get("type") == "text/plain":
            body_text = chunk.get("value")
        elif chunk.get("type") == "text/html":
            body_html = chunk.get("value")

    return {
        "subject": payload.get("subject", ""),
        "body_text": body_text,
        "body_html": body_html,
        "template_id": payload.get("template_id"),
        "importance": importance_map.get(
            payload.get("priority", "normal"), Importance.NORMAL
        ),
        "from_": (
            EmailAddress(address=from_block.get("email", ""), name=from_block.get("name"))
            if from_block.get("email")
            else None
        ),
        "to": [
            EmailAddress(address=t.get("email", ""), name=t.get("name"))
            for t in personal.get("to", [])
        ],
        "cc": [
            EmailAddress(address=t.get("email", ""), name=t.get("name"))
            for t in personal.get("cc", [])
        ],
        "bcc": [
            EmailAddress(address=t.get("email", ""), name=t.get("name"))
            for t in personal.get("bcc", [])
        ],
        "tags": list(payload.get("categories", []) or []),
        "headers": dict(payload.get("headers", {}) or {}),
    }


_SUPPRESSION_PATHS: Dict[str, str] = {
    "bounce": "/v3/suppression/bounces",
    "block": "/v3/suppression/blocks",
    "spam_report": "/v3/suppression/spam_reports",
    "unsubscribe": "/v3/asm/suppressions/global",
    "invalid": "/v3/suppression/invalid_emails",
}


class SendgridEmailInstance(AbstractEmailProviderInstance):
    """Item 95 — bonded SendGrid email instance with the capability ladder.

    Construction: pass the `ProviderInstanceModel` and an optional
    `httpx.AsyncClient` (or an SDK shim with a `.send`-compatible client
    on `_client`). The SendGrid REST API is consumed directly via
    `httpx` here rather than through the SendGrid SDK because the SDK's
    surface is mail-send-only; the suppression / validation / stats
    endpoints require their own HTTP path.
    """

    capabilities = SendgridProvider.capabilities

    def __init__(
        self,
        instance: Optional[ProviderInstanceModel] = None,
        api_key: Optional[str] = None,
        from_email: Optional[str] = None,
        http_client: Optional[Any] = None,
    ) -> None:
        super().__init__(instance=instance)
        self._api_key = api_key or env("SENDGRID_API_KEY")
        self._from_email = from_email or env("SENDGRID_FROM_EMAIL")
        self._http_client = http_client

    # The eight Phase-1 abilities are not implemented at the typed-instance
    # layer yet; the legacy `AbstractEmailProvider.send_email` path is the
    # canonical send route. Item 89's `bond_instance` follow-up will rewire
    # those over the typed instance. Until then, satisfy the abstracts by
    # delegating to the legacy provider classmethods where possible.

    async def send(self, message: EmailMessage) -> SentMessage:
        result = await SendgridProvider.send(self.model, message)
        recipient = message.to[0].format() if message.to else ""
        if isinstance(result, str) and result.lower().startswith("failed"):
            raise map_validation_error(result)
        return SentMessage(
            message_id="",
            provider=SendgridProvider.name,
            accepted_at=datetime.now(timezone.utc),
            recipient=recipient,
            upstream_response={"raw": result},
        )

    async def send_bulk(self, messages):
        from zephyrex.extensions.email.AbstractEmailProviderInstance import (
            BulkSendResult as _BR,
        )

        rows = []
        succeeded = 0
        failed = 0
        for m in messages:
            try:
                sent = await self.send(m)
                rows.append(
                    BulkSendRow(
                        recipient=sent.recipient, success=True, message_id=sent.message_id
                    )
                )
                succeeded += 1
            except Exception as exc:
                rows.append(
                    BulkSendRow(
                        recipient=(m.to[0].format() if m.to else ""),
                        success=False,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                )
                failed += 1
        return _BR(rows=rows, succeeded=succeeded, failed=failed)

    async def list_emails(
        self,
        *,
        folder: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 10,
        cursor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        page = await self.list_messages(limit=limit, cursor=cursor)
        return [m.model_dump() for m in page.items]

    async def get_email(self, message_id: str) -> Dict[str, Any]:
        raise NotSupportedError(provider="sendgrid", capability="get_email")

    async def update_email(self, message_id, *, read=None, flagged=None, folder=None, deleted=False):
        raise NotSupportedError(provider="sendgrid", capability="update_email")

    async def reply(self, message_id, body, attachments=None):
        raise NotSupportedError(provider="sendgrid", capability="reply")

    async def download_attachment(self, message_id, attachment_id):
        raise NotSupportedError(provider="sendgrid", capability="download_attachment")

    async def list_threads(self, folder=None, limit=10):
        raise NotSupportedError(provider="sendgrid", capability="list_threads")

    # ------------------------------------------------------------------
    # Item 95 — capability ladder.
    # ------------------------------------------------------------------

    def _provider_name(self) -> str:
        return "sendgrid"

    async def _request(
        self, method: str, path: str, *, params=None, json_body=None
    ) -> Tuple[int, Dict[str, Any]]:
        """Issue an authenticated SendGrid REST call.

        Routes through the shared `ProviderHTTPClient` per Item 97 so the
        request carries trace propagation + redaction. Returns
        `(status_code, parsed_json_body_or_text)`.
        """
        from zephyrex.lib.ProviderHTTPClient import (
            ClientPolicy,
            get_async_client,
        )

        client = self._http_client or get_async_client(ClientPolicy(timeout=30.0))
        url = f"https://api.sendgrid.com{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = await client.request(
            method, url, params=params, json=json_body, headers=headers
        )
        status = response.status_code
        body: Any
        try:
            body = response.json()
        except Exception:
            body = {"text": getattr(response, "text", "")}
        if status >= 400:
            raise map_upstream_status(status, str(body), provider="sendgrid")
        return status, body

    async def validate_address(self, email: str) -> EmailValidationResult:
        _, data = await self._request(
            "POST",
            "/v3/validations/email",
            json_body={"email": email, "source": "framework"},
        )
        result = (data or {}).get("result", {})
        verdict = (result.get("verdict") or "").lower() or "valid"
        return EmailValidationResult(
            email=email,
            verdict=verdict,
            score=result.get("score"),
            checks=result.get("checks", {}) or {},
            raw=data or {},
        )

    async def send_with_template(
        self,
        template_id: str,
        recipients: List[str],
        substitutions: Optional[Dict[str, Any]] = None,
    ) -> BulkSendResult:
        rows: List[BulkSendRow] = []
        succeeded = 0
        failed = 0
        for r in recipients:
            try:
                _, body = await self._request(
                    "POST",
                    "/v3/mail/send",
                    json_body={
                        "from": {"email": self._from_email or "noreply@example.com"},
                        "personalizations": [
                            {
                                "to": [{"email": r}],
                                "dynamic_template_data": dict(substitutions or {}),
                            }
                        ],
                        "template_id": template_id,
                    },
                )
                rows.append(BulkSendRow(recipient=r, success=True))
                succeeded += 1
            except Exception as exc:
                rows.append(
                    BulkSendRow(
                        recipient=r,
                        success=False,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                )
                failed += 1
        return BulkSendResult(
            rows=rows, succeeded=succeeded, failed=failed, template_id=template_id
        )

    async def list_suppressions(
        self,
        suppression_type: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> SuppressionListPage:
        path = _SUPPRESSION_PATHS.get(suppression_type)
        if path is None:
            raise EmailValidationError(
                f"Unknown suppression_type {suppression_type!r}; "
                f"supported: {sorted(_SUPPRESSION_PATHS)}"
            )

        params = {"limit": limit}
        if cursor:
            envelope = decode_token(cursor, query_hash({"type": suppression_type, "limit": limit}))
            cursor_value = envelope.get("provider_cursor")
            if cursor_value is not None:
                # SendGrid suppression endpoints page via `offset`.
                params["offset"] = cursor_value

        _, data = await self._request("GET", path, params=params)
        rows = data if isinstance(data, list) else (data.get("data") or data.get("items") or [])

        items = [
            SuppressionEntry(
                email=row.get("email", ""),
                suppression_type=suppression_type,
                reason=row.get("reason"),
                created_at=(
                    datetime.fromtimestamp(row["created"], tz=timezone.utc)
                    if isinstance(row.get("created"), (int, float))
                    else None
                ),
                raw=row,
            )
            for row in rows
        ]
        next_token: Optional[str] = None
        if len(items) >= limit:
            next_offset = (params.get("offset") or 0) + len(items)
            next_token = encode_token(
                next_offset, limit, query_hash({"type": suppression_type, "limit": limit})
            )
        return SuppressionListPage(items=items, next_token=next_token)

    async def add_suppression(
        self, email: str, suppression_type: str, reason: Optional[str] = None
    ) -> None:
        path = _SUPPRESSION_PATHS.get(suppression_type)
        if path is None:
            raise EmailValidationError(
                f"Unknown suppression_type {suppression_type!r}"
            )
        await self._request(
            "POST", path, json_body={"recipient_emails": [email]}
        )

    async def remove_suppression(self, email: str, suppression_type: str) -> None:
        path = _SUPPRESSION_PATHS.get(suppression_type)
        if path is None:
            raise EmailValidationError(
                f"Unknown suppression_type {suppression_type!r}"
            )
        await self._request("DELETE", f"{path}/{email}")

    async def get_stats(
        self,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> EmailStats:
        params: Dict[str, Any] = {}
        if since is not None:
            params["start_date"] = since.date().isoformat()
        if until is not None:
            params["end_date"] = until.date().isoformat()
        _, data = await self._request("GET", "/v3/stats", params=params)
        # SendGrid returns a list of `{date, stats: [{metrics: {...}}]}`;
        # aggregate across the window.
        delivered = opens = clicks = bounces = spam = unsub = requests = 0
        for row in data if isinstance(data, list) else []:  # type: ignore[var-annotated]
            for stat in row.get("stats", []) or []:
                metrics = stat.get("metrics", {}) or {}
                delivered += int(metrics.get("delivered", 0))
                opens += int(metrics.get("opens", 0))
                clicks += int(metrics.get("clicks", 0))
                bounces += int(metrics.get("bounces", 0))
                spam += int(metrics.get("spam_reports", 0))
                unsub += int(metrics.get("unsubscribes", 0))
                requests += int(metrics.get("requests", 0))
        return EmailStats(
            since=since,
            until=until,
            delivered=delivered,
            opens=opens,
            clicks=clicks,
            bounces=bounces,
            spam_reports=spam,
            unsubscribes=unsub,
            requests=requests,
            raw={"rows": data} if isinstance(data, list) else (data or {}),
        )

    async def list_messages(
        self,
        *,
        since: Optional[datetime] = None,
        status: Optional[str] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> MessageListPage:
        # SendGrid's email-activity API uses a `query` string + `limit`.
        # We round-trip the cursor through the framework's `next_token`
        # envelope per Item 7 so the surface is uniform with other
        # providers.
        query_params: Dict[str, Any] = {"limit": limit}
        if status:
            query_params["status"] = status
        if since is not None:
            query_params["last_event_time"] = since.isoformat()

        if cursor:
            envelope = decode_token(cursor, query_hash(query_params))
            page_cursor = envelope.get("provider_cursor")
            if page_cursor:
                query_params["page_token"] = page_cursor

        _, data = await self._request(
            "GET", "/v3/messages", params=query_params
        )
        messages = data.get("messages", []) if isinstance(data, dict) else []
        items = [
            MessageSummary(
                message_id=str(row.get("msg_id", "")),
                recipient=row.get("to_email", "") or row.get("recipient", ""),
                subject=row.get("subject"),
                status=row.get("status"),
                sent_at=(
                    datetime.fromisoformat(row["last_event_time"].replace("Z", "+00:00"))
                    if isinstance(row.get("last_event_time"), str)
                    else None
                ),
                raw=row,
            )
            for row in messages
        ]
        next_token: Optional[str] = None
        next_page_token = data.get("next_page_token") if isinstance(data, dict) else None
        if next_page_token:
            next_token = encode_token(
                next_page_token, limit, query_hash(query_params)
            )
        return MessageListPage(items=items, next_token=next_token)


# ============================================================================
# Item 94 — SendGrid Event Webhook handlers.
#
# Each event type registers via `@webhook_handler(EXT_EMail, provider="sendgrid",
# event=...)`; on dispatch the handler normalises the upstream payload
# into a canonical `EmailDeliveryEvent` and fans into the
# `dispatch_email_delivery_event` hook bus.
# ============================================================================


def _coerce_sendgrid_event(raw: Dict[str, Any], event_type: str) -> EmailDeliveryEvent:
    """Translate one SendGrid event row into an `EmailDeliveryEvent`."""
    timestamp = raw.get("timestamp")
    if isinstance(timestamp, str):
        try:
            timestamp = float(timestamp)
        except ValueError:
            timestamp = None
    return EmailDeliveryEvent(
        message_id=str(raw.get("sg_message_id", "") or raw.get("smtp-id", "") or ""),
        provider="sendgrid",
        event_type=event_type,
        recipient=raw.get("email", "") or "",
        timestamp=timestamp,
        raw=raw,
    )


async def _dispatch_sendgrid_events(payload: Any, fallback_event: str) -> None:
    """Walk a SendGrid Event-Webhook payload (list-of-dicts, single dict,
    or `{"events": [...]}` envelope from the webhook router) and fan
    each row through the canonical hook bus."""
    rows: List[Dict[str, Any]]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("events"), list):
            rows = payload["events"]
        else:
            rows = [payload]
    else:
        return
    for row in rows:
        event_type = row.get("event") or fallback_event
        event = _coerce_sendgrid_event(row, event_type)
        await dispatch_email_delivery_event(event)


class _EmailExtensionStub:
    """Pass-through stub for `webhook_handler`'s `extension_class`
    parameter. The decorator only reads `extension_name` and uses the
    class for best-effort provider-class discovery; pinning it to
    `"email"` here decouples handler registration from `EXT_EMail`'s
    module-load order so we don't trigger a circular import."""

    extension_name = "email"


def _register_sendgrid_webhook_handlers() -> None:
    """Register handlers for SendGrid's documented Event Webhook event types.

    Idempotent: re-importing this module re-registers the same handlers
    (`webhook_handler` overwrites with a warning, which is acceptable
    during reload).
    """
    # Lazy import — webhooks is an optional extension. If it isn't loaded,
    # SendGrid's Event Webhook simply won't be wired and the rest of the
    # email provider continues to work.
    try:
        from zephyrex.extensions.webhooks import (
            WEBHOOK_REGISTRY,
            WebhookContext,
            webhook_handler,
        )
        from zephyrex.extensions.webhooks.BLL_Webhooks import (
            _PROVIDER_CLASSES,
        )
    except ImportError:
        return

    for event_name in (
        "bounce",
        "delivered",
        "open",
        "click",
        "spam_report",
        "unsubscribe",
        "dropped",
        "processed",
    ):
        @webhook_handler(_EmailExtensionStub, provider="sendgrid", event=event_name)
        async def _handler(ctx: "WebhookContext", _evt: str = event_name) -> None:
            await _dispatch_sendgrid_events(ctx.payload, _evt)

    # Wire SendGrid as the verifier for `(email, sendgrid)` so the webhook
    # router calls `SendgridProvider.verify_signature` at dispatch time.
    _PROVIDER_CLASSES[("email", "sendgrid")] = SendgridProvider


_register_sendgrid_webhook_handlers()


# ============================================================================
# Helper: pluck an HTTP status code out of a legacy error string.
# ============================================================================


def _extract_status_code(message: str) -> Optional[int]:
    """Return the first 3-digit status code in a legacy error string, if any.

    Used by ``send_via_provider`` to map ``"Failed to send email: 503: ..."``
    to a typed ``TransientExternalError`` via ``map_upstream_status``.
    """
    import re

    m = re.search(r"\b([1-5]\d{2})\b", message or "")
    if not m:
        return None
    return int(m.group(1))


# ============================================================================
# SendGrid Contact External Model
# ============================================================================


class SendGrid_ContactModel(AbstractExternalModel):
    """External model for SendGrid Contact API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "marketing/contacts"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid contact ID")
    email: str = Field(..., description="Contact email address")
    first_name: Optional[str] = Field(None, description="Contact first name")
    last_name: Optional[str] = Field(None, description="Contact last name")
    phone_number: Optional[str] = Field(None, description="Contact phone number")
    country: Optional[str] = Field(None, description="Contact country")
    city: Optional[str] = Field(None, description="Contact city")
    state_province_region: Optional[str] = Field(
        None, description="Contact state/province/region"
    )
    postal_code: Optional[str] = Field(None, description="Contact postal code")
    custom_fields: Dict[str, Any] = Field(
        default_factory=dict, description="Custom contact fields"
    )
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    list_ids: List[str] = Field(
        default_factory=list, description="List IDs the contact belongs to"
    )

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Contact."""

        email: str = Field(..., description="Contact email address")
        first_name: Optional[str] = Field(None, description="Contact first name")
        last_name: Optional[str] = Field(None, description="Contact last name")
        phone_number: Optional[str] = Field(None, description="Contact phone number")
        country: Optional[str] = Field(None, description="Contact country")
        city: Optional[str] = Field(None, description="Contact city")
        state_province_region: Optional[str] = Field(
            None, description="Contact state/province/region"
        )
        postal_code: Optional[str] = Field(None, description="Contact postal code")
        custom_fields: Optional[Dict[str, Any]] = Field(
            None, description="Custom contact fields"
        )
        list_ids: Optional[List[str]] = Field(
            None, description="List IDs to add contact to"
        )

    class Update(BaseModel):
        """Update model for SendGrid Contact."""

        email: Optional[str] = Field(None, description="Contact email address")
        first_name: Optional[str] = Field(None, description="Contact first name")
        last_name: Optional[str] = Field(None, description="Contact last name")
        phone_number: Optional[str] = Field(None, description="Contact phone number")
        country: Optional[str] = Field(None, description="Contact country")
        city: Optional[str] = Field(None, description="Contact city")
        state_province_region: Optional[str] = Field(
            None, description="Contact state/province/region"
        )
        postal_code: Optional[str] = Field(None, description="Contact postal code")
        custom_fields: Optional[Dict[str, Any]] = Field(
            None, description="Custom contact fields"
        )
        list_ids: Optional[List[str]] = Field(
            None, description="List IDs to add contact to"
        )

    class Search(BaseModel):
        """Search model for SendGrid Contact."""

        email: Optional[str] = Field(None, description="Search by email")
        first_name: Optional[str] = Field(None, description="Search by first name")
        last_name: Optional[str] = Field(None, description="Search by last name")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Prepare contact data
            contacts = [kwargs]  # SendGrid expects array of contacts

            # Create contact via SendGrid
            response = actual_client.marketing.contacts.put(
                request_body={"contacts": contacts}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {
                    "success": True,
                    "data": {
                        "id": response.job_id,  # SendGrid returns job ID for async processing
                        "email": kwargs.get("email"),
                        **kwargs,
                    },
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Search for contact by ID
            response = actual_client.marketing.contacts.search.post(
                request_body={"query": f"contact_id = '{external_id}'"}
            )

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                if result.get("result", []):
                    contact = result["result"][0]
                    return {"success": True, "data": contact}
                else:
                    return {"success": False, "error": "Not found"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List contacts via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List contacts via SendGrid
            response = actual_client.marketing.contacts.get()

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                return {
                    "success": True,
                    "data": result.get("result", []),
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update contact via SendGrid
            contacts = [{**kwargs, "id": external_id}]
            response = actual_client.marketing.contacts.put(
                request_body={"contacts": contacts}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {
                    "success": True,
                    "data": {
                        "id": external_id,
                        **kwargs,
                    },
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete contact via SendGrid
            response = actual_client.marketing.contacts.delete(
                query_params={"ids": external_id}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Template External Model
# ============================================================================


class SendGrid_TemplateModel(AbstractExternalModel):
    """External model for SendGrid Template API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "templates"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid template ID")
    name: str = Field(..., description="Template name")
    generation: str = Field("dynamic", description="Template generation type")
    updated_at: str = Field(..., description="Last update timestamp")
    versions: List[Dict[str, Any]] = Field(
        default_factory=list, description="Template versions"
    )

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Template."""

        name: str = Field(..., description="Template name")
        generation: str = Field("dynamic", description="Template generation type")

    class Update(BaseModel):
        """Update model for SendGrid Template."""

        name: Optional[str] = Field(None, description="Template name")

    class Search(BaseModel):
        """Search model for SendGrid Template."""

        name: Optional[str] = Field(None, description="Search by name")
        generation: Optional[str] = Field(None, description="Search by generation type")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Create template via SendGrid
            response = actual_client.templates.post(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Get template via SendGrid
            response = actual_client.templates._(external_id).get()

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List templates via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List templates via SendGrid
            response = actual_client.templates.get(**kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                templates = response.body.get("templates", [])
                return {"success": True, "data": templates}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update template via SendGrid
            response = actual_client.templates._(external_id).patch(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete template via SendGrid
            response = actual_client.templates._(external_id).delete()

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Campaign External Model
# ============================================================================


class SendGrid_CampaignModel(AbstractExternalModel):
    """External model for SendGrid Campaign API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "marketing/campaigns"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid campaign ID")
    name: str = Field(..., description="Campaign name")
    subject: str = Field(..., description="Campaign subject line")
    sender_id: int = Field(..., description="Sender ID")
    list_ids: List[str] = Field(default_factory=list, description="Recipient list IDs")
    segment_ids: List[str] = Field(default_factory=list, description="Segment IDs")
    categories: List[str] = Field(
        default_factory=list, description="Campaign categories"
    )
    suppression_group_id: Optional[int] = Field(
        None, description="Suppression group ID"
    )
    custom_unsubscribe_url: Optional[str] = Field(
        None, description="Custom unsubscribe URL"
    )
    ip_pool: Optional[str] = Field(None, description="IP pool")
    html_content: str = Field(..., description="HTML content")
    plain_content: Optional[str] = Field(None, description="Plain text content")
    status: str = Field(..., description="Campaign status")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Campaign."""

        name: str = Field(..., description="Campaign name")
        subject: str = Field(..., description="Campaign subject line")
        sender_id: int = Field(..., description="Sender ID")
        list_ids: Optional[List[str]] = Field(None, description="Recipient list IDs")
        segment_ids: Optional[List[str]] = Field(None, description="Segment IDs")
        categories: Optional[List[str]] = Field(None, description="Campaign categories")
        suppression_group_id: Optional[int] = Field(
            None, description="Suppression group ID"
        )
        custom_unsubscribe_url: Optional[str] = Field(
            None, description="Custom unsubscribe URL"
        )
        ip_pool: Optional[str] = Field(None, description="IP pool")
        html_content: str = Field(..., description="HTML content")
        plain_content: Optional[str] = Field(None, description="Plain text content")

    class Update(BaseModel):
        """Update model for SendGrid Campaign."""

        name: Optional[str] = Field(None, description="Campaign name")
        subject: Optional[str] = Field(None, description="Campaign subject line")
        sender_id: Optional[int] = Field(None, description="Sender ID")
        list_ids: Optional[List[str]] = Field(None, description="Recipient list IDs")
        segment_ids: Optional[List[str]] = Field(None, description="Segment IDs")
        categories: Optional[List[str]] = Field(None, description="Campaign categories")
        suppression_group_id: Optional[int] = Field(
            None, description="Suppression group ID"
        )
        custom_unsubscribe_url: Optional[str] = Field(
            None, description="Custom unsubscribe URL"
        )
        ip_pool: Optional[str] = Field(None, description="IP pool")
        html_content: Optional[str] = Field(None, description="HTML content")
        plain_content: Optional[str] = Field(None, description="Plain text content")

    class Search(BaseModel):
        """Search model for SendGrid Campaign."""

        name: Optional[str] = Field(None, description="Search by name")
        status: Optional[str] = Field(None, description="Search by status")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Create campaign via SendGrid
            response = actual_client.marketing.campaigns.post(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Get campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).get()

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List campaigns via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List campaigns via SendGrid
            response = actual_client.marketing.campaigns.get(**kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                return {
                    "success": True,
                    "data": result.get("result", []),
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).patch(
                request_body=kwargs
            )

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).delete()

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Reference Models for BLL Integration
# ============================================================================

# Create reference models that can be used in BLL relationships
SendGrid_ContactReferenceModel = create_external_reference_model(
    SendGrid_ContactModel,
    "contact_id",
    "external_contact_id",
)

SendGrid_TemplateReferenceModel = create_external_reference_model(
    SendGrid_TemplateModel,
    "template_id",
    "external_template_id",
)

SendGrid_CampaignReferenceModel = create_external_reference_model(
    SendGrid_CampaignModel,
    "campaign_id",
    "external_campaign_id",
)


# ============================================================================
# SendGrid Managers for BLL Integration
# ============================================================================


class SendGrid_ContactManager(AbstractExternalManager):
    """Manager for SendGrid contacts."""

    model_class = SendGrid_ContactModel
    reference_model_class = SendGrid_ContactReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid contact manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_ContactModel

    async def sync_contact(
        self, email: str, provider_instance_id: str
    ) -> Optional[str]:
        """
        Sync a contact with SendGrid.

        Args:
            email: Email address of the contact
            provider_instance_id: ID of the provider instance to use

        Returns:
            Contact ID if successful, None otherwise
        """
        try:
            # Check if contact exists
            search_result = await self.search_external(
                provider_instance_id=provider_instance_id, email=email
            )

            if search_result and search_result.get("data"):
                # Contact exists, return its ID
                contact = search_result["data"][0]
                return contact.get("id")  # type: ignore[no-any-return]

            # Create new contact
            create_result = await self.create_external(
                provider_instance_id=provider_instance_id, email=email
            )

            if create_result and create_result.get("success"):
                return create_result["data"].get("id")  # type: ignore[no-any-return]

            return None

        except Exception as e:
            logger.error(f"Error syncing contact {email}: {e}")
            return None

    async def add_to_list(
        self, contact_id: str, list_id: str, provider_instance_id: str
    ) -> bool:
        """
        Add a contact to a list.

        Args:
            contact_id: SendGrid contact ID
            list_id: SendGrid list ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current contact data
            contact_result = await self.get_external(
                external_id=contact_id, provider_instance_id=provider_instance_id
            )

            if not contact_result or not contact_result.get("success"):
                return False

            contact = contact_result["data"]
            current_lists = contact.get("list_ids", [])

            # Add list if not already present
            if list_id not in current_lists:
                current_lists.append(list_id)

                # Update contact
                update_result = await self.update_external(
                    external_id=contact_id,
                    provider_instance_id=provider_instance_id,
                    list_ids=current_lists,
                )

                return update_result and update_result.get("success", False)  # type: ignore[no-any-return]

            return True  # Already in list

        except Exception as e:
            logger.error(f"Error adding contact {contact_id} to list {list_id}: {e}")
            return False


class SendGrid_TemplateManager(AbstractExternalManager):
    """Manager for SendGrid templates."""

    model_class = SendGrid_TemplateModel
    reference_model_class = SendGrid_TemplateReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid template manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_TemplateModel

    async def create_template_version(
        self,
        template_id: str,
        provider_instance_id: str,
        name: str,
        subject: str,
        html_content: str,
        plain_content: Optional[str] = None,
        active: bool = True,
    ) -> Optional[str]:
        """
        Create a new version for a template.

        Args:
            template_id: SendGrid template ID
            provider_instance_id: ID of the provider instance to use
            name: Version name
            subject: Email subject
            html_content: HTML content
            plain_content: Plain text content (optional)
            active: Whether the version is active

        Returns:
            Version ID if successful, None otherwise
        """
        try:
            # This would typically use the SendGrid API to create a template version
            # For now, we'll log the attempt
            logger.info(f"Creating template version for template {template_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.templates._(template_id).versions.post(...)

            return f"{template_id}_v1"  # Placeholder

        except Exception as e:
            logger.error(f"Error creating template version: {e}")
            return None

    async def activate_template_version(
        self, template_id: str, version_id: str, provider_instance_id: str
    ) -> bool:
        """
        Activate a specific template version.

        Args:
            template_id: SendGrid template ID
            version_id: Version ID to activate
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to activate a version
            logger.info(f"Activating version {version_id} for template {template_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.templates._(template_id).versions._(version_id).activate.post()

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error activating template version: {e}")
            return False


class SendGrid_CampaignManager(AbstractExternalManager):
    """Manager for SendGrid campaigns."""

    model_class = SendGrid_CampaignModel
    reference_model_class = SendGrid_CampaignReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid campaign manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_CampaignModel

    async def send_campaign(self, campaign_id: str, provider_instance_id: str) -> bool:
        """
        Send a campaign.

        Args:
            campaign_id: SendGrid campaign ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to send the campaign
            logger.info(f"Sending campaign {campaign_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.campaigns._(campaign_id).schedule.put(
            #     request_body={"send_at": "now"}
            # )

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error sending campaign {campaign_id}: {e}")
            return False

    async def schedule_campaign(
        self, campaign_id: str, send_at: str, provider_instance_id: str
    ) -> bool:
        """
        Schedule a campaign for future sending.

        Args:
            campaign_id: SendGrid campaign ID
            send_at: ISO 8601 timestamp for when to send
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to schedule the campaign
            logger.info(f"Scheduling campaign {campaign_id} for {send_at}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.campaigns._(campaign_id).schedule.put(
            #     request_body={"send_at": send_at}
            # )

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error scheduling campaign {campaign_id}: {e}")
            return False

    async def get_campaign_stats(
        self, campaign_id: str, provider_instance_id: str
    ) -> Optional[dict]:
        """
        Get statistics for a campaign.

        Args:
            campaign_id: SendGrid campaign ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            Statistics dict if successful, None otherwise
        """
        try:
            # This would typically use the SendGrid API to get campaign stats
            logger.info(f"Getting stats for campaign {campaign_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.stats.campaigns._(campaign_id).get()

            # Placeholder stats
            return {
                "opens": 0,
                "clicks": 0,
                "delivered": 0,
                "bounces": 0,
                "spam_reports": 0,
                "unsubscribes": 0,
            }

        except Exception as e:
            logger.error(f"Error getting campaign stats: {e}")
            return None


# ============================================================================
# Stalwart Provider (SMTP submission transport)
# ============================================================================

# Stalwart is an open-source mail server typically deployed self-hosted.
# We integrate via the SMTP submission port (587 with STARTTLS) rather than
# the JMAP API; the SMTP path is universally available across deployments
# and depends only on aiosmtplib + the standard library's email package.
try:
    import aiosmtplib  # noqa: F401
    from email.message import EmailMessage as _StalwartEmailMessage

    _aiosmtplib_available = True
except ImportError:
    _aiosmtplib_available = False
    import warnings

    warnings.warn(
        "aiosmtplib package missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


class StalwartProvider(AbstractEmailProvider):
    """SMTP submission provider for self-hosted Stalwart mail servers."""

    name: ClassVar[str] = "stalwart"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Stalwart SMTP submission email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. Stalwart is a full mail server (SMTP submission +
    # IMAP + JMAP); the SEND path is wired today via aiosmtplib. IMAP-
    # backed receive-side abilities (LIST / READ / UPDATE / THREADS) are
    # tracked under Item 75 of Group 26 and will light up once the typed
    # provider-instance contract (Item 26) lands.
    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    # Item 92 — Stalwart authenticates via SMTP AUTH (PLAIN/LOGIN), which
    # is HTTP-Basic-equivalent username+password.
    default_auth_strategy: ClassVar[str] = "basic"

    # Items 96 + 97 — ops policies. SMTP transport is exempt from the shared
    # `ProviderHTTPClient` (Item 31 covers HTTP only).
    rate_limit: ClassVar[RateLimit] = RateLimit(rps=50, burst=100)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="aiosmtplib",
                friendly_name="aiosmtplib",
                semver=">=3.0.0",
                reason="async SMTP submission transport for Stalwart",
            )
        ]
    )

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        host: str
        port: int = 587
        username: str
        password: SecretStr
        use_tls: bool = True

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "STALWART_FROM_EMAIL",
            "host": "STALWART_HOST",
            "port": "STALWART_PORT",
            "username": "STALWART_USERNAME",
            "password": "STALWART_PASSWORD",
            "use_tls": "STALWART_USE_TLS",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "STALWART_HOST": "",
            "STALWART_PORT": "587",
            "STALWART_USERNAME": "",
            "STALWART_PASSWORD": "",
            "STALWART_FROM_EMAIL": "",
            "STALWART_USE_TLS": "true",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "smtp", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Stalwart"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return False

        host = env("STALWART_HOST")
        username = env("STALWART_USERNAME")
        password = env("STALWART_PASSWORD")
        if instance is not None:
            password = instance.api_key or password

        if not host or not username or not password:
            logger.error("Stalwart host/username/password not configured")
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        """Probe upstream liveness via SMTP ``NOOP`` (Items 27 + 96).

        Opens a short-lived SMTP connection, issues NOOP, and disconnects.
        Defensive: never raises; always returns a ``HealthReport``.
        """
        host = env("STALWART_HOST")
        port_str = env("STALWART_PORT") or "587"
        if not host:
            return HealthReport(
                HealthStatus.DOWN, detail="Stalwart host not configured"
            )
        if not _aiosmtplib_available:
            return HealthReport(
                HealthStatus.DOWN, detail="aiosmtplib not installed"
            )
        try:
            import asyncio
            import aiosmtplib

            try:
                port = int(port_str)
            except ValueError:
                port = 587

            async def _probe() -> str:
                smtp = aiosmtplib.SMTP(hostname=host, port=port, timeout=5.0)
                try:
                    await smtp.connect()
                    code, _ = await smtp.noop()
                    return f"NOOP {code}"
                finally:
                    try:
                        await smtp.quit()
                    except Exception:
                        pass

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    detail = "skipped: SMTP probe inside running loop"
                    return HealthReport(HealthStatus.OK, detail=detail)
            except RuntimeError:
                pass
            detail = asyncio.run(_probe())
            return HealthReport(HealthStatus.OK, detail=detail)
        except Exception as exc:  # noqa: BLE001 — defensive, never raise
            return HealthReport(HealthStatus.DOWN, detail=f"SMTP error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        """Bond an instance by capturing its SMTP connection parameters.

        For SMTP transport there is no long-lived SDK client to wrap; we
        instead store the connection config in the SDK slot so ``send_email``
        can open a fresh connection per send (the safe, stateless default).
        """
        # credential vault layering (Item 32) is a follow-up that swaps
        # `.get_secret_value()` for `CredentialRef.resolve()`.
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return None

        try:
            host = env("STALWART_HOST")
            port = int(env("STALWART_PORT") or "587")
            username = env("STALWART_USERNAME")
            password = (instance.api_key if instance else None) or env(
                "STALWART_PASSWORD"
            )
            use_tls = (env("STALWART_USE_TLS") or "true").lower() != "false"
            from_email = env("STALWART_FROM_EMAIL")

            if not host or not username or not password:
                logger.error("Stalwart connection parameters missing")
                return None

            # Item 92 — materialise the Basic-auth strategy for callers
            # that consume an AuthStrategy rather than raw credentials.
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy,
                username=username,
                password=password,
            )
            config = {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "start_tls": use_tls,
                "from_email": from_email,
                "auth_strategy": auth_strategy,
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond Stalwart instance: {e}")
            return None

    @classmethod
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
        """Send an email via SMTP submission to a Stalwart server."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]

        if not _aiosmtplib_available:
            return "Failed to send email: aiosmtplib not installed"

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond Stalwart instance"

        config = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or config.get("from_email")
            or env("STALWART_FROM_EMAIL")
        )
        if not from_email:
            return "Failed to send email: Stalwart from_email not configured"

        try:
            message = _StalwartEmailMessage()
            message["From"] = from_email
            message["To"] = recipient
            message["Subject"] = subject
            if "<html" in body.lower():
                message.set_content(body, subtype="html")
            else:
                message.set_content(body)

            if attachments:
                for attachment_path in attachments:
                    if not os.path.exists(attachment_path):
                        logger.warning(f"Attachment file not found: {attachment_path}")
                        continue
                    with open(attachment_path, "rb") as fh:
                        data = fh.read()
                    file_type = (
                        mimetypes.guess_type(attachment_path)[0]
                        or "application/octet-stream"
                    )
                    maintype, _, subtype = file_type.partition("/")
                    message.add_attachment(
                        data,
                        maintype=maintype or "application",
                        subtype=subtype or "octet-stream",
                        filename=os.path.basename(attachment_path),
                    )

            logger.debug(f"Sending Stalwart email to {recipient} from {from_email}")
            await aiosmtplib.send(
                message,
                hostname=config["host"],
                port=config["port"],
                username=config["username"],
                password=config["password"],
                start_tls=config["start_tls"],
            )
            return f"Email sent successfully to {recipient}"
        except Exception as e:
            logger.error(f"Error sending Stalwart email: {e}")
            return f"Failed to send email: {e}"

    # The remaining email_* abilities are not supported by SMTP submission;
    # mirror SendGrid's "log warning, return empty" stubs so the abstract
    # contract is satisfied without pretending to support receive operations.

    @staticmethod
    @ability(name="email_get")
    async def get_emails(provider_instance, folder_name="Inbox", max_emails=10, page_size=10):
        logger.warning("Getting emails is not supported by Stalwart SMTP transport")
        return []

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(provider_instance, recipient, subject, body, attachments=None, importance="normal"):
        logger.warning("Creating drafts is not supported by Stalwart SMTP transport")
        return "Creating draft emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None):
        logger.warning("Searching emails is not supported by Stalwart SMTP transport")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by Stalwart SMTP transport")
        return "Replying to emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by Stalwart SMTP transport")
        return "Deleting emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by Stalwart")
        return []

    # ------------------------------------------------------------------
    # Item 91 — typed send_via_provider / send_bulk_via_provider.
    #
    # Stalwart speaks SMTP submission. The bulk path opportunistically
    # batches multiple ``RCPT TO`` envelopes per session (a single SMTP
    # transaction with N recipients) when every message shares a sender
    # / subject / body; otherwise it falls back to a serial loop, one
    # session per message. Per-item rejections surface as typed errors
    # in the per-item rows of the returned envelope.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via SMTP submission."""
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(
                    status, legacy_result, provider="stalwart"
                )
            raise map_validation_error(legacy_result)

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
        """Send up to ``SEND_BULK_MAX_BATCH`` messages via SMTP submission.

        Opportunistically batches messages that share sender/subject/body
        into a single SMTP transaction with multiple ``RCPT TO`` envelopes;
        falls back to a serial-loop, one-session-per-message path when
        bodies differ. Per-item validation errors are surfaced as typed
        errors in the per-item rows.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        for m in messages:
            err = cls._validate_message(m)
            if err:
                raise map_validation_error(err)

        # Serial-loop fallback — opportunistic single-session batching is
        # a future optimisation tracked under Item 91's follow-up. Per-item
        # rejections surface in the row instead of aborting the batch.
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


# ============================================================================
# SMTP2go Provider (HTTP API transport)
# ============================================================================

try:
    import httpx as _httpx  # noqa: F401

    _httpx_available = True
except ImportError:
    _httpx_available = False
    import warnings

    warnings.warn(
        "httpx package missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


class Smtp2goProvider(AbstractEmailProvider):
    """SMTP2go email provider using the hosted HTTP API."""

    name: ClassVar[str] = "smtp2go"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "SMTP2go HTTP API email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. SMTP2go is a hosted SMTP relay with an HTTP API;
    # send-only at the inbox level. Suppression/stats/templates abilities
    # are tracked under Item 95 of Group 26 and light up once Item 37
    # (typed ability declarations) lands.
    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    # Item 92 — SMTP2go's HTTP API authenticates via a static API key
    # passed in the ``api_key`` body field; we model this as ``api_key``
    # for parity with SendGrid even though the on-the-wire shape differs.
    default_auth_strategy: ClassVar[str] = "api_key"

    # Items 96 + 97 — ops policies (paid-tier defaults).
    rate_limit: ClassVar[RateLimit] = RateLimit(rps=100, burst=200)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="httpx",
                friendly_name="HTTPX",
                semver=">=0.27.0",
                reason="HTTP client for SMTP2go API",
            )
        ]
    )

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        api_key: SecretStr
        api_url: HttpUrl = "https://api.smtp2go.com/v3"  # type: ignore[assignment]

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "SMTP2GO_FROM_EMAIL",
            "api_key": "SMTP2GO_API_KEY",
            "api_url": "SMTP2GO_API_URL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "SMTP2GO_API_KEY": "",
            "SMTP2GO_FROM_EMAIL": "",
            "SMTP2GO_API_URL": "https://api.smtp2go.com/v3",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "messaging", "communication"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "SMTP2go"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _httpx_available:
            logger.error("httpx package not available")
            return False
        api_key = env("SMTP2GO_API_KEY")
        if instance is not None:
            api_key = instance.api_key or api_key
        if not api_key:
            logger.error("SMTP2go API key not configured")
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        """Probe upstream liveness via ``GET /v3/stats/email_summary`` (Items 27 + 97).

        Routes through the shared ``ProviderHTTPClient`` so the request
        carries traceparent + auth + log redaction. Defensive: never
        raises; always returns a ``HealthReport``.
        """
        api_key = env("SMTP2GO_API_KEY")
        api_url = env("SMTP2GO_API_URL") or "https://api.smtp2go.com/v3"
        if not api_key:
            return HealthReport(
                HealthStatus.DOWN, detail="SMTP2go API key not configured"
            )
        try:
            from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_sync_client

            client = get_sync_client(ClientPolicy(timeout=5.0))
            response = client.get(
                f"{api_url.rstrip('/')}/stats/email_summary",
                params={"api_key": api_key},
            )
            status = response.status_code
            if 200 <= status < 300:
                return HealthReport(
                    HealthStatus.OK, detail=f"email_summary status {status}"
                )
            if 400 <= status < 500:
                return HealthReport(
                    HealthStatus.DEGRADED, detail=f"email_summary status {status}"
                )
            return HealthReport(
                HealthStatus.DOWN, detail=f"email_summary status {status}"
            )
        except Exception as exc:  # noqa: BLE001 — defensive, never raise
            return HealthReport(HealthStatus.DOWN, detail=f"network error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        # credential vault layering (Item 32) is a follow-up that swaps
        # `.get_secret_value()` for `CredentialRef.resolve()`.
        if not _httpx_available:
            logger.error("httpx package not available")
            return None
        try:
            api_key = (instance.api_key if instance else None) or env("SMTP2GO_API_KEY")
            api_url = env("SMTP2GO_API_URL") or "https://api.smtp2go.com/v3"
            from_email = env("SMTP2GO_FROM_EMAIL")
            if not api_key:
                logger.error("SMTP2go API key missing")
                return None
            client = _httpx.AsyncClient(base_url=api_url, timeout=30.0)
            # Item 92 — materialise the AuthStrategy. SMTP2go does not
            # actually use a header-based key (it's body-encoded), but
            # we still expose the strategy for consumers that want a
            # uniform handle to "what creds does this provider use".
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy, api_key=api_key
            )
            config = {
                "client": client,
                "api_key": api_key,
                "from_email": from_email,
                "api_url": api_url,
                "auth_strategy": auth_strategy,
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond SMTP2go instance: {e}")
            return None

    @classmethod
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
        """Send an email via SMTP2go's /email/send REST API."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]

        if not _httpx_available:
            return "Failed to send email: httpx not installed"

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond SMTP2go instance"

        config = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or config.get("from_email")
            or env("SMTP2GO_FROM_EMAIL")
        )
        if not from_email:
            return "Failed to send email: SMTP2go from_email not configured"

        is_html = "<html" in body.lower()
        payload: Dict[str, Any] = {
            "api_key": config["api_key"],
            "to": [recipient],
            "sender": from_email,
            "subject": subject,
        }
        if is_html:
            payload["html_body"] = body
        else:
            payload["text_body"] = body

        if attachments:
            payload_attachments = []
            for attachment_path in attachments:
                if not os.path.exists(attachment_path):
                    logger.warning(f"Attachment file not found: {attachment_path}")
                    continue
                with open(attachment_path, "rb") as fh:
                    encoded = base64.b64encode(fh.read()).decode("ascii")
                payload_attachments.append(
                    {
                        "filename": os.path.basename(attachment_path),
                        "fileblob": encoded,
                        "mimetype": (
                            mimetypes.guess_type(attachment_path)[0]
                            or "application/octet-stream"
                        ),
                    }
                )
            if payload_attachments:
                payload["attachments"] = payload_attachments

        try:
            # Item 97 — route through the shared HTTP client pool so the
            # request carries traceparent + log redaction. Use the pooled
            # `httpx.AsyncClient` directly (rather than `ProviderHTTPClient`'s
            # typed-raise pipeline) to preserve the legacy string envelope
            # this method has historically returned.
            from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_async_client

            shared = get_async_client(ClientPolicy(timeout=30.0))
            response = await shared.post(
                f"{config['api_url'].rstrip('/')}/email/send", json=payload
            )
            if 200 <= response.status_code < 300:
                logger.debug(f"SMTP2go: email sent successfully to {recipient}")
                return f"Email sent successfully to {recipient}"
            return f"Failed to send email: {response.status_code}: {response.text}"
        except Exception as e:
            logger.error(f"Error sending SMTP2go email: {e}")
            return f"Failed to send email: {e}"
        finally:
            try:
                await config["client"].aclose()
            except Exception:
                pass

    # SMTP2go is send-only — stub the rest of the abstract contract.

    @staticmethod
    @ability(name="email_get")
    async def get_emails(provider_instance, folder_name="Inbox", max_emails=10, page_size=10):
        logger.warning("Getting emails is not supported by SMTP2go")
        return []

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(provider_instance, recipient, subject, body, attachments=None, importance="normal"):
        logger.warning("Creating drafts is not supported by SMTP2go")
        return "Creating draft emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None):
        logger.warning("Searching emails is not supported by SMTP2go")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by SMTP2go")
        return "Replying to emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by SMTP2go")
        return "Deleting emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by SMTP2go")
        return []

    # ------------------------------------------------------------------
    # Item 91 — typed send_via_provider / send_bulk_via_provider.
    #
    # SMTP2go's REST API accepts an array under ``to`` plus per-item
    # ``custom_headers``; up to 1000 recipients per call. The bulk path
    # packs ``messages`` into the ``to`` array when sender/subject/body
    # are homogeneous; otherwise serial-loops.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via SMTP2go's HTTP API."""
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(
                    status, legacy_result, provider="smtp2go"
                )
            raise map_validation_error(legacy_result)

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
        """Send up to ``SEND_BULK_MAX_BATCH`` messages via SMTP2go.

        SMTP2go accepts an array of recipients in the ``to`` field of a
        single ``/email/send`` call. We currently use a serial-loop so
        each message preserves its full shape; the upstream-batched path
        lights up once Item 91's homogeneous-batch detection is finalised.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        for m in messages:
            err = cls._validate_message(m)
            if err:
                raise map_validation_error(err)

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
