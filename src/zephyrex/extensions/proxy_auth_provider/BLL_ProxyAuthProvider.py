# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as an authenticating reverse proxy in front of the services
the operator declares.

``GET`` and ``DELETE /v1/proxy/<upstream>/<path>`` reach ``<path>`` (and
the query) below the upstream's base address, for a signed-in user
(session cookie, bearer token or API key, authenticated by the framework
as on any route) whose account is active and, when the upstream names
teams, who is a live member of one. The upstream gets the request with
this server's credentials and any forgeable identity headers removed, and
this server's identity headers added (``ProxyHeaders``), signed when the
upstream has a signing secret. Its answer streams back, status and
headers, redirects included.

Only upstreams ROOT declared exist (``PRV_Upstream``); a request names one
by name and a path below it, never an address. Bodies stream both ways
under the upstream's byte caps (a declared length over a cap is refused
before anything is sent; a stream that runs over is cut off), and its
timeout bounds connecting and every wait for bytes.

Only methods without a request body are proxied: the route layer reads
the body of a ``POST``/``PUT``/``PATCH`` as JSON before a route runs, and
the app's content negotiation transcodes or refuses non-JSON bodies, so
those requests could not reach an upstream unchanged.
"""

from datetime import datetime, timezone
from typing import Any, AsyncIterator, ClassVar, FrozenSet, List, Optional, Tuple

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from zephyrex.extensions.proxy_auth_provider.PRV_Upstream import (
    PRV_Upstream_ProxyAuthProvider,
    Upstream,
    UpstreamMisconfigured,
)
from zephyrex.extensions.proxy_auth_provider.ProxyHeaders import (
    Identity,
    RawHeaders,
    UnsafePath,
    client_response_headers,
    declared_length,
    header_value,
    identity_headers,
    upstream_request_headers,
    upstream_target,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.lib.ProviderHTTPClient import (
    ClientPolicy,
    SSRFGuardError,
    get_async_client,
    validate_outbound_url,
)
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.logic.BLL_Auth import TeamModel, UserModel, UserTeamModel
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType

PROXY_PREFIX = "/v1/proxy"
_TAG = "Proxy Auth"


class RequestTooLarge(Exception):
    """The client's body ran past the upstream's request cap."""


class ResponseTooLarge(Exception):
    """The upstream's body ran past its response cap; the answer is cut off."""


def live_user(model_registry: Any, user_id: str) -> Optional[UserModel]:
    """The user, unless deleted (a ROOT read includes deleted rows)."""
    UserDB = UserModel.DB(model_registry.DB.manager.Base)
    users: List[UserModel] = UserDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=user_id,
        filters=[UserDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=UserModel,
    )
    return users[0] if users else None


def live_team_ids(model_registry: Any, user_id: str) -> FrozenSet[str]:
    """The teams the user belongs to through an enabled, unexpired,
    undeleted membership of a team that is not deleted."""
    base = model_registry.DB.manager.Base
    UserTeamDB = UserTeamModel.DB(base)
    now = datetime.now(timezone.utc)
    memberships: List[UserTeamModel] = UserTeamDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        user_id=user_id,
        enabled=True,
        filters=[UserTeamDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=UserTeamModel,
    )
    member_of = [
        str(m.team_id)
        for m in memberships
        if m.expires_at is None or ensure_utc(m.expires_at) > now
    ]
    if not member_of:
        return frozenset()
    TeamDB = TeamModel.DB(base)
    teams: List[TeamModel] = TeamDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[TeamDB.id.in_(member_of), TeamDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=TeamModel,
    )
    return frozenset(str(team.id) for team in teams)


def identity_of(user: UserModel, teams: FrozenSet[str]) -> Identity:
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return Identity(
        user_id=str(user.id),
        username=user.username,
        email=user.email,
        name=header_value(user.display_name) or header_value(full_name),
        groups=tuple(sorted(teams)),
    )


def declared_upstream(
    model_registry: Any, name: str
) -> Optional[ProviderInstanceModel]:
    """The enabled, undeleted upstream instance ROOT created under
    ``name``, or None."""
    root_id = env("ROOT_ID")
    provider = ProviderManager(model_registry=model_registry, requester_id=root_id).get(
        name=PRV_Upstream_ProxyAuthProvider.name
    )
    InstanceDB = ProviderInstanceModel.DB(model_registry.DB.manager.Base)
    found: List[ProviderInstanceModel] = InstanceDB.list(
        requester_id=root_id,
        model_registry=model_registry,
        provider_id=provider.id,
        name=name,
        enabled=True,
        created_by_user_id=root_id,
        filters=[InstanceDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=ProviderInstanceModel,
    )
    return found[0] if found else None


def raw_rest_and_query(request: Request, upstream_name: str) -> Tuple[str, str]:
    """The path below ``/v1/proxy/<upstream>/`` exactly as the client sent
    it (still percent-encoded, with any ``.json``-style suffix the content
    negotiation strips from the routed path), and the raw query."""
    raw_path = request.scope.get("raw_path") or request.url.path.encode("utf-8")
    path = raw_path.decode("latin-1")
    remainder = (
        path[len(PROXY_PREFIX) + 1 :] if path.startswith(PROXY_PREFIX + "/") else ""
    )
    _, _, rest = remainder.partition("/")
    if not remainder.partition("/")[0]:
        raise HTTPException(status_code=404, detail=f"No upstream {upstream_name!r}")
    query = (request.scope.get("query_string") or b"").decode("latin-1")
    return rest, query


def forwarding_headers(request: Request, public_prefix: str) -> RawHeaders:
    """Where the request came from, as this server saw it."""
    found = [
        ("X-Forwarded-For", request.client.host if request.client else None),
        ("X-Forwarded-Proto", request.url.scheme),
        ("X-Forwarded-Host", request.headers.get("host")),
        ("X-Forwarded-Prefix", public_prefix),
    ]
    checked = [(name, header_value(value)) for name, value in found]
    return [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in checked
        if value is not None
    ]


async def capped_body(request: Request, limit: int) -> AsyncIterator[bytes]:
    sent = 0
    async for chunk in request.stream():
        sent += len(chunk)
        if sent > limit:
            raise RequestTooLarge(f"the request body is over {limit} bytes")
        if chunk:
            yield chunk


async def relayed(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    """The upstream's body as it arrives, cut off past ``limit`` bytes."""
    sent = 0
    try:
        async for chunk in response.aiter_raw():
            sent += len(chunk)
            if sent > limit:
                raise ResponseTooLarge(f"the upstream's body is over {limit} bytes")
            yield chunk
    finally:
        await response.aclose()


def carries_body(raw: RawHeaders) -> bool:
    length = declared_length(raw)
    chunked = any(
        name.decode("latin-1").lower() == "transfer-encoding" for name, _ in raw
    )
    return chunked or bool(length)


class ProxyAuthManager(AbstractBLLManager, RouterMixin):
    """Proxy routes only: the upstreams are provider instances."""

    prefix: ClassVar[Optional[str]] = PROXY_PREFIX
    tags: ClassVar[Optional[List[str]]] = [_TAG]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def upstream_for(self, name: str, teams: FrozenSet[str]) -> Upstream:
        instance = declared_upstream(self.model_registry, name)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"No upstream {name!r}")
        try:
            upstream = PRV_Upstream_ProxyAuthProvider.upstream(instance)
        except UpstreamMisconfigured as exc:
            logger.error("proxy_auth_provider: upstream %s: %s", name, exc)
            raise HTTPException(
                status_code=502, detail="The upstream is misconfigured"
            ) from None
        if not upstream.admits(teams):
            raise HTTPException(status_code=403, detail="Not a member of its teams")
        return upstream

    async def forward(self, request: Request, upstream_name: str) -> Response:
        user = live_user(self.model_registry, str(self.requester.id))
        if user is None or user.active is False:
            raise HTTPException(status_code=403, detail="This account is disabled")
        teams = live_team_ids(self.model_registry, str(user.id))
        upstream = self.upstream_for(upstream_name, teams)
        rest, query = raw_rest_and_query(request, upstream_name)
        try:
            url, target = upstream_target(upstream.base_url, rest, query)
        except UnsafePath as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        raw = list(request.headers.raw)
        length = declared_length(raw)
        if length is not None and length > upstream.max_request_bytes:
            raise HTTPException(status_code=413, detail="The request body is too large")
        public_prefix = f"{PROXY_PREFIX}/{upstream_name}"
        headers = (
            upstream_request_headers(raw)
            + forwarding_headers(request, public_prefix)
            + identity_headers(
                identity_of(user, teams),
                request.method,
                target,
                upstream.signing_secret,
            )
        )
        client = get_async_client(ClientPolicy(timeout=upstream.timeout_seconds))
        try:
            validate_outbound_url(url)
            answer = await client.send(
                client.build_request(
                    request.method,
                    url,
                    headers=headers,
                    content=(
                        capped_body(request, upstream.max_request_bytes)
                        if carries_body(raw)
                        else None
                    ),
                ),
                stream=True,
            )
        except SSRFGuardError as exc:
            logger.error(
                "proxy_auth_provider: upstream %s refused: %s", upstream_name, exc
            )
            raise HTTPException(
                status_code=502, detail="The upstream is not an allowed destination"
            ) from None
        except RequestTooLarge:
            raise HTTPException(
                status_code=413, detail="The request body is too large"
            ) from None
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504, detail="The upstream did not answer in time"
            ) from None
        except httpx.RequestError as exc:
            logger.warning(
                "proxy_auth_provider: upstream %s unreachable: %s",
                upstream_name,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=502, detail="The upstream could not be reached"
            ) from None
        answered = list(answer.headers.raw)
        answered_length = declared_length(answered)
        if (
            answered_length is not None
            and answered_length > upstream.max_response_bytes
        ):
            await answer.aclose()
            raise HTTPException(
                status_code=502, detail="The upstream's answer is too large"
            )
        relay = StreamingResponse(
            relayed(answer, upstream.max_response_bytes), status_code=answer.status_code
        )
        relay.raw_headers = client_response_headers(
            answered, upstream.base_url, public_prefix
        )
        return relay

    @custom_route(
        method="GET",
        path="/{upstream}/{path:path}",
        authentication_type="session",
        openapi_tags=(_TAG,),
        summary="GET a page of a declared upstream as the signed-in user",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
    )
    async def get_route(self, upstream: str, path: str, request: Request) -> Response:
        return await self.forward(request, upstream)

    @custom_route(
        method="DELETE",
        path="/{upstream}/{path:path}",
        authentication_type="session",
        openapi_tags=(_TAG,),
        summary="DELETE on a declared upstream as the signed-in user",
        expose_in=(ExposeIn.REST,),
        response_class=Response,
    )
    async def delete_route(
        self, upstream: str, path: str, request: Request
    ) -> Response:
        return await self.forward(request, upstream)
