# SPDX-License-Identifier: AGPL-3.0-or-later
"""Any CardDAV address book (RFC 6352): iCloud, Fastmail, Nextcloud,
Radicale, Baïkal and the rest.

An instance is one address book collection: ``addressbook_url`` is the
collection's URL, signed in to with ``username`` and the API key as the
password (an app-specific password on iCloud and Fastmail). A contact's
id is its resource name in the collection (``<uid>.vcf``). A change is
guarded by the vCard's ETag, read just before it.
"""

import uuid
from datetime import date
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import vobject
from vobject.base import ParseError

from zephyrex.extensions.AbstractExtensionProvider import InstanceSetting
from zephyrex.extensions.contacts.EXT_Contacts import AbstractContactsProvider
from zephyrex.extensions.ExternalErrors import (
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.DAV import (
    CARDDAV,
    collection_url,
    multistatus_resources,
    resource_name,
    resource_url,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

PRODUCT_ID = "-//Zephyrex//Contacts//EN"


def addressbook_query(query: Optional[str], limit: int) -> str:
    """An addressbook-query REPORT for up to ``limit`` contacts; those whose
    name or address contains ``query`` when given."""
    match = ""
    if query is not None:
        text = escape(query)
        match = (
            '<C:filter test="anyof">'
            + "".join(
                f'<C:prop-filter name="{prop}"><C:text-match collation="i;unicode-casemap" '
                f'match-type="contains">{text}</C:text-match></C:prop-filter>'
                for prop in ("FN", "EMAIL")
            )
            + "</C:filter>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<C:addressbook-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">'
        "<D:prop><D:getetag/><C:address-data/></D:prop>"
        f"{match}<C:limit><C:nresults>{limit}</C:nresults></C:limit>"
        "</C:addressbook-query>"
    )


def _values(card: Any, key: str) -> List[str]:
    return [
        str(line.value).strip()
        for line in card.contents.get(key, [])
        if str(line.value).strip()
    ]


def _birthday(card: Any) -> Optional[str]:
    """A vCard BDAY as an ISO date: ``1990-04-12``, ``--04-12`` without a year."""
    found = _values(card, "bday")
    if not found:
        return None
    value = found[0]
    if value.startswith("--"):
        digits = value[2:].replace("-", "")
        return f"--{digits[:2]}-{digits[2:4]}" if len(digits) == 4 else None
    digits = value.replace("-", "")[:8]
    return (
        f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if len(digits) == 8 and digits.isdigit()
        else None
    )


def _organization(card: Any) -> str:
    lines = card.contents.get("org", [])
    if not lines:
        return ""
    value = lines[0].value
    return str(value[0] if isinstance(value, list) and value else value)


def _replace(
    card: Any, key: str, values: List[str], type_param: Optional[str] = None
) -> None:
    for line in list(card.contents.get(key, [])):
        card.remove(line)
    for value in values:
        line = card.add(key)
        line.value = value
        if type_param is not None:
            line.type_param = type_param


def apply_fields(card: Any, fields: Dict[str, Any]) -> None:
    """Write the contacts extension's ``fields`` onto a vCard."""
    if "name" in fields:
        _replace(card, "fn", [fields["name"]])
        for line in list(card.contents.get("n", [])):
            card.remove(line)
        card.add("n").value = vobject.vcard.Name(given=fields["name"])
    if "emails" in fields:
        _replace(card, "email", list(fields["emails"]), "INTERNET")
    if "phones" in fields:
        _replace(card, "tel", list(fields["phones"]), "VOICE")
    if "birthday" in fields:
        born: date = fields["birthday"]
        _replace(card, "bday", [born.isoformat()])
    if "organization" in fields:
        for line in list(card.contents.get("org", [])):
            card.remove(line)
        card.add("org").value = [fields["organization"]]
    if "notes" in fields:
        _replace(card, "note", [fields["notes"]])


def new_vcard(uid: str, fields: Dict[str, Any]) -> bytes:
    """A vCard 3.0 for ``fields``, with ``uid``."""
    card = vobject.vCard()
    card.add("version").value = "3.0"
    card.add("prodid").value = PRODUCT_ID
    card.add("uid").value = uid
    apply_fields(card, fields)
    return str(card.serialize()).encode()


def changed_vcard(text: str, changes: Dict[str, Any]) -> bytes:
    """``text`` with ``changes`` written onto it."""
    try:
        card = vobject.readOne(text)
    except (ParseError, ValueError) as exc:
        raise PermanentExternalError(f"the resource is not a vCard: {exc}") from exc
    apply_fields(card, changes)
    return str(card.serialize()).encode()


class PRV_CardDAV_Contacts(AbstractContactsProvider):
    name: ClassVar[str] = "carddav"
    friendly_name: ClassVar[str] = "CardDAV"
    description: ClassVar[str] = (
        "Any CardDAV address book: iCloud, Fastmail, Nextcloud, Radicale"
    )
    _env: ClassVar[Dict[str, Any]] = {
        "CARDDAV_ADDRESSBOOK_URL": "",
        "CARDDAV_USERNAME": "",
        "CARDDAV_PASSWORD": "",
    }
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting(
            "addressbook_url",
            "The address book collection's URL (ends in /)",
            env="CARDDAV_ADDRESSBOOK_URL",
        ),
        InstanceSetting("username", "Sign-in name", env="CARDDAV_USERNAME"),
        InstanceSetting(
            "api_key",
            "Password (an app-specific password on iCloud and Fastmail)",
            env="CARDDAV_PASSWORD",
            secret=True,
            field="api_key",
        ),
    )
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="vobject",
                friendly_name="vobject",
                semver=">=0.9.9",
                reason="Reading and writing CardDAV contacts (vCard)",
            ),
        ]
    )

    @classmethod
    def _collection(cls, instance: ProviderInstanceModel) -> str:
        url = str(cls.setting(instance, "addressbook_url") or "")
        if not url:
            raise TransientExternalError(
                "CardDAV address book URL not configured", provider=cls.name
            )
        return collection_url(url)

    @classmethod
    async def _call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        content: Optional[bytes] = None,
    ) -> Any:
        username = str(cls.setting(instance, "username") or "")
        return await cls.http().request(
            method,
            url,
            raw=True,
            headers=headers or {},
            content=content,
            auth=(username, cls.token(instance)),
        )

    @classmethod
    def _contacts(cls, found: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
        """Each contact in ``found`` (``(href, vCard)``), by name."""
        contacts: List[Dict[str, Any]] = []
        for href, data in found:
            for card in vobject.readComponents(data):
                contacts.append(
                    cls.contact(
                        contact_id=resource_name(href),
                        name=(_values(card, "fn") or [""])[0],
                        emails=_values(card, "email"),
                        phones=_values(card, "tel"),
                        birthday=_birthday(card),
                        organization=_organization(card),
                        notes=(_values(card, "note") or [""])[0],
                        url=(_values(card, "url") or [""])[0],
                    )
                )
        return sorted(contacts, key=lambda contact: str(contact["name"]).casefold())

    @classmethod
    async def list_contacts(
        cls, instance: ProviderInstanceModel, query: Optional[str], limit: int
    ) -> List[Dict[str, Any]]:
        response = await cls._call(
            instance,
            "REPORT",
            cls._collection(instance),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=addressbook_query(query, limit).encode(),
        )
        found = multistatus_resources(response.text, f"{CARDDAV}address-data")
        return cls._contacts(found)[:limit]

    @classmethod
    async def get_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        response = await cls._call(
            instance, "GET", resource_url(cls._collection(instance), contact_id)
        )
        return cls._contacts([(contact_id, response.text)])[0]

    @classmethod
    async def create_contact(
        cls, instance: ProviderInstanceModel, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        uid = str(uuid.uuid4())
        name = f"{uid}.vcf"
        body = new_vcard(uid, fields)
        await cls._call(
            instance,
            "PUT",
            resource_url(cls._collection(instance), name),
            headers={"Content-Type": "text/vcard; charset=utf-8", "If-None-Match": "*"},
            content=body,
        )
        return cls._contacts([(name, body.decode())])[0]

    @classmethod
    async def update_contact(
        cls, instance: ProviderInstanceModel, contact_id: str, changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        url = resource_url(cls._collection(instance), contact_id)
        current = await cls._call(instance, "GET", url)
        body = changed_vcard(current.text, changes)
        etag = current.headers.get("etag")
        await cls._call(
            instance,
            "PUT",
            url,
            # Guarded by the version read, so a change made meanwhile is not overwritten.
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                **({"If-Match": etag} if etag else {}),
            },
            content=body,
        )
        return cls._contacts([(contact_id, body.decode())])[0]

    @classmethod
    async def delete_contact(
        cls, instance: ProviderInstanceModel, contact_id: str
    ) -> Dict[str, Any]:
        await cls._call(
            instance, "DELETE", resource_url(cls._collection(instance), contact_id)
        )
        return {"id": contact_id, "deleted": True, "provider": cls.name}
