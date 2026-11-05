# SPDX-License-Identifier: AGPL-3.0-or-later
"""A downstream SCIM 2.0 service provider (RFC 7643/7644): Slack, Zoom,
GitHub Enterprise, or any server that takes users and groups over SCIM.

Each instance is one service provider: ``base_url`` is its SCIM base (the
URL ``/Users`` hangs off) and its bearer token is the instance's API key,
write-only. Every call passes the SSRF guard (``cls.http()``), so a
service provider on a private network must be named in
``EGRESS_ALLOWED_HOSTS``.

This module speaks the protocol and nothing else: requests are
``application/scim+json``, ETags travel as ``If-Match`` when the service
provider says it supports them, a 429 is waited out for as long as its
``Retry-After`` says (within a bound), and other refusals come back as the
framework's typed errors (401/403 as ``AuthExternalError``). What to push
is ``SCIMSync``'s business.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import quote

from pydantic import BaseModel as RouteModel
from pydantic import ConfigDict, Field, ValidationError

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticProvider,
    InstanceSetting,
)
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
    RateLimitExternalError,
    TransientExternalError,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SCIM_MEDIA_TYPE = "application/scim+json"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ENDPOINTS = {"User": "Users", "Group": "Groups"}

SCIM_REQUEST_TIMEOUT_SECONDS = 30.0
# How many times one request is retried after a 429, and the longest
# Retry-After honoured; a longer one is surfaced for the next run.
RATE_LIMIT_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 60.0
RATE_LIMIT_BACKOFF_SECONDS = 1.0
PAGE_SIZE = 100
MAX_PAGES = 500

DELETE_MODES = ("deactivate", "delete")
USER_NAME_SOURCES = ("email", "username")
TRUE_WORDS = ("1", "true", "yes", "on")


def scim_string(value: str) -> str:
    """``value`` as a SCIM filter string literal: a JSON string (RFC 7644
    3.4.2.2), so a quote or backslash in it cannot end the literal and
    smuggle in more filter."""
    return json.dumps(value, ensure_ascii=False)


def equality_filter(attribute: str, value: str) -> str:
    return f"{attribute} eq {scim_string(value)}"


def resource_path(resource_type: str, remote_id: Optional[str] = None) -> str:
    """The path of ``resource_type``'s endpoint, or of one resource in it.
    An id is one encoded path segment: a service provider's id cannot walk
    the request to another endpoint."""
    endpoint = ENDPOINTS[resource_type]
    if remote_id is None:
        return endpoint
    if not remote_id or remote_id in (".", ".."):
        raise InvalidInputExternalError(f"{remote_id!r} is not a SCIM id")
    return f"{endpoint}/{quote(remote_id, safe='')}"


class _Supported(RouteModel):
    model_config = ConfigDict(extra="ignore")
    supported: bool = False


class _FilterSupport(_Supported):
    maxResults: Optional[int] = None


class ServiceProviderConfig(RouteModel):
    """What the service provider says it can do (RFC 7643 section 5);
    anything it leaves out it is taken not to do."""

    model_config = ConfigDict(extra="ignore")
    patch: _Supported = Field(default_factory=_Supported)
    etag: _Supported = Field(default_factory=_Supported)
    filter: _FilterSupport = Field(default_factory=_FilterSupport)

    @property
    def page_size(self) -> int:
        most = self.filter.maxResults
        return min(PAGE_SIZE, most) if most and most > 0 else PAGE_SIZE


class _Meta(RouteModel):
    model_config = ConfigDict(extra="ignore")
    version: Optional[str] = None


class _Resource(RouteModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(..., min_length=1)
    meta: Optional[_Meta] = None


class _ListResponse(RouteModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    totalResults: int = Field(..., ge=0)
    resources: List[Dict[str, Any]] = Field(default_factory=list, alias="Resources")


@dataclass(frozen=True)
class RemoteResource:
    """A resource as the service provider answered with it: its id, its
    version (the ETag) and the whole body."""

    id: str
    etag: Optional[str]
    body: Dict[str, Any] = field(default_factory=dict)


def remote_resource(body: Any, etag_header: Optional[str]) -> RemoteResource:
    try:
        parsed = _Resource.model_validate(body)
    except ValidationError as exc:
        raise TransientExternalError(
            "the service provider answered with no resource id", cause=exc
        ) from exc
    version = parsed.meta.version if parsed.meta else None
    return RemoteResource(id=parsed.id, etag=etag_header or version, body=dict(body))


def patch_body(operations: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {"schemas": [PATCH_SCHEMA], "Operations": [dict(op) for op in operations]}


class PRV_SCIM_SCIMProvider(AbstractStaticProvider):
    name: ClassVar[str] = "scim_target"
    friendly_name: ClassVar[str] = "SCIM 2.0 service provider"
    description: ClassVar[str] = (
        "A downstream SCIM 2.0 service provider that receives users and groups"
    )
    _abilities: ClassVar[Set[str]] = set()
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = SCIM_REQUEST_TIMEOUT_SECONDS
    instance_settings: ClassVar[Tuple[InstanceSetting, ...]] = (
        InstanceSetting("base_url", "SCIM base URL (https://api.example.com/scim/v2)"),
        InstanceSetting(
            "api_key",
            "Bearer token the service provider issued",
            secret=True,
            field="api_key",
        ),
        InstanceSetting(
            "delete_mode",
            "What a removed user becomes: deactivate (active=false) or delete",
            default="deactivate",
        ),
        InstanceSetting(
            "user_name",
            "Which local field is the SCIM userName: email or username",
            default="email",
        ),
        InstanceSetting(
            "team_id",
            "Only this team (and its sub-teams) and their members; empty for all",
        ),
        InstanceSetting("push_groups", "Push teams as SCIM groups", default="true"),
    )

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def required(cls, instance: ProviderInstanceModel, key: str) -> str:
        value = (cls.setting(instance, key) or "").strip()
        if not value:
            raise PermanentExternalError(
                f"SCIM target {key} not configured", provider=cls.name
            )
        return value

    @classmethod
    def choice(
        cls, instance: ProviderInstanceModel, key: str, allowed: Sequence[str]
    ) -> str:
        value = (cls.setting(instance, key) or "").strip().lower()
        if value not in allowed:
            raise PermanentExternalError(
                f"SCIM target {key} is one of {', '.join(allowed)}, not {value!r}",
                provider=cls.name,
            )
        return value

    @classmethod
    def flag(cls, instance: ProviderInstanceModel, key: str) -> bool:
        return (cls.setting(instance, key) or "").strip().lower() in TRUE_WORDS

    @classmethod
    def url(cls, instance: ProviderInstanceModel, path: str) -> str:
        return f"{cls.required(instance, 'base_url').rstrip('/')}/{path}"

    @classmethod
    async def call(
        cls,
        instance: ProviderInstanceModel,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        if_match: Optional[str] = None,
    ) -> Tuple[Any, Optional[str]]:
        """One SCIM request: the decoded answer (None for an empty one) and
        its ETag header. A 429 is retried after its Retry-After (or a
        growing backoff), up to ``RATE_LIMIT_RETRIES`` times."""
        headers = {
            "Authorization": f"Bearer {cls.required(instance, 'api_key')}",
            "Accept": SCIM_MEDIA_TYPE,
        }
        content: Optional[bytes] = None
        if body is not None:
            headers["Content-Type"] = SCIM_MEDIA_TYPE
            content = json.dumps(body).encode()
        if if_match:
            headers["If-Match"] = if_match
        url = cls.url(instance, path)
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = await cls.http().request(
                    method,
                    url,
                    raw=True,
                    params=params,
                    headers=headers,
                    content=content,
                )
            except RateLimitExternalError as exc:
                wait = exc.retry_after_seconds
                if wait is None:
                    wait = RATE_LIMIT_BACKOFF_SECONDS * 2**attempt
                if attempt == RATE_LIMIT_RETRIES or wait > MAX_RETRY_AFTER_SECONDS:
                    raise
                await asyncio.sleep(wait)
                continue
            if not response.content:
                return None, response.headers.get("ETag")
            try:
                decoded = response.json()
            except ValueError as exc:
                raise TransientExternalError(
                    "the service provider answered with something not JSON",
                    provider=cls.name,
                    cause=exc,
                ) from exc
            return decoded, response.headers.get("ETag")
        raise AssertionError("unreachable: the last attempt returns or raises")

    @classmethod
    async def service_provider_config(
        cls, instance: ProviderInstanceModel
    ) -> ServiceProviderConfig:
        """What the service provider supports. One that has no
        ServiceProviderConfig is taken to support none of the options."""
        try:
            body, _ = await cls.call(instance, "GET", "ServiceProviderConfig")
        except InvalidInputExternalError as exc:
            if exc.upstream_status == 404:
                return ServiceProviderConfig()
            raise
        try:
            return ServiceProviderConfig.model_validate(body or {})
        except ValidationError as exc:
            raise TransientExternalError(
                "the service provider's ServiceProviderConfig is malformed",
                provider=cls.name,
                cause=exc,
            ) from exc

    @classmethod
    async def search(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        filter_expression: Optional[str],
        page_size: int,
        limit: Optional[int] = None,
    ) -> List[RemoteResource]:
        """The resources matching ``filter_expression`` (all, when None),
        page by page (``startIndex``/``count``) until ``totalResults`` or
        ``limit`` are reached."""
        found: List[RemoteResource] = []
        start = 1
        for _ in range(MAX_PAGES):
            params: Dict[str, Any] = {"startIndex": start, "count": page_size}
            if filter_expression is not None:
                params["filter"] = filter_expression
            body, _ = await cls.call(
                instance, "GET", resource_path(resource_type), params=params
            )
            try:
                page = _ListResponse.model_validate(body or {})
            except ValidationError as exc:
                raise TransientExternalError(
                    f"the service provider's {resource_type} list is malformed",
                    provider=cls.name,
                    cause=exc,
                ) from exc
            found.extend(remote_resource(item, None) for item in page.resources)
            start += len(page.resources)
            if (
                not page.resources
                or start > page.totalResults
                or (limit is not None and len(found) >= limit)
            ):
                return found[:limit] if limit is not None else found
        raise TransientExternalError(
            f"the service provider's {resource_type} list ran past {MAX_PAGES} pages",
            provider=cls.name,
        )

    @classmethod
    async def find(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        attribute: str,
        value: str,
    ) -> Optional[RemoteResource]:
        """The resource whose ``attribute`` equals ``value``, if any."""
        found = await cls.search(
            instance, resource_type, equality_filter(attribute, value), 1, limit=1
        )
        return found[0] if found else None

    @classmethod
    async def get(
        cls, instance: ProviderInstanceModel, resource_type: str, remote_id: str
    ) -> RemoteResource:
        body, etag = await cls.call(
            instance, "GET", resource_path(resource_type, remote_id)
        )
        return remote_resource(body, etag)

    @classmethod
    async def create(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        resource: Mapping[str, Any],
    ) -> RemoteResource:
        body, etag = await cls.call(
            instance, "POST", resource_path(resource_type), body=resource
        )
        return remote_resource(body, etag)

    @classmethod
    async def replace(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        remote_id: str,
        resource: Mapping[str, Any],
        if_match: Optional[str],
    ) -> RemoteResource:
        body, etag = await cls.call(
            instance,
            "PUT",
            resource_path(resource_type, remote_id),
            body={**resource, "id": remote_id},
            if_match=if_match,
        )
        return remote_resource(body, etag)

    @classmethod
    async def patch(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        remote_id: str,
        operations: Sequence[Mapping[str, Any]],
        if_match: Optional[str],
    ) -> Optional[RemoteResource]:
        """The resource after the patch. A 204 with no body (RFC 7644
        3.5.2) gives only its new version, when the ETag header says; None
        when it says nothing."""
        body, etag = await cls.call(
            instance,
            "PATCH",
            resource_path(resource_type, remote_id),
            body=patch_body(operations),
            if_match=if_match,
        )
        if body:
            return remote_resource(body, etag)
        return RemoteResource(id=remote_id, etag=etag) if etag else None

    @classmethod
    async def delete(
        cls,
        instance: ProviderInstanceModel,
        resource_type: str,
        remote_id: str,
        if_match: Optional[str],
    ) -> None:
        await cls.call(
            instance,
            "DELETE",
            resource_path(resource_type, remote_id),
            if_match=if_match,
        )
