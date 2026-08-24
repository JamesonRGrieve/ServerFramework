# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google (Gmail) email provider — Gmail REST API over an OAuth access token.

Ported from the pre-zephyrex AGInfrastructure Google provider into the current
static ``AbstractEmailProvider`` format. Authenticates with a pre-obtained
OAuth2 access token; the Google client libraries are optional and guarded.
"""

from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal
from email.mime.text import MIMEText
from typing import Any, ClassVar, Dict, List, Optional, Set

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
    extract_status_code as _extract_status_code,
    map_upstream_status,
    map_validation_error,
)
from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EmailMessage,
    _DeprecatedEnvDict,
)
from zephyrex.extensions.ExternalErrors import DegradationPolicy, fail_fast
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

try:
    from google.oauth2.credentials import Credentials as _GoogleCredentials
    from googleapiclient.discovery import build as _google_build

    _google_available = True
except ImportError:  # pragma: no cover - optional driver
    _GoogleCredentials = None  # type: ignore[assignment,misc]
    _google_build = None  # type: ignore[assignment,misc]
    _google_available = False


class GoogleProvider(AbstractEmailProvider):
    """Gmail email provider over the Gmail REST API (OAuth access token)."""

    name: ClassVar[str] = "google"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Google Gmail email provider (Gmail REST API)"

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
                name="google-api-python-client",
                friendly_name="Google API Python Client",
                semver=">=2.0.0",
                reason="Gmail REST API access",
            ),
            PIP_Dependency(
                name="google-auth",
                friendly_name="Google Auth",
                semver=">=2.0.0",
                reason="OAuth2 credentials for Gmail",
            ),
        ]
    )

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        access_token: SecretStr

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "GOOGLE_EMAIL_FROM_EMAIL",
            "access_token": "GOOGLE_EMAIL_ACCESS_TOKEN",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "GOOGLE_EMAIL_ACCESS_TOKEN": "",
            "GOOGLE_EMAIL_FROM_EMAIL": "",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "gmail", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Google"

    @classmethod
    def _access_token(cls, instance: Optional[ProviderInstanceModel] = None) -> str:
        token: str = env("GOOGLE_EMAIL_ACCESS_TOKEN") or ""
        if instance is not None:
            token = instance.api_key or token
        return token

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _google_available:
            logger.error("google-api-python-client/google-auth not available")
            return False
        if not cls._access_token(instance):
            logger.error("Google access token not configured")
            return False
        return True

    @classmethod
    def _build_service(cls, token: str):
        creds = _GoogleCredentials(token=token)
        return _google_build("gmail", "v1", credentials=creds, cache_discovery=False)

    @classmethod
    def health_check(cls) -> HealthReport:
        token = cls._access_token()
        if not token:
            return HealthReport(HealthStatus.DOWN, detail="access token not configured")
        if not _google_available:
            return HealthReport(HealthStatus.DOWN, detail="google client not installed")
        try:
            service = cls._build_service(token)
            service.users().getProfile(userId="me").execute()
            return HealthReport(HealthStatus.OK, detail="gmail profile reachable")
        except Exception as exc:  # noqa: BLE001 — defensive
            return HealthReport(HealthStatus.DOWN, detail=f"gmail error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _google_available:
            logger.error("google client not available")
            return None
        token = cls._access_token(instance)
        if not token:
            logger.error("Google access token missing")
            return None
        return AbstractProviderInstance_SDK(
            {
                "access_token": token,
                "from_email": env("GOOGLE_EMAIL_FROM_EMAIL"),
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
        """Send an email via the Gmail REST API."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]
        if not _google_available:
            return "Failed to send email: google client not installed"
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond Google instance"
        cfg = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or cfg.get("from_email")
            or env("GOOGLE_EMAIL_FROM_EMAIL")
        )
        try:
            message = MIMEText(body, "html" if "<html" in body.lower() else "plain")
            message["to"] = recipient
            message["subject"] = subject
            if from_email:
                message["from"] = from_email
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            service = cls._build_service(cfg["access_token"])
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email sent successfully to {recipient}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error sending Gmail email: {e}")
            return f"Failed to send email: {e}"

    @classmethod
    @ability(name="email_get")
    async def get_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        folder_name: str = "INBOX",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """List recent message metadata via the Gmail REST API."""
        if not _google_available:
            return []
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        try:
            service = cls._build_service(bonded.sdk["access_token"])
            results = (
                service.users()
                .messages()
                .list(userId="me", maxResults=max_emails, labelIds=[folder_name])
                .execute()
            )
            out: List[Dict[str, Any]] = []
            for ref in results.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="metadata")
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                out.append(
                    {
                        "id": ref["id"],
                        "from": headers.get("from", ""),
                        "to": headers.get("to", ""),
                        "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""),
                        "snippet": msg.get("snippet", ""),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error fetching Gmail emails: {e}")
            return []

    @classmethod
    @ability(name="email_search")
    async def search_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "INBOX",
        max_emails: int = 10,
        date_range=None,
    ) -> List[Dict[str, Any]]:
        """Search messages using the Gmail query syntax."""
        if not _google_available:
            return []
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        try:
            service = cls._build_service(bonded.sdk["access_token"])
            results = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_emails)
                .execute()
            )
            out: List[Dict[str, Any]] = []
            for ref in results.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="metadata")
                    .execute()
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                out.append(
                    {
                        "id": ref["id"],
                        "from": headers.get("from", ""),
                        "subject": headers.get("subject", ""),
                        "snippet": msg.get("snippet", ""),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error searching Gmail emails: {e}")
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
        logger.warning("Draft creation is not implemented for the Google provider")
        return "Creating draft emails is not supported"

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not implemented for the Google provider")
        return "Replying to emails is not supported"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not implemented for the Google provider")
        return "Deleting emails is not supported"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning(
            "Attachment processing is not implemented for the Google provider"
        )
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
                raise map_upstream_status(status, legacy_result, provider="google")
            raise map_validation_error(legacy_result)
        recipient = message.to[0].format() if message.to else ""
        return {
            "message_id": "",
            "provider": cls.name,
            "accepted_at": datetime.utcnow().isoformat(),
            "recipient": recipient,
            "upstream_response": {"raw": legacy_result},
        }
