# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mailgun email provider — send-only HTTP API relay.

Ported from the pre-zephyrex AGInfrastructure Mailgun provider into the current
static ``AbstractEmailProvider`` format (modelled on ``PRV_SMTP2Go_EMail``).
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional, Set

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
    _DeprecatedEnvDict,
)
from zephyrex.extensions.ExternalErrors import DegradationPolicy, fail_fast
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:
    import requests as _requests

    _requests_available = True
except ImportError:  # pragma: no cover - optional driver
    _requests = None  # type: ignore[assignment]
    _requests_available = False


class MailgunProvider(AbstractEmailProvider):
    """Mailgun email provider using the hosted HTTP API (send-only)."""

    name: ClassVar[str] = "mailgun"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Mailgun HTTP API email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    default_auth_strategy: ClassVar[str] = "basic"

    rate_limit: ClassVar[RateLimit] = RateLimit(rps=100, burst=200)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0001")
    )

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="Requests",
                semver=">=2.28.0",
                reason="HTTP client for Mailgun API",
            )
        ]
    )

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        api_key: SecretStr
        domain: str
        api_url: HttpUrl = "https://api.mailgun.net/v3"  # type: ignore[assignment]

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "MAILGUN_FROM_EMAIL",
            "api_key": "MAILGUN_API_KEY",
            "domain": "MAILGUN_DOMAIN",
            "api_url": "MAILGUN_API_URL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "MAILGUN_API_KEY": "",
            "MAILGUN_FROM_EMAIL": "",
            "MAILGUN_DOMAIN": "",
            "MAILGUN_API_URL": "https://api.mailgun.net/v3",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "messaging", "communication"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Mailgun"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _requests_available:
            logger.error("requests package not available")
            return False
        api_key = env("MAILGUN_API_KEY")
        if instance is not None:
            api_key = instance.api_key or api_key
        if not api_key:
            logger.error("Mailgun API key not configured")
            return False
        if not env("MAILGUN_DOMAIN"):
            logger.error("Mailgun domain not configured")
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        api_key = env("MAILGUN_API_KEY")
        domain = env("MAILGUN_DOMAIN")
        if not api_key or not domain:
            return HealthReport(
                HealthStatus.DOWN, detail="Mailgun api_key/domain not configured"
            )
        if not _requests_available:
            return HealthReport(HealthStatus.DOWN, detail="requests not installed")
        api_url = (env("MAILGUN_API_URL") or "https://api.mailgun.net/v3").rstrip("/")
        try:
            response = _requests.get(
                f"{api_url}/{domain}/stats/total",
                auth=("api", api_key),
                params={"event": "delivered"},
                timeout=5.0,
            )
            status = response.status_code
            if 200 <= status < 300:
                return HealthReport(HealthStatus.OK, detail=f"stats status {status}")
            if 400 <= status < 500:
                return HealthReport(
                    HealthStatus.DEGRADED, detail=f"stats status {status}"
                )
            return HealthReport(HealthStatus.DOWN, detail=f"stats status {status}")
        except Exception as exc:  # noqa: BLE001 — defensive
            return HealthReport(HealthStatus.DOWN, detail=f"network error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _requests_available:
            logger.error("requests package not available")
            return None
        try:
            api_key = (instance.api_key if instance else None) or env("MAILGUN_API_KEY")
            if not api_key:
                logger.error("Mailgun API key missing")
                return None
            config = {
                "api_key": api_key,
                "domain": env("MAILGUN_DOMAIN"),
                "from_email": env("MAILGUN_FROM_EMAIL"),
                "api_url": (
                    env("MAILGUN_API_URL") or "https://api.mailgun.net/v3"
                ).rstrip("/"),
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to bond Mailgun instance: {e}")
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
        """Send an email via Mailgun's /messages REST API."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]

        if not _requests_available:
            return "Failed to send email: requests not installed"

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond Mailgun instance"
        config = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or config.get("from_email")
            or env("MAILGUN_FROM_EMAIL")
        )
        domain = config.get("domain")
        if not from_email:
            return "Failed to send email: Mailgun from_email not configured"
        if not domain:
            return "Failed to send email: Mailgun domain not configured"

        data: Dict[str, Any] = {"from": from_email, "to": recipient, "subject": subject}
        if "<html" in body.lower():
            data["html"] = body
        else:
            data["text"] = body
        if importance.lower() == "high":
            data["h:Importance"] = "High"
            data["h:X-Priority"] = "1"
        elif importance.lower() == "low":
            data["h:Importance"] = "Low"
            data["h:X-Priority"] = "5"

        open_files = []
        files = []
        try:
            if attachments:
                for path in attachments:
                    if not os.path.exists(path):
                        logger.warning(f"Attachment file not found: {path}")
                        continue
                    fh = open(path, "rb")
                    open_files.append(fh)
                    files.append(("attachment", (os.path.basename(path), fh)))
            response = _requests.post(
                f"{config['api_url']}/{domain}/messages",
                auth=("api", config["api_key"]),
                data=data,
                files=files or None,
                timeout=30.0,
            )
            if 200 <= response.status_code < 300:
                logger.debug(f"Mailgun: email sent successfully to {recipient}")
                return f"Email sent successfully to {recipient}"
            return f"Failed to send email: {response.status_code}: {response.text}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error sending Mailgun email: {e}")
            return f"Failed to send email: {e}"
        finally:
            for fh in open_files:
                try:
                    fh.close()
                except Exception:
                    pass

    @staticmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance, folder_name="Inbox", max_emails=10, page_size=10
    ):
        logger.warning("Getting emails is not supported by Mailgun")
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
        logger.warning("Creating drafts is not supported by Mailgun")
        return "Creating draft emails is not supported by Mailgun"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None
    ):
        logger.warning("Searching emails is not supported by Mailgun")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by Mailgun")
        return "Replying to emails is not supported by Mailgun"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by Mailgun")
        return "Deleting emails is not supported by Mailgun"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by Mailgun")
        return []
