# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Contacts, through the People API v1.

The instance's API key is an OAuth access token with the ``contacts``
scope (else ``GOOGLE_CONTACTS_TOKEN``). A contact's id is its resource
name without ``people/`` (``c1234``). A change is guarded by the contact's
etag, read just before it, so a change made meanwhile is refused rather
than overwritten.
"""

from datetime import date
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.contacts.EXT_Contacts import AbstractContactsProvider
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://people.googleapis.com/v1"
PERSON_FIELDS = "names,emailAddresses,phoneNumbers,birthdays,organizations,biographies"
# The People API's own cap on a search's page.
SEARCH_PAGE_LIMIT = 30
# Which Person fields each of our fields writes, for an update's mask.
UPDATE_FIELDS = {
    "name": "names",
    "emails": "emailAddresses",
    "phones": "phoneNumbers",
    "birthday": "birthdays",
    "organization": "organizations",
    "notes": "biographies",
}


def google_birthday(found: Mapping[str, Any]) -> Optional[str]:
    """A People API birthday as an ISO date, ``--MM-DD`` without a year."""
    day = found.get("date") or {}
    if not day.get("month") or not day.get("day"):
        return None
    year = day.get("year")
    month_day = f"{day['month']:02d}-{day['day']:02d}"
    return f"{year:04d}-{month_day}" if year else f"--{month_day}"


class PRV_Google_Contacts(AbstractContactsProvider):
    name: ClassVar[str] = "google_contacts"
    friendly_name: ClassVar[str] = "Google Contacts"
    description: ClassVar[str] = "Google Contacts"
    _env: ClassVar[Dict[str, Any]] = {"GOOGLE_CONTACTS_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "OAuth access token (contacts)",
            env="GOOGLE_CONTACTS_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _person_url(cls, contact_id: str) -> str:
        if not contact_id or "/" in contact_id:
            raise InvalidInputExternalError(
                f"{contact_id!r} is not a Google contact id"
            )
        return f"{API_URL}/people/{quote(contact_id, safe='')}"

    @classmethod
    async def _call(
        cls, instance: ProviderInstanceModel, method: str, url: str, **kwargs: Any
    ) -> Any:
        return await cls.http().request(
            method,
            url,
            headers={"Authorization": f"Bearer {cls.token(instance)}"},
            **kwargs,
        )

    @classmethod
    def _contact(cls, found: Mapping[str, Any]) -> Dict[str, Any]:
        names = found.get("names") or [{}]
        organizations = found.get("organizations") or [{}]
        biographies = found.get("biographies") or [{}]
        birthdays = [
            b for b in (google_birthday(b) for b in found.get("birthdays", [])) if b
        ]
        contact_id = str(found.get("resourceName", "")).removeprefix("people/")
        return cls.contact(
            contact_id=contact_id,
            name=str(
                names[0].get("displayName") or names[0].get("unstructuredName") or ""
            ),
            emails=[
                e["value"] for e in found.get("emailAddresses", []) if e.get("value")
            ],
            phones=[
                p["value"] for p in found.get("phoneNumbers", []) if p.get("value")
            ],
            birthday=birthdays[0] if birthdays else None,
            organization=str(organizations[0].get("name", "")),
            notes=str(biographies[0].get("value", "")),
            url=(
                f"https://contacts.google.com/person/{contact_id}" if contact_id else ""
            ),
        )

    @staticmethod
    def _body(fields: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if "name" in fields:
            body["names"] = [{"unstructuredName": fields["name"]}]
        if "emails" in fields:
            body["emailAddresses"] = [
                {"value": address} for address in fields["emails"]
            ]
        if "phones" in fields:
            body["phoneNumbers"] = [{"value": number} for number in fields["phones"]]
        if "birthday" in fields:
            born: date = fields["birthday"]
            body["birthdays"] = [
                {"date": {"year": born.year, "month": born.month, "day": born.day}}
            ]
        if "organization" in fields:
            body["organizations"] = [{"name": fields["organization"]}]
        if "notes" in fields:
            body["biographies"] = [
                {"value": fields["notes"], "contentType": "TEXT_PLAIN"}
            ]
        return body

    @classmethod
    async def list_contacts(
        cls, instance: ProviderInstanceModel, query: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        if query is None:
            found = await cls._call(
                instance,
                "GET",
                f"{API_URL}/people/me/connections",
                params={
                    "personFields": PERSON_FIELDS,
                    "pageSize": limit,
                    "sortOrder": "FIRST_NAME_ASCENDING",
                },
            )
            return [cls._contact(person) for person in found.get("connections", [])]
        found = await cls._call(
            instance,
            "GET",
            f"{API_URL}/people:searchContacts",
            params={
                "query": query,
                "readMask": PERSON_FIELDS,
                "pageSize": min(limit, SEARCH_PAGE_LIMIT),
            },
        )
        return [
            cls._contact(result["person"])
            for result in found.get("results", [])
            if "person" in result
        ]

    @classmethod
    async def get_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        return cls._contact(
            await cls._call(
                instance,
                "GET",
                cls._person_url(contact_id),
                params={"personFields": PERSON_FIELDS},
            )
        )

    @classmethod
    async def create_contact(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._contact(
            await cls._call(
                instance,
                "POST",
                f"{API_URL}/people:createContact",
                params={"personFields": PERSON_FIELDS},
                json=cls._body(fields),
            )
        )

    @classmethod
    async def update_contact(
        cls, instance: ProviderInstanceModel, contact_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = cls._person_url(contact_id)
        current = await cls._call(
            instance, "GET", url, params={"personFields": PERSON_FIELDS}
        )
        return cls._contact(
            await cls._call(
                instance,
                "PATCH",
                f"{url}:updateContact",
                params={
                    "updatePersonFields": ",".join(
                        UPDATE_FIELDS[key] for key in changes
                    ),
                    "personFields": PERSON_FIELDS,
                },
                json={**cls._body(changes), "etag": current.get("etag")},
            )
        )

    @classmethod
    async def delete_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        await cls._call(
            instance, "DELETE", f"{cls._person_url(contact_id)}:deleteContact"
        )
        return {"id": contact_id, "deleted": True, "provider": cls.name}
