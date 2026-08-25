# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stalwart email provider — SMTP submission for self-hosted Stalwart servers.

Extracted from ``PRV_SendGrid_EMail`` into its own provider module. Stalwart is
an open-source mail server typically deployed self-hosted; we integrate via the
SMTP submission port (587 with STARTTLS) rather than the JMAP API, so this
depends only on ``aiosmtplib`` + the standard-library ``email`` package.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
from datetime import datetime
from decimal import Decimal
from email.utils import formataddr, parseaddr
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Type

from pydantic import EmailStr, SecretStr

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.AbstractExternalModel import idempotent
from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.extensions.email.EmailErrors import (
    EmailValidationError,
    extract_status_code as _extract_status_code,
    map_upstream_status,
    map_validation_error,
)
from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EmailDeliveryEvent,
    EmailMessage,
    Importance,
    dispatch_email_delivery_event,
    _DeprecatedEnvDict,
)
from zephyrex.extensions.ExternalErrors import DegradationPolicy, fail_fast
from zephyrex.extensions.FieldMappings import (
    Compose,
    EnumRemap,
    FieldMapping,
    Rename,
)
from zephyrex.extensions.Paginators import AbstractPaginator, PageTokenPaginator
from zephyrex.extensions.QueryTranslators import (
    AbstractQueryDSLTranslator,
    IMAPSearchTranslator,
)
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def _build_auth_strategy(strategy_name: str, **kwargs):
    """Build an AuthStrategy, importing the shared factory lazily.

    Imported lazily (not at module top) so this provider module never depends
    on ``PRV_SendGrid_EMail`` at import time — a top-level dependency there let
    provider discovery observe a partially-imported SendGrid module and drop it
    from the cached provider set.
    """
    try:
        from zephyrex.extensions.email.PRV_SendGrid_EMail import (
            _build_auth_strategy as _factory,
        )

        return _factory(strategy_name, **kwargs)
    except Exception:  # pragma: no cover - defensive
        return None


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

    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    default_auth_strategy: ClassVar[str] = "basic"

    rate_limit: ClassVar[RateLimit] = RateLimit(rps=50, burst=100)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    # Item 93 — federation surface. Stalwart's message search is IMAP
    # ``SEARCH`` (RFC 3501); results page via an opaque next-token cursor.
    paginator: ClassVar[Type[AbstractPaginator]] = PageTokenPaginator
    query_translator: ClassVar[Type[AbstractQueryDSLTranslator]] = IMAPSearchTranslator

    # Item 93 — declarative EmailMessage <-> RFC-5322 message mappings for the
    # SMTP submission path. ``EmailAddress`` <-> ``From`` mailbox is a
    # ``Compose``; ``Importance`` <-> the ``X-Priority`` numeric header is an
    # ``EnumRemap``.
    field_mappings: ClassVar[List[FieldMapping]] = [
        Rename(internal="subject", external="subject"),
        Compose(
            externals=["from_address", "from_name"],
            internal="from",
            fn=lambda addr, name: formataddr((name or "", addr)),
            inverse_fn=lambda mailbox: (
                parseaddr(mailbox)[1],
                parseaddr(mailbox)[0] or None,
            ),
        ),
        EnumRemap(
            internal="importance",
            external="x_priority",
            mapping={
                Importance.HIGH.value: "1",
                Importance.NORMAL.value: "3",
                Importance.LOW.value: "5",
            },
        ),
    ]

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
        """Probe upstream liveness via SMTP ``NOOP``."""
        host = env("STALWART_HOST")
        port_str = env("STALWART_PORT") or "587"
        if not host:
            return HealthReport(
                HealthStatus.DOWN, detail="Stalwart host not configured"
            )
        if not _aiosmtplib_available:
            return HealthReport(HealthStatus.DOWN, detail="aiosmtplib not installed")
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
        """Bond an instance by capturing its SMTP connection parameters."""
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

    # Item 94 — inbound webhook (inbound-mail) verification. Stalwart signs each
    # callback with HMAC-SHA256 over the raw body.
    STALWART_WEBHOOK_SECRET_ENV: ClassVar[str] = "STALWART_WEBHOOK_SECRET"
    STALWART_SIGNATURE_HEADER: ClassVar[str] = "x-stalwart-signature"

    @classmethod
    def verify_signature(cls, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify a Stalwart webhook via HMAC-SHA256 over the raw body.

        Stalwart signs each callback with HMAC-SHA256 of the raw request body
        keyed by ``STALWART_WEBHOOK_SECRET``, presented hex-encoded in the
        ``X-Stalwart-Signature`` header (an optional ``sha256=`` prefix is
        tolerated). Compared in constant time. Returns False on missing
        secret/header or mismatch; never raises.
        """
        secret = env(cls.STALWART_WEBHOOK_SECRET_ENV)
        if not secret:
            logger.warning(
                f"Stalwart webhook verification: {cls.STALWART_WEBHOOK_SECRET_ENV} "
                "not set; rejecting."
            )
            return False
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        provided = normalized.get(cls.STALWART_SIGNATURE_HEADER, "") or ""
        if not provided:
            logger.debug("Stalwart webhook missing signature header; rejecting.")
            return False
        if provided.lower().startswith("sha256="):
            provided = provided[7:]
        expected = hmac.new(
            secret.encode("utf-8"), body or b"", hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, provided)

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
            # Item 97 — Stalwart submits over SMTP (aiosmtplib), not HTTP, so the
            # ProviderHTTPClient wrapper does not apply. Acquire from the
            # persistent per-class TokenBucket for the same rate-limit throttling
            # the HTTP providers get on send (parity with the shared client's
            # limiter).
            bucket = cls._send_rate_bucket()
            if bucket is not None:
                bucket.acquire_blocking(timeout=30.0)
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

    @staticmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance, folder_name="Inbox", max_emails=10, page_size=10
    ):
        logger.warning("Getting emails is not supported by Stalwart SMTP transport")
        return []

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(
        provider_instance,
        recipient,
        subject,
        body,
        attachments=None,
        importance="normal",
    ):
        logger.warning("Creating drafts is not supported by Stalwart SMTP transport")
        return "Creating draft emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None
    ):
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
                raise map_upstream_status(status, legacy_result, provider="stalwart")
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
        """Send up to ``SEND_BULK_MAX_BATCH`` messages via SMTP submission."""
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


# ============================================================================
# Item 94 — inbound webhook handlers (Stalwart inbound-mail + delivery events).
# On dispatch the handler normalises the payload into a canonical
# `EmailDeliveryEvent` and fans it through `dispatch_email_delivery_event`.
# ============================================================================


def _ts_to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _EmailExtensionStub:
    """Pass-through stub for ``webhook_handler``'s ``extension_class``; pins the
    extension name to ``"email"`` so registration doesn't depend on
    ``EXT_EMail``'s module-load order (mirrors ``PRV_SendGrid_EMail``)."""

    extension_name = "email"


def _coerce_stalwart_event(raw: Dict[str, Any], event_type: str) -> EmailDeliveryEvent:
    """Translate one Stalwart hook row into an ``EmailDeliveryEvent``."""
    return EmailDeliveryEvent(
        message_id=str(
            raw.get("message-id", "")
            or raw.get("message_id", "")
            or raw.get("queue-id", "")
            or ""
        ),
        provider="stalwart",
        event_type=event_type,
        recipient=raw.get("rcpt", "") or raw.get("to", "") or "",
        timestamp=_ts_to_float(raw.get("ts") or raw.get("timestamp")),
        raw=raw,
    )


async def _dispatch_stalwart_events(payload: Any, fallback_event: str) -> None:
    """Fan a Stalwart webhook payload (list, single dict, or ``{"events": [...]}``
    envelope) through the canonical hook bus."""
    rows: List[Dict[str, Any]]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = (
            payload["events"] if isinstance(payload.get("events"), list) else [payload]
        )
    else:
        return
    for row in rows:
        event_type = row.get("event") or row.get("type") or fallback_event
        await dispatch_email_delivery_event(_coerce_stalwart_event(row, event_type))


def _register_stalwart_webhook_handlers() -> None:
    """Register Stalwart inbound-mail + delivery webhook handlers + wire the
    verifier. Idempotent; a no-op if the optional ``webhooks`` extension isn't
    loaded."""
    try:
        from zephyrex.extensions.webhooks import WebhookContext, webhook_handler
        from zephyrex.extensions.webhooks.BLL_Webhooks import _PROVIDER_CLASSES
    except ImportError:
        return

    for event_name in ("inbound", "delivered", "bounce"):

        @webhook_handler(_EmailExtensionStub, provider="stalwart", event=event_name)
        async def _handler(ctx: "WebhookContext", _evt: str = event_name) -> None:
            await _dispatch_stalwart_events(ctx.payload, _evt)

    # Wire Stalwart as the verifier for `(email, stalwart)` so the webhook router
    # calls `StalwartProvider.verify_signature` at dispatch time.
    _PROVIDER_CLASSES[("email", "stalwart")] = StalwartProvider


_register_stalwart_webhook_handlers()
