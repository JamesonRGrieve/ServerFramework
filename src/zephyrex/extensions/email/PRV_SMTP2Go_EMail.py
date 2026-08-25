# SPDX-License-Identifier: AGPL-3.0-or-later
"""SMTP2Go email provider — send-only HTTP API relay."""

from __future__ import annotations

import base64
import hmac
import mimetypes
import os
from datetime import datetime
from decimal import Decimal
from email.utils import formataddr, parseaddr
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Type

from pydantic import EmailStr, HttpUrl, SecretStr

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
    KeyValueTranslator,
)
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:
    import httpx as _httpx

    _httpx_available = True
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _httpx_available = False

try:
    from zephyrex.extensions.email.PRV_SendGrid_EMail import (
        _build_auth_strategy,
        _extract_status_code,
    )
except ImportError:

    def _build_auth_strategy(strategy: str, **kwargs):  # type: ignore[misc]
        return None

    def _extract_status_code(msg: str) -> Optional[int]:  # type: ignore[misc]
        import re

        m = re.search(r"\b(\d{3})\b", msg)
        return int(m.group(1)) if m else None


class Smtp2goProvider(AbstractEmailProvider):
    """SMTP2go email provider using the hosted HTTP API."""

    name: ClassVar[str] = "smtp2go"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "SMTP2go HTTP API email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    default_auth_strategy: ClassVar[str] = "api_key"

    rate_limit: ClassVar[RateLimit] = RateLimit(rps=100, burst=200)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    # Item 93 — federation surface. SMTP2go's messages/activity search is a
    # flat ``?field=value`` key/value surface; results page via an opaque
    # next-token cursor.
    paginator: ClassVar[Type[AbstractPaginator]] = PageTokenPaginator
    query_translator: ClassVar[Type[AbstractQueryDSLTranslator]] = KeyValueTranslator

    # Item 93 — declarative EmailMessage <-> SMTP2go send-payload mappings.
    # SMTP2go's HTTP API takes a flat JSON body: ``sender`` (an RFC-5322 mailbox
    # string), ``subject``, ``text_body``/``html_body``. ``EmailAddress`` <->
    # mailbox is a ``Compose``; ``Importance`` <-> the numeric X-Priority value
    # is an ``EnumRemap``.
    field_mappings: ClassVar[List[FieldMapping]] = [
        Rename(internal="subject", external="subject"),
        Rename(internal="body_text", external="text_body"),
        Rename(internal="body_html", external="html_body"),
        Compose(
            externals=["from_address", "from_name"],
            internal="sender",
            fn=lambda addr, name: formataddr((name or "", addr)),
            inverse_fn=lambda mailbox: (
                parseaddr(mailbox)[1],
                parseaddr(mailbox)[0] or None,
            ),
        ),
        EnumRemap(
            internal="importance",
            external="priority",
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
        api_key = env("SMTP2GO_API_KEY")
        api_url = env("SMTP2GO_API_URL") or "https://api.smtp2go.com/v3"
        if not api_key:
            return HealthReport(
                HealthStatus.DOWN, detail="SMTP2go API key not configured"
            )
        try:
            from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_sync_client

            client = get_sync_client(ClientPolicy(timeout=5.0))
            response = client.post(
                f"{api_url.rstrip('/')}/stats/email_summary",
                json={"api_key": api_key},
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
        except Exception as exc:
            return HealthReport(HealthStatus.DOWN, detail=f"network error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
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

    # Item 94 — inbound webhook (bounce-activity) verification. SMTP2go
    # authenticates its callbacks with a bearer token.
    SMTP2GO_WEBHOOK_SECRET_ENV: ClassVar[str] = "SMTP2GO_WEBHOOK_SECRET"

    @classmethod
    def verify_signature(cls, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify an SMTP2go webhook via a bearer-token check.

        SMTP2go authenticates its webhook callbacks with a bearer token in the
        ``Authorization`` header; we compare it in constant time against
        ``SMTP2GO_WEBHOOK_SECRET``. Returns False on missing secret/header or
        mismatch; never raises.
        """
        secret = env(cls.SMTP2GO_WEBHOOK_SECRET_ENV)
        if not secret:
            logger.warning(
                f"SMTP2go webhook verification: {cls.SMTP2GO_WEBHOOK_SECRET_ENV} "
                "not set; rejecting."
            )
            return False
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        auth = normalized.get("authorization", "") or ""
        token = auth[7:] if auth.lower().startswith("bearer ") else auth
        if not token:
            logger.debug("SMTP2go webhook missing bearer token; rejecting.")
            return False
        return hmac.compare_digest(token, secret)

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

    @staticmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance, folder_name="Inbox", max_emails=10, page_size=10
    ):
        logger.warning("Getting emails is not supported by SMTP2go")
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
        logger.warning("Creating drafts is not supported by SMTP2go")
        return "Creating draft emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None
    ):
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

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(status, legacy_result, provider="smtp2go")
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
            except Exception as exc:
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
# Item 94 — inbound webhook handlers (SMTP2go bounce activity).
# Each event type registers via `@webhook_handler(..., provider="smtp2go", ...)`;
# on dispatch the handler normalises the payload into a canonical
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


def _coerce_smtp2go_event(raw: Dict[str, Any], event_type: str) -> EmailDeliveryEvent:
    """Translate one SMTP2go bounce-activity row into an ``EmailDeliveryEvent``."""
    return EmailDeliveryEvent(
        message_id=str(raw.get("message-id", "") or raw.get("message_id", "") or ""),
        provider="smtp2go",
        event_type=event_type,
        recipient=raw.get("rcpt", "") or raw.get("email", "") or "",
        timestamp=_ts_to_float(raw.get("ts") or raw.get("timestamp")),
        raw=raw,
    )


async def _dispatch_smtp2go_events(payload: Any, fallback_event: str) -> None:
    """Fan an SMTP2go webhook payload (list, single dict, or ``{"data": [...]}``
    envelope) through the canonical hook bus."""
    rows: List[Dict[str, Any]]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload["data"] if isinstance(payload.get("data"), list) else [payload]
    else:
        return
    for row in rows:
        event_type = row.get("event") or row.get("type") or fallback_event
        await dispatch_email_delivery_event(_coerce_smtp2go_event(row, event_type))


def _register_smtp2go_webhook_handlers() -> None:
    """Register SMTP2go bounce-activity webhook handlers + wire the verifier.

    Idempotent; a no-op if the optional ``webhooks`` extension isn't loaded.
    """
    try:
        from zephyrex.extensions.webhooks import WebhookContext, webhook_handler
        from zephyrex.extensions.webhooks.BLL_Webhooks import _PROVIDER_CLASSES
    except ImportError:
        return

    for event_name in ("bounce", "spam", "unsubscribe"):

        @webhook_handler(_EmailExtensionStub, provider="smtp2go", event=event_name)
        async def _handler(ctx: "WebhookContext", _evt: str = event_name) -> None:
            await _dispatch_smtp2go_events(ctx.payload, _evt)

    # Wire SMTP2go as the verifier for `(email, smtp2go)` so the webhook router
    # calls `Smtp2goProvider.verify_signature` at dispatch time.
    _PROVIDER_CLASSES[("email", "smtp2go")] = Smtp2goProvider


_register_smtp2go_webhook_handlers()
