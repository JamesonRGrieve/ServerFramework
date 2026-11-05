# SPDX-License-Identifier: AGPL-3.0-or-later
"""SCIM 2.0 resources (RFC 7643) as this server keeps them: a framework
user is a SCIM User, a framework team a SCIM Group.

User: ``userName`` is the username, the primary email the account's
email (required; a ``userName`` that is an address stands in for a
missing one), ``name.givenName``/``familyName`` the first/last name,
``displayName``, ``active``, ``preferredLanguage`` (or ``locale``) the
language and ``timezone``; ``groups`` (read-only) lists the connection's
groups the user is in. Group: ``displayName`` is the team's name and
``members`` the connection's users in it. Attributes this server has no
place for are accepted and not kept.

A resource's version (its ETag) is a digest of everything it says but
``meta``, so it changes exactly when the resource does."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from zephyrex.extensions.scim_consumer.SCIMErrors import (
    INVALID_SYNTAX,
    INVALID_VALUE,
    bad_request,
)
from zephyrex.extensions.scim_consumer.SCIMFilter import GROUP_URN, USER_URN, get
from zephyrex.lib.DateTimeUtils import ensure_utc

SCIM_MEDIA_TYPE = "application/scim+json"
LIST_RESPONSE_URN = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SERVICE_PROVIDER_CONFIG_URN = (
    "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
)
RESOURCE_TYPE_URN = "urn:ietf:params:scim:schemas:core:2.0:ResourceType"
SCHEMA_URN = "urn:ietf:params:scim:schemas:core:2.0:Schema"

USER = "User"
GROUP = "Group"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200
# Attributes a request can never set: the server assigns them.
USER_READ_ONLY = frozenset({"id", "meta", "groups"})
GROUP_READ_ONLY = frozenset({"id", "meta"})
# Returned whatever ``attributes`` / ``excludedAttributes`` say.
ALWAYS_RETURNED = frozenset({"id", "schemas", "meta"})
TRUE_WORDS = frozenset({"true"})
FALSE_WORDS = frozenset({"false"})


@dataclass(frozen=True)
class UserFields:
    """A SCIM User read into the framework user's columns."""

    username: str
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    display_name: Optional[str]
    active: bool
    language: Optional[str]
    timezone: Optional[str]
    external_id: Optional[str]

    def columns(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name,
            "active": self.active,
            "language": self.language,
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class GroupFields:
    """A SCIM Group read into a team's name and its members' ids."""

    display_name: str
    external_id: Optional[str]
    member_ids: Tuple[str, ...]


def _text(payload: Mapping[str, Any], name: str) -> Optional[str]:
    value = get(payload, name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise bad_request(INVALID_VALUE, f"{name} is a string")
    stripped = value.strip()
    return stripped or None


def _boolean(value: Any, name: str) -> bool:
    """A boolean, also as the "True"/"False" strings Azure AD sends."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in TRUE_WORDS:
        return True
    if isinstance(value, str) and value.strip().lower() in FALSE_WORDS:
        return False
    raise bad_request(INVALID_VALUE, f"{name} is a boolean")


def _require_object(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise bad_request(INVALID_SYNTAX, "the body is a JSON object")
    return payload


def _primary_email(payload: Mapping[str, Any]) -> Optional[str]:
    emails = get(payload, "emails")
    if emails is None:
        return None
    if not isinstance(emails, list):
        raise bad_request(INVALID_VALUE, "emails is a list")
    chosen: Optional[str] = None
    for item in emails:
        if not isinstance(item, Mapping):
            raise bad_request(INVALID_VALUE, "each email is an object")
        value = _text(item, "value")
        if value is None:
            continue
        primary = get(item, "primary")
        if primary is not None and _boolean(primary, "emails.primary"):
            return value
        chosen = chosen or value
    return chosen


def parse_user(payload: Any) -> UserFields:
    """A User body (POST, PUT, or a patched resource) read into the
    framework's fields; 400 ``invalidValue`` when it cannot be."""
    body = _require_object(payload)
    username = _text(body, "userName")
    if username is None:
        raise bad_request(INVALID_VALUE, "userName is required")
    email = _primary_email(body)
    if email is None and "@" in username:
        email = username
    if email is None:
        raise bad_request(
            INVALID_VALUE, "an email is required (emails, or a userName that is one)"
        )
    name = get(body, "name")
    if name is not None and not isinstance(name, Mapping):
        raise bad_request(INVALID_VALUE, "name is an object")
    name = name or {}
    active = get(body, "active")
    return UserFields(
        username=username,
        email=email,
        first_name=_text(name, "givenName"),
        last_name=_text(name, "familyName"),
        display_name=_text(body, "displayName"),
        active=True if active is None else _boolean(active, "active"),
        language=_text(body, "preferredLanguage") or _text(body, "locale"),
        timezone=_text(body, "timezone"),
        external_id=_text(body, "externalId"),
    )


def parse_group(payload: Any) -> GroupFields:
    """A Group body read into a team name and member ids."""
    body = _require_object(payload)
    display_name = _text(body, "displayName")
    if display_name is None:
        raise bad_request(INVALID_VALUE, "displayName is required")
    members = get(body, "members")
    if members is None:
        members = []
    if not isinstance(members, list):
        raise bad_request(INVALID_VALUE, "members is a list")
    ids: List[str] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise bad_request(INVALID_VALUE, "each member is an object")
        member_type = _text(member, "type")
        if member_type is not None and member_type.lower() != USER.lower():
            raise bad_request(INVALID_VALUE, "members are users; groups do not nest")
        value = _text(member, "value")
        if value is None:
            raise bad_request(INVALID_VALUE, "each member has a value")
        if value not in ids:
            ids.append(value)
    return GroupFields(
        display_name=display_name,
        external_id=_text(body, "externalId"),
        member_ids=tuple(ids),
    )


def timestamp(value: Any) -> Optional[str]:
    if not isinstance(value, datetime):
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def version_of(resource: Mapping[str, Any]) -> str:
    """The resource's weak ETag: a digest of all but its ``meta``."""
    content = {k: v for k, v in resource.items() if k != "meta"}
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f'W/"{digest[:32]}"'


def _with_meta(
    resource: Dict[str, Any],
    resource_type: str,
    row: Mapping[str, Any],
    location: str,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"resourceType": resource_type, "location": location}
    created = timestamp(row.get("created_at"))
    modified = timestamp(row.get("updated_at")) or created
    if created:
        meta["created"] = created
    if modified:
        meta["lastModified"] = modified
    meta["version"] = version_of(resource)
    resource["meta"] = meta
    return resource


@dataclass(frozen=True)
class Reference:
    """A member of a group, or a group a user is in."""

    id: str
    display: Optional[str]
    location: str


def _references(references: Iterable[Reference], kind: Optional[str]) -> List[Any]:
    rendered: List[Dict[str, Any]] = []
    for reference in sorted(references, key=lambda r: r.id):
        item: Dict[str, Any] = {"value": reference.id, "$ref": reference.location}
        if reference.display:
            item["display"] = reference.display
        if kind:
            item["type"] = kind
        rendered.append(item)
    return rendered


def user_resource(
    user: Mapping[str, Any],
    external_id: Optional[str],
    groups: Iterable[Reference],
    location: str,
) -> Dict[str, Any]:
    """A framework user (a row's columns) as a SCIM User."""
    resource: Dict[str, Any] = {"schemas": [USER_URN], "id": user["id"]}
    if external_id:
        resource["externalId"] = external_id
    resource["userName"] = user.get("username") or user.get("email")
    name = {
        key: value
        for key, value in (
            ("givenName", user.get("first_name")),
            ("familyName", user.get("last_name")),
        )
        if value
    }
    formatted = " ".join(
        v for v in (user.get("first_name"), user.get("last_name")) if v
    )
    if formatted:
        name["formatted"] = formatted
    if name:
        resource["name"] = name
    if user.get("display_name"):
        resource["displayName"] = user["display_name"]
    if user.get("email"):
        resource["emails"] = [{"value": user["email"], "type": "work", "primary": True}]
    resource["active"] = bool(user.get("active", True))
    if user.get("language"):
        resource["preferredLanguage"] = user["language"]
    if user.get("timezone"):
        resource["timezone"] = user["timezone"]
    listed = _references(groups, None)
    if listed:
        resource["groups"] = listed
    return _with_meta(resource, USER, user, location)


def group_resource(
    team: Mapping[str, Any],
    external_id: Optional[str],
    members: Iterable[Reference],
    location: str,
) -> Dict[str, Any]:
    """A framework team as a SCIM Group."""
    resource: Dict[str, Any] = {"schemas": [GROUP_URN], "id": team["id"]}
    if external_id:
        resource["externalId"] = external_id
    resource["displayName"] = team.get("name")
    resource["members"] = _references(members, USER)
    return _with_meta(resource, GROUP, team, location)


def _names(raw: Optional[str]) -> Optional[frozenset[str]]:
    if raw is None or not raw.strip():
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def project(
    resource: Dict[str, Any],
    attributes: Optional[str],
    excluded_attributes: Optional[str],
) -> Dict[str, Any]:
    """``resource`` cut to the top-level ``attributes`` asked for, or
    without the ``excludedAttributes`` (RFC 7644 §3.4.2.5)."""
    wanted, unwanted = _names(attributes), _names(excluded_attributes)

    def top(name: str) -> str:
        return name.rsplit(":", 1)[-1].split(".", 1)[0]

    if wanted is not None:
        tops = {top(name) for name in wanted}
        return {
            k: v
            for k, v in resource.items()
            if k.lower() in tops or k in ALWAYS_RETURNED
        }
    if unwanted is not None:
        drop = {top(name) for name in unwanted} - ALWAYS_RETURNED
        return {k: v for k, v in resource.items() if k.lower() not in drop}
    return resource


def page_bounds(start_index: Optional[str], count: Optional[str]) -> Tuple[int, int]:
    """``startIndex`` (1-based, below 1 means 1) and ``count`` (capped,
    below 0 means 0) from the query string (RFC 7644 §3.4.2.4)."""

    def number(raw: Optional[str], name: str, default: int) -> int:
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            raise bad_request(INVALID_VALUE, f"{name} is an integer") from None

    start = max(1, number(start_index, "startIndex", 1))
    size = min(MAX_PAGE_SIZE, max(0, number(count, "count", DEFAULT_PAGE_SIZE)))
    return start, size


def list_response(
    resources: Sequence[Dict[str, Any]], start: int, size: int
) -> Dict[str, Any]:
    page = list(resources[start - 1 : start - 1 + size])
    return {
        "schemas": [LIST_RESPONSE_URN],
        "totalResults": len(resources),
        "startIndex": start,
        "itemsPerPage": len(page),
        "Resources": page,
    }


def etag_matches(header: Optional[str], version: str) -> bool:
    """Whether an ``If-Match``/``If-None-Match`` value names ``version``
    (``*`` names any); weak and strong forms compare alike."""
    if header is None:
        return False

    def bare(tag: str) -> str:
        tag = tag.strip()
        if tag[:2].upper() == "W/":
            tag = tag[2:]
        return tag.strip('"')

    tags = [bare(tag) for tag in header.split(",") if tag.strip()]
    return "*" in tags or bare(version) in tags


def service_provider_config(location: str) -> Dict[str, Any]:
    return {
        "schemas": [SERVICE_PROVIDER_CONFIG_URN],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": MAX_PAGE_SIZE},
        "changePassword": {"supported": False},
        "sort": {"supported": True},
        "etag": {"supported": True},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer token",
                "description": "The connection's token, as Authorization: Bearer",
                "specUri": "https://www.rfc-editor.org/rfc/rfc6750",
                "primary": True,
            }
        ],
        "meta": {"resourceType": "ServiceProviderConfig", "location": location},
    }


def resource_types(base: str) -> List[Dict[str, Any]]:
    return [
        {
            "schemas": [RESOURCE_TYPE_URN],
            "id": USER,
            "name": USER,
            "endpoint": "/Users",
            "description": "User accounts",
            "schema": USER_URN,
            "meta": {"resourceType": "ResourceType", "location": f"{base}/{USER}"},
        },
        {
            "schemas": [RESOURCE_TYPE_URN],
            "id": GROUP,
            "name": GROUP,
            "endpoint": "/Groups",
            "description": "Teams",
            "schema": GROUP_URN,
            "meta": {"resourceType": "ResourceType", "location": f"{base}/{GROUP}"},
        },
    ]


def _attribute(
    name: str,
    kind: str = "string",
    *,
    required: bool = False,
    multi_valued: bool = False,
    mutability: str = "readWrite",
    uniqueness: str = "none",
    case_exact: bool = False,
    sub_attributes: Sequence[Dict[str, Any]] = (),
    description: str = "",
) -> Dict[str, Any]:
    attribute: Dict[str, Any] = {
        "name": name,
        "type": kind,
        "multiValued": multi_valued,
        "description": description,
        "required": required,
        "caseExact": case_exact,
        "mutability": mutability,
        "returned": "default",
        "uniqueness": uniqueness,
    }
    if sub_attributes:
        attribute["subAttributes"] = list(sub_attributes)
    return attribute


def _reference_attribute(
    name: str, mutability: str, description: str
) -> Dict[str, Any]:
    return _attribute(
        name,
        "complex",
        multi_valued=True,
        mutability=mutability,
        description=description,
        sub_attributes=(
            _attribute("value", case_exact=True, mutability=mutability),
            _attribute("$ref", "reference", case_exact=True, mutability=mutability),
            _attribute("display", mutability="readOnly"),
            _attribute("type", mutability=mutability),
        ),
    )


def schemas(base: str) -> List[Dict[str, Any]]:
    user_attributes = [
        _attribute(
            "userName",
            required=True,
            uniqueness="server",
            description="The username, unique on this server",
        ),
        _attribute(
            "name",
            "complex",
            sub_attributes=(
                _attribute("formatted", mutability="readOnly"),
                _attribute("givenName"),
                _attribute("familyName"),
            ),
        ),
        _attribute("displayName"),
        _attribute(
            "emails",
            "complex",
            multi_valued=True,
            description="The primary address is the account's email",
            sub_attributes=(
                _attribute("value"),
                _attribute("type"),
                _attribute("primary", "boolean"),
            ),
        ),
        _attribute("active", "boolean"),
        _attribute("preferredLanguage"),
        _attribute("timezone"),
        _reference_attribute("groups", "readOnly", "The groups the user is in"),
    ]
    group_attributes = [
        _attribute("displayName", required=True),
        _reference_attribute("members", "readWrite", "The group's users"),
    ]
    return [
        {
            "schemas": [SCHEMA_URN],
            "id": USER_URN,
            "name": USER,
            "description": "User account",
            "attributes": user_attributes,
            "meta": {"resourceType": "Schema", "location": f"{base}/{USER_URN}"},
        },
        {
            "schemas": [SCHEMA_URN],
            "id": GROUP_URN,
            "name": GROUP,
            "description": "Team",
            "attributes": group_attributes,
            "meta": {"resourceType": "Schema", "location": f"{base}/{GROUP_URN}"},
        },
    ]
