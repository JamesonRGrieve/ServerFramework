# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proxmox Mail Gateway (PMG) email provider.

PMG is an open-source mail *gateway* (Postfix relay + spam/virus filtering +
quarantine) rather than a mailbox ESP, so this provider integrates two surfaces:

* **Send** — outbound mail is submitted through PMG's SMTP relay (aiosmtplib,
  like the Stalwart provider), throttled by the shared per-class TokenBucket.
* **Management** — the PMG REST API (``https://<host>:8006/api2/json``,
  authenticated with a ``PMGAPIToken``) for liveness (``/version``), mail
  statistics (``/statistics/mail``), message tracking (``/nodes/<node>/tracker``)
  and quarantine control (list/release/delete on ``/quarantine/{spam,virus,
  attachment}`` + ``/quarantine/content``).

The API calls route through the shared ``ProviderHTTPClient`` so they carry the
SSRF guard, TLS policy, trace propagation and log redaction the rest of the
email surface uses.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
from decimal import Decimal
from email.utils import formataddr, parseaddr
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Tuple, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    HealthReport,
    HealthStatus,
    InstanceSetting,
    ability,
)
from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.extensions.email.EXT_EMail import (
    FROM_EMAIL_SETTING,
    AbstractEmailProvider,
    Capability,
    EmailDeliveryEvent,
    Importance,
    dispatch_email_delivery_event,
    from_email_setting,
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
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, get_async_client
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:
    import aiosmtplib  # noqa: F401
    from email.message import EmailMessage as _PMGEmailMessage

    _aiosmtplib_available = True
except ImportError:
    _aiosmtplib_available = False


def _build_auth_strategy(strategy_name: str, **kwargs: Any) -> Any:
    """Materialise the declared AuthStrategy via the shared factory, imported
    lazily so this module never depends on ``PRV_SendGrid_EMail`` at import
    time (which perturbs provider discovery)."""
    try:
        from zephyrex.extensions.email.PRV_SendGrid_EMail import (
            _build_auth_strategy as _factory,
        )

        return _factory(strategy_name, **kwargs)
    except Exception:  # pragma: no cover - defensive
        return None


class ProxmoxMailGatewayProvider(AbstractEmailProvider):
    """Send via the PMG SMTP relay; manage via the PMG REST API."""

    name: ClassVar[str] = "proxmox_mail_gateway"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Proxmox Mail Gateway (SMTP relay + REST API)"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    capabilities: ClassVar = frozenset(
        {
            Capability.SEND,
            Capability.ATTACHMENTS,
            Capability.STATS,
            Capability.MESSAGES,
            Capability.INBOUND_WEBHOOK,
        }
    )

    default_auth_strategy: ClassVar[str] = "api_key"
    rate_limit: ClassVar[RateLimit] = RateLimit(rps=20, burst=40)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    # Item 93 — federation surface. PMG's tracker / statistics search is a flat
    # key/value query; results page via an opaque next-token cursor.
    paginator: ClassVar[Type[AbstractPaginator]] = PageTokenPaginator
    query_translator: ClassVar[Type[AbstractQueryDSLTranslator]] = KeyValueTranslator
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
                reason="async SMTP submission transport for the PMG relay",
            )
        ]
    )

    # Item 94 — PMG can POST notification callbacks; verify them with HMAC-SHA256
    # over the raw body keyed by PMG_WEBHOOK_SECRET (X-PMG-Signature).
    PMG_WEBHOOK_SECRET_ENV: ClassVar[str] = "PMG_WEBHOOK_SECRET"
    PMG_SIGNATURE_HEADER: ClassVar[str] = "x-pmg-signature"

    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *AbstractEmailProvider.instance_settings,
        from_email_setting("PMG_FROM_EMAIL"),
        InstanceSetting("smtp_host", "The relay's SMTP host", env="PMG_SMTP_HOST"),
        InstanceSetting(
            "smtp_port", "The relay's SMTP port", env="PMG_SMTP_PORT", default="587"
        ),
        InstanceSetting(
            "smtp_username",
            "The SMTP AUTH user, when the relay asks for one",
            env="PMG_SMTP_USERNAME",
        ),
        InstanceSetting(
            "smtp_password",
            "The SMTP AUTH password",
            env="PMG_SMTP_PASSWORD",
            secret=True,
        ),
        InstanceSetting(
            "use_tls",
            "STARTTLS to the relay; false only on a trusted network",
            env="PMG_SMTP_USE_TLS",
            default="true",
        ),
        InstanceSetting(
            "api_url",
            "The REST API's address (https://<host>:8006/api2/json)",
            env="PMG_API_URL",
        ),
        InstanceSetting(
            "api_token",
            "A PMGAPIToken value (user@realm!tokenid=secret)",
            env="PMG_API_TOKEN",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "api_node",
            "The node whose message tracker is read",
            env="PMG_API_NODE",
            default="localhost",
        ),
        InstanceSetting(
            "api_tls_verify",
            "Verify the API's certificate; false only for a self-signed one "
            "on a trusted network",
            env="PMG_API_TLS_VERIFY",
            default="true",
        ),
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "smtp", "gateway", "quarantine"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Proxmox Mail Gateway"

    # ------------------------------------------------------------------ config
    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return False
        if not cls.setting(instance, "smtp_host"):
            logger.error("PMG SMTP host not configured")
            return False
        return True

    @classmethod
    def _api_base(cls, instance: Optional[ProviderInstanceModel]) -> str:
        return cls.destination(
            instance, (cls.setting(instance, "api_url") or "").rstrip("/")
        )

    @classmethod
    def _api_headers(cls, instance: Optional[ProviderInstanceModel]) -> Dict[str, str]:
        token = cls.setting(instance, "api_token")
        return {"Authorization": f"PMGAPIToken={token}"} if token else {}

    @classmethod
    def _api_client(cls, instance: Optional[ProviderInstanceModel]) -> Any:
        verify = cls.setting_flag(instance, "api_tls_verify")
        return get_async_client(ClientPolicy(timeout=15.0, tls_verify=verify))

    @classmethod
    def _api_url(cls, instance: Optional[ProviderInstanceModel], path: str) -> str:
        base = cls._api_base(instance)
        if not base:
            raise RuntimeError("PMG api_url not configured")
        return f"{base}/{path.lstrip('/')}"

    @staticmethod
    def _api_data(response: Any) -> Any:
        response.raise_for_status()
        body = response.json()
        # PMG wraps payloads in a top-level {"data": ...} envelope.
        return body.get("data", body) if isinstance(body, dict) else body

    @classmethod
    async def _api_get(
        cls,
        instance: Optional[ProviderInstanceModel],
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        response = await cls._api_client(instance).get(
            cls._api_url(instance, path),
            headers=cls._api_headers(instance),
            params=params,
        )
        return cls._api_data(response)

    @classmethod
    async def _api_post(
        cls,
        instance: Optional[ProviderInstanceModel],
        path: str,
        data: Dict[str, Any],
    ) -> Any:
        response = await cls._api_client(instance).post(
            cls._api_url(instance, path),
            headers=cls._api_headers(instance),
            data=data,
        )
        return cls._api_data(response)

    # --------------------------------------------------------------- lifecycle
    @classmethod
    def bond_instance(
        cls, instance: Optional[ProviderInstanceModel]
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return None
        try:
            api_token = cls.setting(instance, "api_token")
            host = cls.setting(instance, "smtp_host")
            port = cls.setting_port(instance, "smtp_port")
            if host:
                cls.mail_server(instance, host, port)
            config = {
                "host": host,
                "port": port,
                "username": cls.setting(instance, "smtp_username"),
                "password": cls.setting(instance, "smtp_password"),
                "start_tls": cls.setting_flag(instance, "use_tls"),
                "from_email": cls.setting(instance, FROM_EMAIL_SETTING),
                "api_url": cls._api_base(instance),
                "api_token": api_token,
                "auth_strategy": _build_auth_strategy(
                    cls.default_auth_strategy, api_key=api_token
                ),
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond Proxmox Mail Gateway instance: {e}")
            return None

    @classmethod
    def health_check(cls) -> HealthReport:
        """Probe PMG liveness via ``GET /version`` on the REST API."""
        if not cls._api_base(None):
            return HealthReport(HealthStatus.DOWN, detail="PMG api_url not configured")
        try:
            import asyncio

            async def _probe() -> str:
                data = await cls._api_get(None, "version")
                return (
                    f"version {data.get('version', '?')}"
                    if isinstance(data, dict)
                    else "ok"
                )

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return HealthReport(
                        HealthStatus.OK, detail="skipped: probe inside running loop"
                    )
            except RuntimeError:
                pass
            return HealthReport(HealthStatus.OK, detail=asyncio.run(_probe()))
        except Exception as exc:  # noqa: BLE001 — defensive, never raise
            return HealthReport(HealthStatus.DOWN, detail=f"PMG API error: {exc}")

    # -------------------------------------------------------------------- send
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
        """Send an email through the PMG SMTP relay."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to bond Proxmox Mail Gateway instance"
        config = bonded.sdk
        from_email = config.get("from_email")
        if not from_email:
            return "Failed to send email: PMG from_email not configured"
        if not config.get("host"):
            return "Failed to send email: PMG SMTP host not configured"
        try:
            message = _PMGEmailMessage()
            message["From"] = from_email
            message["To"] = recipient
            message["Subject"] = subject
            if "<html" in body.lower():
                message.set_content(body, subtype="html")
            else:
                message.set_content(body)

            for attachment_path in attachments or []:
                if not os.path.exists(attachment_path):
                    logger.warning(f"PMG attachment not found: {attachment_path}")
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

            logger.debug(f"PMG: sending email to {recipient} from {from_email}")
            # Item 97 — rate-limit the SMTP submission via the persistent
            # per-class TokenBucket (SMTP, so the HTTP wrapper does not apply).
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
            logger.error(f"Error sending PMG email: {e}")
            return f"Failed to send email: {e}"

    # -------------------------------------------------------------- management
    @classmethod
    async def get_stats(
        cls,
        provider_instance: Optional[ProviderInstanceModel] = None,
        starttime: Optional[int] = None,
        endtime: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Mail statistics via ``GET /statistics/mail`` (counts, traffic,
        spam/virus). ``starttime``/``endtime`` are UNIX epoch seconds."""
        params: Dict[str, Any] = {}
        if starttime is not None:
            params["starttime"] = starttime
        if endtime is not None:
            params["endtime"] = endtime
        data = await cls._api_get(provider_instance, "statistics/mail", params or None)
        return data if isinstance(data, dict) else {"data": data}

    @classmethod
    async def list_messages(
        cls,
        provider_instance: Optional[ProviderInstanceModel] = None,
        starttime: Optional[int] = None,
        endtime: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Message tracking via ``GET /nodes/<node>/tracker`` — the PMG
        equivalent of a message list."""
        node = cls.setting(provider_instance, "api_node")
        params: Dict[str, Any] = {}
        if starttime is not None:
            params["starttime"] = starttime
        if endtime is not None:
            params["endtime"] = endtime
        data = await cls._api_get(
            provider_instance, f"nodes/{node}/tracker", params or None
        )
        return list(data) if isinstance(data, list) else []

    @classmethod
    async def list_quarantine(
        cls,
        kind: str = "spam",
        starttime: Optional[int] = None,
        *,
        provider_instance: Optional[ProviderInstanceModel] = None,
    ) -> List[Dict[str, Any]]:
        """List quarantined mail. ``kind`` is ``spam``/``virus``/``attachment``
        (``GET /quarantine/<kind>``)."""
        if kind not in ("spam", "virus", "attachment"):
            raise ValueError("kind must be one of: spam, virus, attachment")
        params = {"starttime": starttime} if starttime is not None else None
        data = await cls._api_get(provider_instance, f"quarantine/{kind}", params)
        return list(data) if isinstance(data, list) else []

    @classmethod
    async def release_quarantine(
        cls,
        mail_id: str,
        *,
        provider_instance: Optional[ProviderInstanceModel] = None,
    ) -> Any:
        """Deliver a quarantined message to its recipient
        (``POST /quarantine/content`` with ``action=deliver``)."""
        return await cls._api_post(
            provider_instance,
            "quarantine/content",
            {"id": mail_id, "action": "deliver"},
        )

    @classmethod
    async def delete_quarantine(
        cls,
        mail_id: str,
        *,
        provider_instance: Optional[ProviderInstanceModel] = None,
    ) -> Any:
        """Delete a quarantined message
        (``POST /quarantine/content`` with ``action=delete``)."""
        return await cls._api_post(
            provider_instance,
            "quarantine/content",
            {"id": mail_id, "action": "delete"},
        )

    # ---------------------------------------------------------------- webhooks
    @classmethod
    def verify_signature(cls, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify a PMG notification callback via HMAC-SHA256 over the raw body
        keyed by ``PMG_WEBHOOK_SECRET`` (``X-PMG-Signature``; a ``sha256=``
        prefix is tolerated). Constant-time compare; never raises."""
        secret = env(cls.PMG_WEBHOOK_SECRET_ENV)
        if not secret:
            logger.warning(
                f"PMG webhook verification: {cls.PMG_WEBHOOK_SECRET_ENV} not set; "
                "rejecting."
            )
            return False
        normalized = {k.lower(): v for k, v in (headers or {}).items()}
        provided = normalized.get(cls.PMG_SIGNATURE_HEADER, "") or ""
        if not provided:
            logger.debug("PMG webhook missing signature header; rejecting.")
            return False
        if provided.lower().startswith("sha256="):
            provided = provided[7:]
        expected = hmac.new(
            secret.encode("utf-8"), body or b"", hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, provided)


def _coerce_pmg_event(raw: Dict[str, Any], event_type: str) -> EmailDeliveryEvent:
    """Translate one PMG notification row into an ``EmailDeliveryEvent``."""

    def _ts(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return EmailDeliveryEvent(
        message_id=str(raw.get("id", "") or raw.get("msgid", "") or ""),
        provider="proxmox_mail_gateway",
        event_type=event_type,
        recipient=raw.get("receiver", "") or raw.get("to", "") or "",
        timestamp=_ts(raw.get("time") or raw.get("timestamp")),
        raw=raw,
    )


async def _dispatch_pmg_events(payload: Any, fallback_event: str) -> None:
    """Fan a PMG notification payload through the canonical hook bus."""
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
        await dispatch_email_delivery_event(_coerce_pmg_event(row, event_type))


class _EmailExtensionStub:
    """Pins ``webhook_handler``'s extension name to ``email`` (mirrors the other
    providers) so registration doesn't depend on ``EXT_EMail`` load order."""

    extension_name = "email"


def _register_pmg_webhook_handlers() -> None:
    """Register PMG notification handlers + wire the verifier. Idempotent; a
    no-op if the optional ``webhooks`` extension isn't loaded."""
    try:
        from zephyrex.extensions.webhooks import WebhookContext, webhook_handler
        from zephyrex.extensions.webhooks.BLL_Webhooks import _PROVIDER_CLASSES
    except ImportError:
        return

    for event_name in ("quarantine", "bounce", "delivered"):

        @webhook_handler(
            _EmailExtensionStub, provider="proxmox_mail_gateway", event=event_name
        )
        async def _handler(ctx: "WebhookContext", _evt: str = event_name) -> None:
            await _dispatch_pmg_events(ctx.payload, _evt)

    _PROVIDER_CLASSES[("email", "proxmox_mail_gateway")] = ProxmoxMailGatewayProvider


_register_pmg_webhook_handlers()
