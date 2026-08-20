# SPDX-License-Identifier: AGPL-3.0-or-later
"""POP3 + SMTP email provider — receive over POP3, send over SMTP.

Ported from the pre-zephyrex AGInfrastructure POP3 provider. POP3 has no
server-side search or folders, so this subclass of :class:`IMAPProvider`
overrides the receive side (``poplib``) and disables search; the SMTP send
path is inherited unchanged. Standard library only.
"""

from __future__ import annotations

import email as _email
import poplib
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import EmailStr, SecretStr

from zephyrex.extensions.AbstractExtensionProvider import (
    HealthReport,
    HealthStatus,
    ability,
)
from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    _DeprecatedEnvDict,
)
from zephyrex.extensions.email.PRV_IMAP_EMail import (
    IMAPProvider,
    _decode_header_value,
)
from zephyrex.extensions.RateLimit import RateLimit
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.BLL_Providers import ProviderInstanceModel


class POP3Provider(IMAPProvider):
    """Generic POP3 (receive) + SMTP (send) email provider."""

    name: ClassVar[str] = "pop3"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "POP3/SMTP email provider (receive + send)"

    _abilities: ClassVar = {"email_send", "email_get"}

    capabilities: ClassVar = frozenset(
        {Capability.SEND, Capability.LIST, Capability.READ}
    )

    _env_prefix: ClassVar[str] = "POP3"

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        pop3_host: str
        pop3_port: int = 995
        smtp_host: str
        smtp_port: int = 587
        username: str
        password: SecretStr
        use_ssl: bool = True

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "POP3_FROM_EMAIL",
            "pop3_host": "POP3_HOST",
            "pop3_port": "POP3_PORT",
            "smtp_host": "POP3_SMTP_HOST",
            "smtp_port": "POP3_SMTP_PORT",
            "username": "POP3_USERNAME",
            "password": "POP3_PASSWORD",
            "use_ssl": "POP3_USE_SSL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "POP3_HOST": "",
            "POP3_PORT": "995",
            "POP3_SMTP_HOST": "",
            "POP3_SMTP_PORT": "587",
            "POP3_USERNAME": "",
            "POP3_PASSWORD": "",
            "POP3_FROM_EMAIL": "",
            "POP3_USE_SSL": "true",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "pop3", "smtp", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "POP3"

    @classmethod
    def _config(cls) -> Dict[str, Any]:
        """POP3 config (the ``imap_host`` slot carries the POP3 host)."""
        return {
            "imap_host": env("POP3_HOST"),
            "imap_port": int(env("POP3_PORT") or "995"),
            "smtp_host": env("POP3_SMTP_HOST"),
            "smtp_port": int(env("POP3_SMTP_PORT") or "587"),
            "username": env("POP3_USERNAME"),
            "password": env("POP3_PASSWORD"),
            "from_email": env("POP3_FROM_EMAIL") or env("POP3_USERNAME"),
            "use_ssl": (env("POP3_USE_SSL") or "true").lower() != "false",
        }

    @classmethod
    def health_check(cls) -> HealthReport:
        cfg = cls._config()
        if not cfg["imap_host"]:
            return HealthReport(HealthStatus.DOWN, detail="POP3 host not configured")
        try:
            conn: poplib.POP3
            if cfg["use_ssl"]:
                conn = poplib.POP3_SSL(cfg["imap_host"], cfg["imap_port"], timeout=5)
            else:
                conn = poplib.POP3(cfg["imap_host"], cfg["imap_port"], timeout=5)
            try:
                conn.quit()
            except Exception:
                pass
            return HealthReport(HealthStatus.OK, detail="POP3 reachable")
        except Exception as exc:  # noqa: BLE001 — defensive
            return HealthReport(HealthStatus.DOWN, detail=f"POP3 error: {exc}")

    @classmethod
    @ability(name="email_get")
    async def get_emails(
        cls,
        provider_instance: ProviderInstanceModel,
        folder_name: str = "INBOX",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """Fetch the most recent messages over POP3."""
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return []
        cfg = bonded.sdk
        conn: poplib.POP3
        try:
            if cfg["use_ssl"]:
                conn = poplib.POP3_SSL(cfg["imap_host"], cfg["imap_port"])
            else:
                conn = poplib.POP3(cfg["imap_host"], cfg["imap_port"])
            conn.user(cfg["username"])
            conn.pass_(cfg["password"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error connecting to POP3: {e}")
            return []
        try:
            count = len(conn.list()[1])
            start = max(1, count - max_emails + 1)
            out: List[Dict[str, Any]] = []
            for i in range(count, start - 1, -1):
                raw = b"\n".join(conn.retr(i)[1])
                msg = _email.message_from_bytes(raw)
                out.append(
                    {
                        "id": str(i),
                        "from": _decode_header_value(msg.get("From")),
                        "to": _decode_header_value(msg.get("To")),
                        "subject": _decode_header_value(msg.get("Subject")),
                        "date": _decode_header_value(msg.get("Date")),
                    }
                )
            return out
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error fetching POP3 emails: {e}")
            return []
        finally:
            try:
                conn.quit()
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
        logger.warning("Server-side search is not supported by POP3")
        return []
