# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record of what the social extension published: one row per post a
platform accepted, naming the provider instance (the account) that
published it, the platform's id for the post and its address.

Rows are written by the ``publish_post`` ability, never through the API:
the routes read and search only.
"""

from datetime import datetime
from typing import ClassVar, List, Optional, Type

from pydantic import Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
)
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel


class SocialPublicationModel(
    ApplicationModel.Optional,
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
