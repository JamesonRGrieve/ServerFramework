# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft (Outlook) email provider — Microsoft Graph API over OAuth token.

Ported from the pre-zephyrex AGInfrastructure Microsoft provider into the
current static ``AbstractEmailProvider`` format. Authenticates with a
pre-obtained OAuth2 access token against Microsoft Graph.
"""

from __future__ import annotations

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


class MicrosoftProvider(AbstractEmailProvider):
    """Microsoft Outlook email provider over the Microsoft Graph API."""

    name: ClassVar[str] = "microsoft"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Microsoft Outlook email provider (Graph API)"

    _abilities: ClassVar[Set[str]] = {"email_send", "email_get", "email_search"}

    capabilities: ClassVar = frozenset(
        {
            Capability.SEND,
            Capability.LIST,
            Capability.SEARCH,
            Capability.READ,
            Capability.ATTACHMENTS,
        }
    )

    default_auth_strategy: ClassVar[str] = "bearer"

    rate_limit: ClassVar[RateLimit] = RateLimit(rps=25, burst=50)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0")
    )

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="requests",
                friendly_name="Requests",
                semver=">=2.28.0",
                reason="HTTP client for Microsoft Graph API",
            )
        ]
    )

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        access_token: SecretStr
        api_url: HttpUrl = "https://graph.microsoft.com/v1.0"  # type: ignore[assignment]

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "MICROSOFT_EMAIL_FROM_EMAIL",
            "access_token": "MICROSOFT_EMAIL_ACCESS_TOKEN",
            "api_url": "MICROSOFT_EMAIL_API_URL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "MICROSOFT_EMAIL_ACCESS_TOKEN": "",
            "MICROSOFT_EMAIL_FROM_EMAIL": "",
            "MICROSOFT_EMAIL_API_URL": "https://graph.microsoft.com/v1.0",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "outlook", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Microsoft"

    @classmethod
    def _access_token(cls, instance: Optional[ProviderInstanceModel] = None) -> str:
        token: str = env("MICROSOFT_EMAIL_ACCESS_TOKEN") or ""
        if instance is not None:
            token = instance.api_key or token
        return token

    @classmethod
    def _api_url(cls) -> str:
        return (
            env("MICROSOFT_EMAIL_API_URL") or "https://graph.microsoft.com/v1.0"
        ).rstrip("/")

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _requests_available:
            logger.error("requests package not available")
            return False
        if not cls._access_token(instance):
            logger.error("Microsoft access token not configured")
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        token = cls._access_token()
        if not token:
            return HealthReport(HealthStatus.DOWN, detail="access token not configured")
        if not _requests_available:
            return HealthReport(HealthStatus.DOWN, detail="requests not installed")
        try:
            response = _requests.get(
                f"{cls._api_url()}/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            status = response.status_code
            if 200 <= status < 300:
                return HealthReport(HealthStatus.OK, detail=f"/me status {status}")
            if 400 <= status < 500:
                return HealthReport(
                    HealthStatus.DEGRADED, detail=f"/me status {status}"
                )
            return HealthReport(HealthStatus.DOWN, detail=f"/me status {status}")
        except Exception as exc:  # noqa: BLE001 — defensive
            return HealthReport(HealthStatus.DOWN, detail=f"network error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _requests_available:
            logger.error("requests package not available")
            return None
        token = cls._access_token(instance)
        if not token:
            logger.error("Microsoft access token missing")
            return None
        return AbstractProviderInstance_SDK(
            {
                "access_token": token,
                "api_url": cls._api_url(),
                "from_email": env("MICROSOFT_EMAIL_FROM_EMAIL"),
            }
        )

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
        """Send an email via the Microsoft Graph /me/sendMail endpoint."""
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
            return "Failed to send email: could not bond Microsoft instance"
        cfg = bonded.sdk
        content_type = "HTML" if "<html" in body.lower() else "Text"
        importance_map = {"high": "High", "low": "Low", "normal": "Normal"}
        payload: Dict[str, Any] = {
            "message": {
                "subject": subject,
                "importance": importance_map.get(importance.lower(), "Normal"),
                "body": {"contentType": content_type, "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            "saveToSentItems": True,
        }
        try:
            response = _requests.post(
                f"{cfg['api_url']}/me/sendMail",
                headers={
                    "Authorization": f"Bearer {cfg['access_token']}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            if 200 <= response.status_code < 300:
                return f"Email sent successfully to {recipient}"
            return f"Failed to send email: {response.status_code}: {response.text}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error sending Microsoft email: {e}")
            return f"Failed to send email: {e}"

    @classmethod
    @ability(name="email_get")
    async def get_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """List recent messages from a mail folder via Graph."""
        if not _requests_available:
            return []
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        cfg = bonded.sdk
        headers = {"Authorization": f"Bearer {cfg['access_token']}"}
        try:
            url = (
                f"{cfg['api_url']}/me/mailFolders/{folder_name}/messages"
                f"?$top={max_emails}&$orderby=receivedDateTime desc"
            )
            response = _requests.get(url, headers=headers, timeout=15.0)
            if not (200 <= response.status_code < 300):
                logger.error(f"Graph list failed: {response.status_code}")
                return []
            out: List[Dict[str, Any]] = []
            for msg in response.json().get("value", []):
                out.append(
                    {
                        "id": msg.get("id"),
                        "from": msg.get("from", {})
                        .get("emailAddress", {})
                        .get("address", ""),
                        "subject": msg.get("subject", ""),
                        "date": msg.get("receivedDateTime", ""),
                        "preview": msg.get("bodyPreview", ""),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error fetching Microsoft emails: {e}")
            return []

    @classmethod
    @ability(name="email_search")
    async def search_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        date_range=None,
    ) -> List[Dict[str, Any]]:
        """Search messages via Graph ``$search``."""
        if not _requests_available:
            return []
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        cfg = bonded.sdk
        headers = {
            "Authorization": f"Bearer {cfg['access_token']}",
            "ConsistencyLevel": "eventual",
        }
        try:
            url = f'{cfg["api_url"]}/me/messages?$search="{query}"&$top={max_emails}'
            response = _requests.get(url, headers=headers, timeout=15.0)
            if not (200 <= response.status_code < 300):
                return []
            out: List[Dict[str, Any]] = []
            for msg in response.json().get("value", []):
                out.append(
                    {
                        "id": msg.get("id"),
                        "from": msg.get("from", {})
                        .get("emailAddress", {})
                        .get("address", ""),
                        "subject": msg.get("subject", ""),
                        "preview": msg.get("bodyPreview", ""),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error searching Microsoft emails: {e}")
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
        logger.warning("Draft creation is not implemented for the Microsoft provider")
        return "Creating draft emails is not supported"

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not implemented for the Microsoft provider")
        return "Replying to emails is not supported"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not implemented for the Microsoft provider")
        return "Deleting emails is not supported"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning(
            "Attachment processing is not implemented for the Microsoft provider"
        )
        return []
