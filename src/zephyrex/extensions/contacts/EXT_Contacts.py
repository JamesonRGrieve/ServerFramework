# SPDX-License-Identifier: AGPL-3.0-or-later
"""Address books: list, search, read, create, change and delete contacts on
Google Contacts (People API), Microsoft 365 / Outlook (Microsoft Graph)
and any CardDAV address book (iCloud, Fastmail, Nextcloud, …).

A contact belongs to one address book account, so every ability names its
``provider`` and runs on that provider's instances (one instance per
account). Providers answer a contact as ``{id, name, emails, phones,
birthday, organization, notes, url, provider}``, ``birthday`` an ISO date
(``--MM-DD`` when the year is unknown).
"""

from abc import abstractmethod
from datetime import date
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

CONTACTS_REQUEST_TIMEOUT_SECONDS = 20.0
DEFAULT_CONTACT_LIMIT = 50
MAX_CONTACT_LIMIT = 500


def contact_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_CONTACT_LIMIT:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_CONTACT_LIMIT}")
    return limit


def contact_fields(
    name: Optional[str],
    emails: Optional[List[str]],
    phones: Optional[List[str]],
    birthday: Optional[date],
    organization: Optional[str],
    notes: Optional[str],
) -> Dict[str, Any]:
    """The fields given, checked: a name is not blank, emails are email
    addresses, phones are not blank."""
    if name is not None and not name.strip():
        raise InvalidInputExternalError("a contact needs a name")
    for address in emails or []:
        if "@" not in address or any(c.isspace() for c in address):
            raise InvalidInputExternalError(f"{address!r} is not an email address")
    for number in phones or []:
        if not number.strip():
            raise InvalidInputExternalError("a phone number is not blank")
    fields = {
        "name": name.strip() if name is not None else None,
        "emails": emails,
        "phones": phones,
        "birthday": birthday,
        "organization": organization,
        "notes": notes,
    }
    return {key: value for key, value in fields.items() if value is not None}


    """An address book service; each instance is one account (one book)."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "list_contacts",
        "get_contact",
        "create_contact",
        "update_contact",
        "delete_contact",
    }
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = CONTACTS_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def token(cls, instance: ProviderInstanceModel) -> str:
        token = cls.setting(instance, "api_key")
        if not token:
            raise TransientExternalError(
                f"{cls.friendly_name} access token not configured", provider=cls.name
            )
        return str(token)

    @classmethod
    def contact(
        cls,
        *,
        contact_id: str,
        name: str,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        birthday: Optional[str] = None,
        organization: str = "",
        notes: str = "",
        url: str = "",
    ) -> Dict[str, Any]:
        return {
            "id": contact_id,
            "name": name,
            "emails": emails or [],
            "phones": phones or [],
            "birthday": birthday,
            "organization": organization,
            "notes": notes,
            "url": url,
            "provider": cls.name,
        }

    @classmethod
    @abstractmethod
    async def list_contacts(
        cls, instance: ProviderInstanceModel, query: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        """Contacts, by name; those whose name (or address) matches ``query``
        when given."""

    @classmethod
    @abstractmethod
    async def get_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]: ...

    @classmethod
    @abstractmethod
    async def create_contact(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """``fields``: name, and optionally emails, phones, birthday,
        organization and notes."""

    @classmethod
    @abstractmethod
    async def update_contact(
        cls, instance: ProviderInstanceModel, contact_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """``changes``: any of the fields ``create_contact`` takes; a list
        given replaces the contact's whole list."""

    @classmethod
    @abstractmethod
    async def delete_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]: ...

    @classmethod
    def services(cls) -> List[str]:
        return ["contacts"]


    name: ClassVar[str] = "contacts"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Contacts on Google Contacts, Microsoft 365 and any CardDAV address book"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set(AbstractContactsProvider._abilities)

    @classmethod
    @ability("list_contacts")
    async def list_contacts(
        cls,
        provider: str,
        query: Optional[str] = None,
        limit: int = DEFAULT_CONTACT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Contacts by name, or those matching ``query``."""
        wanted = query.strip() if query is not None else None
        result: List[Dict[str, Any]] = await cls.rotate_provider_for(
            provider, "list_contacts", wanted or None, contact_limit(limit)
        )
        return result

    @classmethod
    @ability("get_contact")
    async def get_contact(cls, provider: str, contact_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "get_contact", contact_id
        )
        return result

    @classmethod
    @ability("create_contact")
    async def create_contact(
        cls,
        provider: str,
        name: str,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        birthday: Optional[date] = None,
        organization: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        fields = contact_fields(name, emails, phones, birthday, organization, notes)
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "create_contact", fields
        )
        return result

    @classmethod
    @ability("update_contact")
    async def update_contact(
        cls,
        provider: str,
        contact_id: str,
        name: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        birthday: Optional[date] = None,
        organization: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Change a contact; a list given replaces its whole list."""
        changes = contact_fields(name, emails, phones, birthday, organization, notes)
        if not changes:
            raise InvalidInputExternalError("nothing to change")
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "update_contact", contact_id, changes
        )
        return result

    @classmethod
    @ability("delete_contact")
    async def delete_contact(cls, provider: str, contact_id: str) -> Dict[str, Any]:
        result: Dict[str, Any] = await cls.rotate_provider_for(
            provider, "delete_contact", contact_id
        )
        return result
