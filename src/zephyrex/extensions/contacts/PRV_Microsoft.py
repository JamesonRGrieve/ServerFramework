# SPDX-License-Identifier: AGPL-3.0-or-later
"""Microsoft 365 / Outlook contacts, through Microsoft Graph.

The instance's API key is a delegated access token with
``Contacts.ReadWrite`` (else ``MICROSOFT_GRAPH_TOKEN``); it works on the
signed-in user's default contacts folder. A search matches names that
start with the query. Phones are kept as home phones, the mobile number
first when Graph has one.
"""

from datetime import date
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.contacts.EXT_Contacts import AbstractContactsProvider
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

API_URL = "https://graph.microsoft.com/v1.0"
CONTACTS_URL = f"{API_URL}/me/contacts"


def odata_text(value: str) -> str:
    """``value`` as an OData string literal (a quote doubled)."""
    return "'" + value.replace("'", "''") + "'"


def graph_birthday(value: Optional[str]) -> Optional[str]:
    """A Graph birthday (``1990-04-12T00:00:00Z``) as an ISO date."""
    return value[:10] if value else None


class PRV_Microsoft_Contacts(AbstractContactsProvider):
    name: ClassVar[str] = "microsoft_contacts"
    friendly_name: ClassVar[str] = "Microsoft 365 Contacts"
    description: ClassVar[str] = "Microsoft 365 / Outlook contacts"
    _env: ClassVar[Dict[str, Any]] = {"MICROSOFT_GRAPH_TOKEN": ""}
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "api_key",
            "Delegated access token (Contacts.ReadWrite)",
            env="MICROSOFT_GRAPH_TOKEN",
            secret=True,
            field="api_key",
        ),
    )

    @classmethod
    def _contact_url(cls, contact_id: str) -> str:
        if not contact_id or "/" in contact_id:
            raise InvalidInputExternalError(
                f"{contact_id!r} is not a Microsoft contact id"
            )
        return f"{CONTACTS_URL}/{quote(contact_id, safe='')}"

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
        mobile = [found["mobilePhone"]] if found.get("mobilePhone") else []
        return cls.contact(
            contact_id=str(found.get("id", "")),
            name=str(found.get("displayName") or ""),
            emails=[
                e["address"]
                for e in found.get("emailAddresses", [])
                if e.get("address")
            ],
            phones=mobile
            + list(found.get("homePhones") or [])
            + list(found.get("businessPhones") or []),
            birthday=graph_birthday(found.get("birthday")),
            organization=str(found.get("companyName") or ""),
            notes=str(found.get("personalNotes") or ""),
        )

    @staticmethod
    def _body(fields: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if "name" in fields:
            body["displayName"] = fields["name"]
            body["givenName"] = fields["name"]
        if "emails" in fields:
            body["emailAddresses"] = [
                {"address": address} for address in fields["emails"]
            ]
        if "phones" in fields:
            body["homePhones"] = list(fields["phones"])
            body["mobilePhone"] = None
        if "birthday" in fields:
            born: date = fields["birthday"]
            body["birthday"] = f"{born.isoformat()}T00:00:00Z"
        if "organization" in fields:
            body["companyName"] = fields["organization"]
        if "notes" in fields:
            body["personalNotes"] = fields["notes"]
        return body

    @classmethod
    async def list_contacts(
        cls, instance: ProviderInstanceModel, query: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"$top": limit, "$orderby": "displayName"}
        if query is not None:
            params["$filter"] = f"startswith(displayName,{odata_text(query)})"
        found = await cls._call(instance, "GET", CONTACTS_URL, params=params)
        return [cls._contact(contact) for contact in found.get("value", [])]

    @classmethod
    async def get_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        return cls._contact(
            await cls._call(instance, "GET", cls._contact_url(contact_id))
        )

    @classmethod
    async def create_contact(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._contact(
            await cls._call(instance, "POST", CONTACTS_URL, json=cls._body(fields))
        )

    @classmethod
    async def update_contact(
        cls, instance: ProviderInstanceModel, contact_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return cls._contact(
            await cls._call(
                instance, "PATCH", cls._contact_url(contact_id), json=cls._body(changes)
            )
        )

    @classmethod
    async def delete_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        await cls._call(instance, "DELETE", cls._contact_url(contact_id))
        return {"id": contact_id, "deleted": True, "provider": cls.name}
