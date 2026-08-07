"""Forward auth consumer BLL.

Makes subrequests to an external forward auth service to validate
incoming requests. The external service returns 2xx (allow) or
401/403 (deny), optionally setting user identity headers in the response.
"""

from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class ForwardAuthEndpointModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """An external forward auth endpoint configuration."""

    name: str = Field(..., description="Friendly name for this auth endpoint")
    url: str = Field(..., description="Forward auth endpoint URL")
    user_header: str = Field(
        "X-Forwarded-User", description="Response header containing username"
    )
    email_header: Optional[str] = Field(
        "X-Forwarded-Email", description="Response header containing email"
    )
    timeout_seconds: int = Field(5, description="Subrequest timeout")
    pass_cookies: bool = Field(
        True, description="Forward cookies from the original request"
    )
    pass_authorization: bool = Field(
        True, description="Forward Authorization header from the original request"
    )
    path_prefix: Optional[str] = Field(
        None, description="Only apply to requests matching this path prefix"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Forward auth endpoint configurations"

    class Create(BaseModel):
        name: str
        url: str
        user_header: str = "X-Forwarded-User"
        email_header: Optional[str] = "X-Forwarded-Email"
        timeout_seconds: int = 5
        pass_cookies: bool = True
        pass_authorization: bool = True
        path_prefix: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        url: Optional[str] = None
        user_header: Optional[str] = None
        email_header: Optional[str] = None
        timeout_seconds: Optional[int] = None
        pass_cookies: Optional[bool] = None
        pass_authorization: Optional[bool] = None
        path_prefix: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        url: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ForwardAuthConsumerManager(AbstractBLLManager):
    _model = ForwardAuthEndpointModel
