from typing import ClassVar, Optional, Type

from pydantic import Field

from zephyrex.lib.Environment import env
from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth._shared import BaseModel


class RateLimitPolicyModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RateLimitPolicyManager"]] = None  # type: ignore[assignment]
    resource_pattern: str = Field(..., description="Resource pattern to match")
    window_seconds: int = Field(..., description="Time window in seconds")
    max_requests: int = Field(..., description="Maximum requests in time window")
    scope: str = Field(..., description="Scope of rate limiting (user, ip, global)")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Rate limiting policies for API endpoints and resources"
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    class Create(BaseModel, NameMixinModel):
        resource_pattern: str = Field(..., description="Resource pattern to match")
        window_seconds: int = Field(..., description="Time window in seconds")
        max_requests: int = Field(..., description="Maximum requests in time window")
        scope: str = Field(..., description="Scope of rate limiting (user, ip, global)")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Policy name")
        resource_pattern: Optional[str] = Field(
            None, description="Resource pattern to match"
        )
        window_seconds: Optional[int] = Field(
            None, description="Time window in seconds"
        )
        max_requests: Optional[int] = Field(
            None, description="Maximum requests in time window"
        )
        scope: Optional[str] = Field(
            None, description="Scope of rate limiting (user, ip, global)"
        )

    class Search(ApplicationModel.Search, NameMixinModel.Search):
        resource_pattern: Optional[StringSearchModel] | None = None
        window_seconds: Optional[NumericalSearchModel] | None = None
        max_requests: Optional[NumericalSearchModel] | None = None
        scope: Optional[StringSearchModel] | None = None


class RateLimitPolicyManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = RateLimitPolicyModel


RateLimitPolicyModel.Manager = RateLimitPolicyManager
