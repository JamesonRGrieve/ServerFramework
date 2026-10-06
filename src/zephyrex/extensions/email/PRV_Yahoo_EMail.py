# SPDX-License-Identifier: AGPL-3.0-or-later
"""Yahoo Mail email provider — IMAP receive + SMTP send.

Yahoo Mail speaks standard IMAP/SMTP, so this provider is a thin subclass of
:class:`IMAPProvider` with Yahoo's hosts as its defaults and the ``YAHOO_*``
variables as the operator's. Yahoo requires an app password rather than the
account password.
"""

from __future__ import annotations

from typing import ClassVar, List, Tuple

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.email.EXT_EMail import AbstractEmailProvider
from zephyrex.extensions.email.InboundIMAP import IMAP_INBOUND_SETTINGS
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider, mailbox_settings


class YahooProvider(IMAPProvider):
    """Yahoo Mail provider (IMAP receive + SMTP send)."""

    name: ClassVar[str] = "yahoo"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Yahoo Mail email provider (IMAP + SMTP)"

    default_imap_host: ClassVar[str] = "imap.mail.yahoo.com"
    default_smtp_host: ClassVar[str] = "smtp.mail.yahoo.com"

    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        *AbstractEmailProvider.instance_settings,
        *mailbox_settings(
            "YAHOO",
            "imap",
            port="993",
            host=default_imap_host,
            smtp_host=default_smtp_host,
        ),
        *IMAP_INBOUND_SETTINGS,
    )

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "imap", "smtp", "messaging", "yahoo"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Yahoo"
