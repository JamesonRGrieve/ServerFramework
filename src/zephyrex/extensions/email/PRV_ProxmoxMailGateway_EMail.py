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
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Set, Type

from pydantic import EmailStr, HttpUrl, SecretStr

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance_SDK,
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.billing.BLL_CostModel import ConstantCostModel
from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EmailDeliveryEvent,
    Importance,
    _DeprecatedEnvDict,
    dispatch_email_delivery_event,
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

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        smtp_host: str
        smtp_port: int = 587
        smtp_username: Optional[str] = None
        smtp_password: Optional[SecretStr] = None
        use_tls: bool = True
        # REST API (management surface): base URL + a PMGAPIToken value of the
        # form ``user@realm!tokenid=secret``.
        api_url: HttpUrl
        api_token: SecretStr
        api_node: str = "localhost"
        api_tls_verify: bool = True

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "PMG_FROM_EMAIL",
            "smtp_host": "PMG_SMTP_HOST",
            "smtp_port": "PMG_SMTP_PORT",
            "smtp_username": "PMG_SMTP_USERNAME",
            "smtp_password": "PMG_SMTP_PASSWORD",
            "use_tls": "PMG_SMTP_USE_TLS",
            "api_url": "PMG_API_URL",
            "api_token": "PMG_API_TOKEN",
            "api_node": "PMG_API_NODE",
            "api_tls_verify": "PMG_API_TLS_VERIFY",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "PMG_FROM_EMAIL": "",
            "PMG_SMTP_HOST": "",
            "PMG_SMTP_PORT": "587",
            "PMG_SMTP_USERNAME": "",
            "PMG_SMTP_PASSWORD": "",
            "PMG_SMTP_USE_TLS": "true",
            "PMG_API_URL": "",
            "PMG_API_TOKEN": "",
            "PMG_API_NODE": "localhost",
            "PMG_API_TLS_VERIFY": "true",
        }
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
        host = env("PMG_SMTP_HOST")
        if not host:
            logger.error("PMG SMTP host not configured")
            return False
        return True

    @staticmethod
    def _api_base() -> str:
        return (env("PMG_API_URL") or "").rstrip("/")

    @classmethod
    def _api_headers(
        cls, instance: Optional[ProviderInstanceModel] = None
    ) -> Dict[str, str]:
        token = (instance.api_key if instance else None) or env("PMG_API_TOKEN")
        return {"Authorization": f"PMGAPIToken={token}"} if token else {}

    @classmethod
    def _api_client(cls) -> Any:
        verify = (env("PMG_API_TLS_VERIFY") or "true").strip().lower() != "false"
        return get_async_client(ClientPolicy(timeout=15.0, tls_verify=verify))

    @classmethod
    async def _api_get(cls, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        base = cls._api_base()
        if not base:
            raise RuntimeError("PMG_API_URL not configured")
        response = await cls._api_client().get(
            f"{base}/{path.lstrip('/')}", headers=cls._api_headers(), params=params
        )
        response.raise_for_status()
        body = response.json()
        # PMG wraps payloads in a top-level {"data": ...} envelope.
        return body.get("data", body) if isinstance(body, dict) else body

    @classmethod
    async def _api_post(cls, path: str, data: Dict[str, Any]) -> Any:
        base = cls._api_base()
        if not base:
            raise RuntimeError("PMG_API_URL not configured")
        response = await cls._api_client().post(
            f"{base}/{path.lstrip('/')}", headers=cls._api_headers(), data=data
        )
        response.raise_for_status()
        body = response.json()
        return body.get("data", body) if isinstance(body, dict) else body

    # --------------------------------------------------------------- lifecycle
    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return None
        try:
            config = {
                "host": env("PMG_SMTP_HOST"),
                "port": int(env("PMG_SMTP_PORT") or "587"),
                "username": env("PMG_SMTP_USERNAME") or None,
                "password": env("PMG_SMTP_PASSWORD") or None,
                "start_tls": (env("PMG_SMTP_USE_TLS") or "true").lower() != "false",
                "from_email": env("PMG_FROM_EMAIL"),
                "api_url": cls._api_base(),
                "api_token": (instance.api_key if instance else None)
                or env("PMG_API_TOKEN"),
                "auth_strategy": _build_auth_strategy(
                    cls.default_auth_strategy,
                    api_key=(instance.api_key if instance else None)
                    or env("PMG_API_TOKEN"),
                ),
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond Proxmox Mail Gateway instance: {e}")
            return None

    @classmethod
    def health_check(cls) -> HealthReport:
        """Probe PMG liveness via ``GET /version`` on the REST API."""
        if not cls._api_base():
            return HealthReport(HealthStatus.DOWN, detail="PMG_API_URL not configured")
        try:
            import asyncio

            async def _probe() -> str:
                data = await cls._api_get("version")
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
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to bond Proxmox Mail Gateway instance"
        config = bonded.sdk
        from_email = provider_instance.get_setting("from_email") or config.get(
            "from_email"
        )
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
                password=(
                    config["password"].get_secret_value()
                    if hasattr(config.get("password"), "get_secret_value")
                    else config.get("password")
                ),
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
        data = await cls._api_get("statistics/mail", params or None)
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
        node = env("PMG_API_NODE") or "localhost"
        params: Dict[str, Any] = {}
        if starttime is not None:
            params["starttime"] = starttime
        if endtime is not None:
            params["endtime"] = endtime
        data = await cls._api_get(f"nodes/{node}/tracker", params or None)
        return list(data) if isinstance(data, list) else []

    @classmethod
    async def list_quarantine(
        cls, kind: str = "spam", starttime: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List quarantined mail. ``kind`` is ``spam``/``virus``/``attachment``
        (``GET /quarantine/<kind>``)."""
        if kind not in ("spam", "virus", "attachment"):
            raise ValueError("kind must be one of: spam, virus, attachment")
        params = {"starttime": starttime} if starttime is not None else None
        data = await cls._api_get(f"quarantine/{kind}", params)
        return list(data) if isinstance(data, list) else []

    @classmethod
    async def release_quarantine(cls, mail_id: str) -> Any:
        """Deliver a quarantined message to its recipient
        (``POST /quarantine/content`` with ``action=deliver``)."""
        return await cls._api_post(
            "quarantine/content", {"id": mail_id, "action": "deliver"}
        )

    @classmethod
    async def delete_quarantine(cls, mail_id: str) -> Any:
        """Delete a quarantined message
        (``POST /quarantine/content`` with ``action=delete``)."""
        return await cls._api_post(
            "quarantine/content", {"id": mail_id, "action": "delete"}
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
