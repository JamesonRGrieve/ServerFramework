# SPDX-License-Identifier: AGPL-3.0-or-later
"""IMAP + SMTP email provider — receive over IMAP, send over SMTP.

Ported from the pre-zephyrex AGInfrastructure IMAP provider into the current
static ``AbstractEmailProvider`` format. Uses only the Python standard library
(``imaplib`` / ``smtplib`` / ``email``), so it has no optional driver guard.
"""

from __future__ import annotations

import email as _email
import imaplib
import os
import smtplib
from datetime import datetime
from decimal import Decimal
from email.header import decode_header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
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
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


def _decode_header_value(value: Optional[str]) -> str:
    """Decode an RFC 2047 header into a plain string."""
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            out += text.decode(charset or "utf-8", errors="replace")
        else:
            out += text
    return out


class IMAPProvider(AbstractEmailProvider):
    """Generic IMAP (receive) + SMTP (send) email provider."""

    name: ClassVar[str] = "imap"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "IMAP/SMTP email provider (receive + send)"

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

    default_auth_strategy: ClassVar[str] = "basic"

    rate_limit: ClassVar[RateLimit] = RateLimit(rps=20, burst=40)
    degradation_policy: ClassVar[DegradationPolicy] = fail_fast()
    cost_model: ClassVar[ConstantCostModel] = ConstantCostModel(
        per_call_usd=Decimal("0.0")
    )

    dependencies: ClassVar[Dependencies] = Dependencies([])

    # Subclasses (Yahoo, etc.) override these host defaults.
    default_imap_host: ClassVar[str] = ""
    default_smtp_host: ClassVar[str] = ""
    _env_prefix: ClassVar[str] = "IMAP"

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        imap_host: str
        imap_port: int = 993
        smtp_host: str
        smtp_port: int = 587
        username: str
        password: SecretStr
        use_ssl: bool = True

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "IMAP_FROM_EMAIL",
            "imap_host": "IMAP_HOST",
            "imap_port": "IMAP_PORT",
            "smtp_host": "IMAP_SMTP_HOST",
            "smtp_port": "IMAP_SMTP_PORT",
            "username": "IMAP_USERNAME",
            "password": "IMAP_PASSWORD",
            "use_ssl": "IMAP_USE_SSL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "IMAP_HOST": "",
            "IMAP_PORT": "993",
            "IMAP_SMTP_HOST": "",
            "IMAP_SMTP_PORT": "587",
            "IMAP_USERNAME": "",
            "IMAP_PASSWORD": "",
            "IMAP_FROM_EMAIL": "",
            "IMAP_USE_SSL": "true",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "imap", "smtp", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "IMAP"

    @classmethod
    def _config(cls) -> Dict[str, Any]:
        """Resolve connection config from env, with subclass host defaults."""
        p = cls._env_prefix
        return {
            "imap_host": env(f"{p}_HOST") or cls.default_imap_host,
            "imap_port": int(env(f"{p}_PORT") or "993"),
            "smtp_host": env(f"{p}_SMTP_HOST") or cls.default_smtp_host,
            "smtp_port": int(env(f"{p}_SMTP_PORT") or "587"),
            "username": env(f"{p}_USERNAME"),
            "password": env(f"{p}_PASSWORD"),
            "from_email": env(f"{p}_FROM_EMAIL") or env(f"{p}_USERNAME"),
            "use_ssl": (env(f"{p}_USE_SSL") or "true").lower() != "false",
        }

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        cfg = cls._config()
        password = cfg["password"]
        if instance is not None:
            password = instance.api_key or password
        if not cfg["imap_host"] or not cfg["username"] or not password:
            logger.error(f"{cls.get_platform_name()} host/username/password missing")
            return False
        return True

    @classmethod
    def health_check(cls) -> HealthReport:
        cfg = cls._config()
        if not cfg["imap_host"]:
            return HealthReport(HealthStatus.DOWN, detail="IMAP host not configured")
        try:
            conn: imaplib.IMAP4
            if cfg["use_ssl"]:
                conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=5)
            else:
                conn = imaplib.IMAP4(cfg["imap_host"], cfg["imap_port"], timeout=5)
            try:
                conn.logout()
            except Exception:
                pass
            return HealthReport(HealthStatus.OK, detail="IMAP reachable")
        except Exception as exc:  # noqa: BLE001 — defensive
            return HealthReport(HealthStatus.DOWN, detail=f"IMAP error: {exc}")

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        cfg = cls._config()
        if instance is not None and instance.api_key:
            cfg["password"] = instance.api_key
        if not cfg["imap_host"] or not cfg["username"] or not cfg["password"]:
            logger.error(f"{cls.get_platform_name()} connection parameters missing")
            return None
        return AbstractProviderInstance_SDK(cfg)

    @classmethod
    def _imap_connect(cls, cfg: Dict[str, Any]):
        conn: imaplib.IMAP4
        if cfg["use_ssl"]:
            conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        else:
            conn = imaplib.IMAP4(cfg["imap_host"], cfg["imap_port"])
        conn.login(cfg["username"], cfg["password"])
        return conn

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
        """Send an email over SMTP submission."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error  # type: ignore[no-any-return]

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return f"Failed to send email: could not bond {cls.get_platform_name()}"
        cfg = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or cfg.get("from_email")
            or cfg.get("username")
        )
        if not from_email:
            return "Failed to send email: from_email not configured"
        if not cfg.get("smtp_host"):
            return "Failed to send email: SMTP host not configured"

        try:
            message = MIMEMultipart()
            message["From"] = from_email
            message["To"] = recipient
            message["Subject"] = subject
            subtype = "html" if "<html" in body.lower() else "plain"
            message.attach(MIMEText(body, subtype))
            if attachments:
                for path in attachments:
                    if not os.path.exists(path):
                        logger.warning(f"Attachment file not found: {path}")
                        continue
                    with open(path, "rb") as fh:
                        part = MIMEApplication(fh.read(), Name=os.path.basename(path))
                    part["Content-Disposition"] = (
                        f'attachment; filename="{os.path.basename(path)}"'
                    )
                    message.attach(part)

            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as server:
                server.starttls()
                server.login(cfg["username"], cfg["password"])
                server.send_message(message)
            return f"Email sent successfully to {recipient}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error sending {cls.get_platform_name()} email: {e}")
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
        """Fetch the most recent messages from a folder over IMAP."""
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        cfg = bonded.sdk
        try:
            conn = cls._imap_connect(cfg)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error connecting to {cls.get_platform_name()} IMAP: {e}")
            return []
        try:
            conn.select(folder_name)
            typ, data = conn.search(None, "ALL")
            if typ != "OK":
                return []
            ids = data[0].split()
            ids = ids[-max_emails:] if max_emails else ids
            out: List[Dict[str, Any]] = []
            for msg_id in reversed(ids):
                typ, msg_data = conn.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = _email.message_from_bytes(msg_data[0][1])
                out.append(
                    {
                        "id": msg_id.decode(),
                        "from": _decode_header_value(msg.get("From")),
                        "to": _decode_header_value(msg.get("To")),
                        "subject": _decode_header_value(msg.get("Subject")),
                        "date": _decode_header_value(msg.get("Date")),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error fetching {cls.get_platform_name()} emails: {e}")
            return []
        finally:
            try:
                conn.logout()
            except Exception:
                pass

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
        """Search a folder for messages whose text matches ``query``."""
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        cfg = bonded.sdk
        try:
            conn = cls._imap_connect(cfg)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error connecting to {cls.get_platform_name()} IMAP: {e}")
            return []
        try:
            conn.select(folder_name)
            typ, data = conn.search(None, "TEXT", f'"{query}"')
            if typ != "OK":
                return []
            ids = data[0].split()[-max_emails:]
            out: List[Dict[str, Any]] = []
            for msg_id in reversed(ids):
                typ, msg_data = conn.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = _email.message_from_bytes(msg_data[0][1])
                out.append(
                    {
                        "id": msg_id.decode(),
                        "from": _decode_header_value(msg.get("From")),
                        "subject": _decode_header_value(msg.get("Subject")),
                        "date": _decode_header_value(msg.get("Date")),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error searching {cls.get_platform_name()} emails: {e}")
            return []
        finally:
            try:
                conn.logout()
            except Exception:
                pass

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
        logger.warning("Creating drafts is not supported by the IMAP provider")
        return "Creating draft emails is not supported"

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by the IMAP provider")
        return "Replying to emails is not supported"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by the IMAP provider")
        return "Deleting emails is not supported"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by the IMAP provider")
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
                raise map_upstream_status(status, legacy_result, provider=cls.name)
            raise map_validation_error(legacy_result)
        recipient = message.to[0].format() if message.to else ""
        return {
            "message_id": "",
            "provider": cls.name,
            "accepted_at": datetime.utcnow().isoformat(),
            "recipient": recipient,
            "upstream_response": {"raw": legacy_result},
        }
