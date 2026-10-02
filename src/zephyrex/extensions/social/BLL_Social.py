# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record of what the social extension published: one row per post a
platform accepted, naming the provider instance (the account) that
published it, the platform's id for the post and its address.

Rows are written by the ``publish_post`` ability, never through the API:
the routes read and search only. A row belongs to the account's owner
(the instance's user and team), so whoever can see the account sees its
posts, and a root account's posts stay root's.
"""

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Type

from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
)
from zephyrex.logic.BLL_Auth import TeamModel, UserModel
from zephyrex.logic.BLL_Providers import ProviderInstanceManager
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel


class SocialPublicationModel(
    ApplicationModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["SocialPublicationManager"]]
    provider: str = Field(..., description="The platform's provider (x, threads, …)")
    provider_instance_id: str = Field(
        ..., description="The provider instance (account) that published"
    )
    platform_post_id: str = Field(..., description="The platform's id for the post")
    url: Optional[str] = Field(None, description="The post's address, when known")
    content: str = Field(..., description="The text published")
    media_urls: Optional[List[str]] = Field(None, description="Media attached")
    published_at: datetime = Field(..., description="When the platform accepted it")

    table_comment: ClassVar[str] = (
        "Posts the social extension published: platform, account (provider "
        "instance), the platform's post id and address, content and media"
    )

    class Create(BaseModel):
        user_id: Optional[str] = None
        team_id: Optional[str] = None
        provider: str
        provider_instance_id: str
        platform_post_id: str
        url: Optional[str] = None
        content: str
        media_urls: Optional[List[str]] = None
        published_at: datetime

    class Update(BaseModel):
        url: Optional[str] = None

    class Search(ApplicationModel.Search):
        provider: Optional[StringSearchModel] = None
        provider_instance_id: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None
        published_at: Optional[DateSearchModel] = None


class SocialPublicationManager(AbstractBLLManager, RouterMixin):
    _model = SocialPublicationModel
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
    ]


def record_publication(
    model_registry: Any,
    post: Dict[str, Any],
    content: str,
    media_urls: List[str],
    published_at: datetime,
) -> str:
    """Record a post a platform accepted, as its account's owner; the
    record's id. ``post`` names the provider, the instance and the
    platform's post id and address."""
    instance = ProviderInstanceManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    ).get(id=post["provider_instance_id"])
    owner = instance.user_id or instance.created_by_user_id or env("ROOT_ID")
    created = SocialPublicationManager(
        model_registry=model_registry, requester_id=str(owner)
    ).create(
        user_id=instance.user_id,
        team_id=instance.team_id,
        provider=post["provider"],
        provider_instance_id=post["provider_instance_id"],
        platform_post_id=post["platform_post_id"],
        url=post["url"],
        content=content,
        media_urls=media_urls or None,
        published_at=published_at,
    )
    return str(created.id)
