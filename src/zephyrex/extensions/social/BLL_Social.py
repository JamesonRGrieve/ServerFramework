from datetime import datetime
from enum import Enum
from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserModel


class SocialPlatform(str, Enum):
    TWITTER = "twitter"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    LINKEDIN = "linkedin"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    REDDIT = "reddit"


class SocialAccountModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.ID,
    metaclass=ModelMeta,
):
    """Social media account connected to a user profile with OAuth credentials."""

    platform: SocialPlatform = Field(..., description="Social media platform")
    username: str = Field(..., description="Username on the platform")
    access_token: Optional[str] = Field(None, description="OAuth access token")
    refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
    token_expires_at: Optional[datetime] = Field(
        None, description="Token expiration time"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Social media accounts connected to user profiles with OAuth credentials"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        platform: SocialPlatform = Field(..., description="Social media platform")
        username: str = Field(..., description="Username on the platform")
        access_token: Optional[str] = Field(None, description="OAuth access token")
        refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
        token_expires_at: Optional[datetime] = Field(
            None, description="Token expiration time"
        )

    class Update(BaseModel):
        username: Optional[str] = Field(None, description="Username on the platform")
        access_token: Optional[str] = Field(None, description="OAuth access token")
        refresh_token: Optional[str] = Field(None, description="OAuth refresh token")
        token_expires_at: Optional[datetime] = Field(
            None, description="Token expiration time"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        platform: Optional[SocialPlatform] = None
        username: Optional[StringSearchModel] = None


class SocialPostModel(
    ApplicationModel.Optional,
    SocialAccountModel.Reference.ID,
    metaclass=ModelMeta,
):
    """Social media post with content, scheduling, and publishing status."""

    platform_post_id: Optional[str] = Field(None, description="Post ID on the platform")
    content: str = Field(..., description="Post content")
    post_url: Optional[str] = Field(None, description="URL of the post on the platform")
    scheduled_time: Optional[datetime] = Field(
        None, description="Scheduled posting time"
    )
    is_posted: bool = Field(False, description="Whether the post has been published")
    posted_at: Optional[datetime] = Field(
        None, description="When the post was published"
    )

    # Database metadata
    table_comment: ClassVar[str] = (
        "Social media posts with content, scheduling, and publishing status"
    )

    class Create(BaseModel, SocialAccountModel.Reference.ID):
        content: str = Field(..., description="Post content")
        scheduled_time: Optional[datetime] = Field(
            None, description="Scheduled posting time"
        )

    class Update(BaseModel):
        platform_post_id: Optional[str] = Field(
            None, description="Post ID on the platform"
        )
        content: Optional[str] = Field(None, description="Post content")
        post_url: Optional[str] = Field(
            None, description="URL of the post on the platform"
        )
        scheduled_time: Optional[datetime] = Field(
            None, description="Scheduled posting time"
        )
        is_posted: Optional[bool] = Field(
            None, description="Whether the post has been published"
        )
        posted_at: Optional[datetime] = Field(
            None, description="When the post was published"
        )

    class Search(ApplicationModel.Search, SocialAccountModel.Reference.ID.Search):
        platform_post_id: Optional[StringSearchModel] = None
        content: Optional[StringSearchModel] = None
        is_posted: Optional[bool] = None
        scheduled_time: Optional[DateSearchModel] = None
        posted_at: Optional[DateSearchModel] = None


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    GIF = "gif"
    AUDIO = "audio"


class SocialMediaModel(
    ApplicationModel.Optional,
    SocialPostModel.Reference.ID,
    metaclass=ModelMeta,
):
    """Media attachment for a social media post (image, video, GIF, etc.)."""

    media_type: MediaType = Field(..., description="Type of media")
    media_url: str = Field(..., description="URL of the media file")

    # Database metadata
    table_comment: ClassVar[str] = (
        "Media attachments for social media posts including images, videos, and GIFs"
    )

    class Create(BaseModel, SocialPostModel.Reference.ID):
        media_type: MediaType = Field(..., description="Type of media")
        media_url: str = Field(..., description="URL of the media file")

    class Update(BaseModel):
        media_type: Optional[MediaType] = Field(None, description="Type of media")
        media_url: Optional[str] = Field(None, description="URL of the media file")

    class Search(ApplicationModel.Search, SocialPostModel.Reference.ID.Search):
        media_type: Optional[MediaType] = None
        media_url: Optional[StringSearchModel] = None


class SocialMetricModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    SocialPostModel.Reference.ID,
    metaclass=ModelMeta,
):
    """Engagement metrics for a social media post (likes, shares, comments, views)."""

    likes: int = Field(0, description="Number of likes")
    shares: int = Field(0, description="Number of shares")
    comments: int = Field(0, description="Number of comments")
    views: int = Field(0, description="Number of views")

    # Database metadata
    table_comment: ClassVar[str] = (
        "Engagement metrics for social media posts including likes, shares, and comments"
    )

    class Create(BaseModel, SocialPostModel.Reference.ID):
        likes: int = Field(0, description="Number of likes")
        shares: int = Field(0, description="Number of shares")
        comments: int = Field(0, description="Number of comments")
        views: int = Field(0, description="Number of views")

    class Update(BaseModel):
        likes: Optional[int] = Field(None, description="Number of likes")
        shares: Optional[int] = Field(None, description="Number of shares")
        comments: Optional[int] = Field(None, description="Number of comments")
        views: Optional[int] = Field(None, description="Number of views")

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        SocialPostModel.Reference.ID.Search,
    ):
        likes: Optional[int] = None
        shares: Optional[int] = None
        comments: Optional[int] = None
        views: Optional[int] = None


class SocialAccountManager(AbstractBLLManager):
    """Manager for social media account operations."""

    _model = SocialAccountModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._posts = None

    @property
    def posts(self) -> "SocialPostManager":
        if self._posts is None:
            self._posts = SocialPostManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._posts


class SocialPostManager(AbstractBLLManager):
    """Manager for social media post operations."""

    _model = SocialPostModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._accounts = None
        self._media = None
        self._metrics = None

    @property
    def accounts(self) -> "SocialAccountManager":
        if self._accounts is None:
            self._accounts = SocialAccountManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._accounts

    @property
    def media(self) -> "SocialMediaManager":
        if self._media is None:
            self._media = SocialMediaManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._media

    @property
    def metrics(self) -> "SocialMetricManager":
        if self._metrics is None:
            self._metrics = SocialMetricManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._metrics


class SocialMediaManager(AbstractBLLManager):
    """Manager for social media attachment operations."""

    _model = SocialMediaModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._posts = None

    @property
    def posts(self) -> "SocialPostManager":
        if self._posts is None:
            self._posts = SocialPostManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._posts


class SocialMetricManager(AbstractBLLManager):
    """Manager for social media engagement metric operations."""

    _model = SocialMetricModel

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._posts = None

    @property
    def posts(self) -> "SocialPostManager":
        if self._posts is None:
            self._posts = SocialPostManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._posts
