# SPDX-License-Identifier: AGPL-3.0-or-later
"""The signed-in user's own files at the services they signed in to.

Routes on ``/v1/cloud``, served only by delegated providers
(``AbstractDelegatedCloudProvider``, e.g. ``google_drive``), which act with
the requester's own access token from ``oauth_consumer``. The deployment's
object stores (S3, Azure, GCS, Dropbox, Nextcloud instances) are never
reachable here; they stay abilities for the server's own use.

- ``GET /{provider}/files?folder=&search=``: ``{"files": [CloudFile]}``.
- ``GET /{provider}/file?path=``: ``{"file": CloudFileContent}``.

A user who never signed in with the provider's identity provider gets 409; a
sign-in that did not grant the provider's scope gets 403 (sign in again to
grant it); the service refusing the token is 403 too.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import HTTPException
from pydantic import BaseModel as ResponseModel

# Delegated providers register themselves on import.
from zephyrex.extensions.cloud import PRV_GoogleDrive  # noqa: F401
from zephyrex.extensions.cloud.EXT_Cloud import (
    AbstractDelegatedCloudProvider,
    CloudFile,
    CloudFileContent,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    RateLimitExternalError,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import RouterMixin

CLOUD_PREFIX = "/v1/cloud"
OAUTH_CONSUMER = "oauth_consumer"
MAX_SEARCH_LENGTH = 200


class CloudFileList(ResponseModel):
    files: list[CloudFile]


class CloudFileRead(ResponseModel):
    file: CloudFileContent


def delegated_provider(name: str) -> type[AbstractDelegatedCloudProvider]:
    provider = AbstractDelegatedCloudProvider.registry.get(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"No file service named {name!r}")
    return provider


async def access_token(
    user_id: str, provider: type[AbstractDelegatedCloudProvider], model_registry: Any
) -> str:
    """The user's current access token at ``provider``'s identity provider,
    holding the scope its calls need."""
    if OAUTH_CONSUMER not in model_registry.loaded_extension_names():
        raise HTTPException(
            status_code=409,
            detail=f"{provider.friendly_name} needs sign-in through {OAUTH_CONSUMER}",
        )
    from zephyrex.extensions.oauth_consumer.EXT_OAuthConsumer import (
        EXT_OAuthConsumer as consumer,
    )

    identities = [
        i
        for i in await consumer.list_oauth_identities(user_id)
        if i["provider"] == provider.identity_provider
    ]
    if not identities:
        raise HTTPException(
            status_code=409,
            detail=f"Sign in with {provider.identity_provider} to reach your {provider.friendly_name}",
        )
    # Someone may have linked several accounts: use the most recently signed-in
    # one whose sign-in granted the access this provider needs.
    identities.sort(key=lambda i: i.get("last_login_at") or "", reverse=True)
    for identity in identities:
        token = await consumer.oauth_access_token(user_id, identity["id"])
        if provider.required_scope in (token.get("scopes") or "").split():
            return str(token["access_token"])
    raise HTTPException(
        status_code=403,
        detail=f"Your sign-in does not allow reading your {provider.friendly_name}; sign in again to allow it",
    )


def upstream_refusal(
    err: BaseExternalError, provider: type[AbstractDelegatedCloudProvider]
) -> HTTPException:
    """``err`` from the provider as the HTTP answer the caller can act on."""
    if isinstance(err, AuthExternalError):
        return HTTPException(
            status_code=403,
            detail=f"{provider.friendly_name} refused your sign-in; sign in again",
        )
    if isinstance(err, InvalidInputExternalError):
        return HTTPException(status_code=422, detail=str(err))
    if isinstance(err, RateLimitExternalError):
        return HTTPException(
            status_code=429, detail=f"{provider.friendly_name} is rate limiting"
        )
    logger.warning("cloud: %s failed: %s", provider.name, err)
    return HTTPException(
        status_code=502, detail=f"{provider.friendly_name} did not answer"
    )


class CloudManager(AbstractBLLManager, RouterMixin):
    prefix: ClassVar[str | None] = CLOUD_PREFIX
    tags: ClassVar[list[str] | None] = ["Cloud"]
    routes_to_register: ClassVar[list[Any] | None] = []

    @custom_route(
        method="GET",
        path="/{provider}/files",
        output_model=CloudFileList,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Cloud",),
        summary="Your readable files at a service you signed in to",
    )
    async def files_route(
        self, provider: str, folder: str = "", search: str | None = None
    ) -> CloudFileList:
        delegated = delegated_provider(provider)
        if search is not None and len(search) > MAX_SEARCH_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"search may be at most {MAX_SEARCH_LENGTH} characters",
            )
        token = await access_token(
            str(self.requester.id), delegated, self.model_registry
        )
        try:
            return CloudFileList(
                files=await delegated.list(token, folder, search or None)
            )
        except BaseExternalError as err:
            raise upstream_refusal(err, delegated) from err

    @custom_route(
        method="GET",
        path="/{provider}/file",
        output_model=CloudFileRead,
        authentication_type="jwt",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("Cloud",),
        summary="One of your files at a service you signed in to, as text",
    )
    async def file_route(self, provider: str, path: str) -> CloudFileRead:
        delegated = delegated_provider(provider)
        token = await access_token(
            str(self.requester.id), delegated, self.model_registry
        )
        try:
            return CloudFileRead(file=await delegated.read(token, path))
        except BaseExternalError as err:
            raise upstream_refusal(err, delegated) from err
