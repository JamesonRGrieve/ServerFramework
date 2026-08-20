# SPDX-License-Identifier: AGPL-3.0-or-later
"""Yahoo Mail email provider — IMAP receive + SMTP send.

Yahoo Mail speaks standard IMAP/SMTP, so this provider is a thin subclass of
:class:`IMAPProvider` with Yahoo host defaults and a ``YAHOO_*`` env namespace.
Yahoo requires an app password rather than the account password.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import EmailStr, SecretStr

from zephyrex.extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    _DeprecatedEnvDict,
)
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider


class YahooProvider(IMAPProvider):
    """Yahoo Mail provider (IMAP receive + SMTP send)."""

    name: ClassVar[str] = "yahoo"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Yahoo Mail email provider (IMAP + SMTP)"

    default_imap_host: ClassVar[str] = "imap.mail.yahoo.com"
    default_smtp_host: ClassVar[str] = "smtp.mail.yahoo.com"
    _env_prefix: ClassVar[str] = "YAHOO"

    class Settings(AbstractEmailProvider.Settings):
        from_email: EmailStr
        username: str
        password: SecretStr
        imap_host: str = "imap.mail.yahoo.com"
        imap_port: int = 993
        smtp_host: str = "smtp.mail.yahoo.com"
        smtp_port: int = 587
        use_ssl: bool = True

        _env_field_map: ClassVar[Dict[str, str]] = {
            "from_email": "YAHOO_FROM_EMAIL",
            "username": "YAHOO_USERNAME",
            "password": "YAHOO_PASSWORD",
            "imap_host": "YAHOO_IMAP_HOST",
            "imap_port": "YAHOO_IMAP_PORT",
            "smtp_host": "YAHOO_SMTP_HOST",
            "smtp_port": "YAHOO_SMTP_PORT",
            "use_ssl": "YAHOO_USE_SSL",
        }

    _env: ClassVar[Dict[str, Any]] = _DeprecatedEnvDict(
        {
            "YAHOO_HOST": "imap.mail.yahoo.com",
            "YAHOO_PORT": "993",
            "YAHOO_SMTP_HOST": "smtp.mail.yahoo.com",
            "YAHOO_SMTP_PORT": "587",
            "YAHOO_USERNAME": "",
            "YAHOO_PASSWORD": "",
            "YAHOO_FROM_EMAIL": "",
            "YAHOO_USE_SSL": "true",
        }
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "imap", "smtp", "messaging", "yahoo"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Yahoo"
