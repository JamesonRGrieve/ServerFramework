"""Trusted proxy header authentication provider BLL.

Manages downstream service targets and header injection configuration.
When a request is proxied to a downstream service, this extension injects
authentication headers (X-Forwarded-User, etc.) after validating the
user's session. No external library dependencies.
"""

from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class ProxyAuthTargetModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A downstream service that receives proxy auth headers."""

    name: str = Field(..., description="Friendly name for this downstream target")
    target_url: str = Field(
        ..., description="Base URL of the downstream service"
    )
    user_header: str = Field(
        "X-Forwarded-User", description="Header to set with the username"
    )
    email_header: Optional[str] = Field(
        "X-Forwarded-Email", description="Header to set with the email"
    )
    name_header: Optional[str] = Field(
        "X-Forwarded-Name", description="Header to set with the display name"
    )
    groups_header: Optional[str] = Field(
        "X-Forwarded-Groups", description="Header to set with comma-separated groups"
    )
    strip_incoming: bool = Field(
        True, description="Strip these headers from incoming requests before proxying"
    )
    allowed_roles: Optional[str] = Field(
        None, description="Space-delimited roles allowed to access this target"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Downstream targets for proxy header auth injection"

    class Create(BaseModel):
        name: str
        target_url: str
        user_header: str = "X-Forwarded-User"
        email_header: Optional[str] = "X-Forwarded-Email"
        name_header: Optional[str] = "X-Forwarded-Name"
        groups_header: Optional[str] = "X-Forwarded-Groups"
        strip_incoming: bool = True
        allowed_roles: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        target_url: Optional[str] = None
        user_header: Optional[str] = None
        email_header: Optional[str] = None
        name_header: Optional[str] = None
        groups_header: Optional[str] = None
        strip_incoming: Optional[bool] = None
        allowed_roles: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        target_url: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ProxyAuthProviderManager(AbstractBLLManager):
    _model = ProxyAuthTargetModel
