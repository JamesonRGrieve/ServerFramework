"""Forward auth provider BLL.

Serves as the forward auth endpoint that reverse proxies call to verify
requests. Validates session cookies/tokens and returns 2xx with user
identity headers on success, or 401/403 on failure. Supports per-path
access rules with role requirements. No external library dependencies.
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


class ForwardAuthRuleModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """An access rule for the forward auth endpoint."""

    name: str = Field(..., description="Friendly name for this rule")
    path_pattern: str = Field(
        ..., description="URL path pattern to match (glob or regex)"
    )
    required_roles: Optional[str] = Field(
        None, description="Space-delimited roles required for access"
    )
    allowed_methods: Optional[str] = Field(
        None, description="Space-delimited HTTP methods (GET POST etc); None = all"
    )
    response_headers_json: str = Field(
        "{}",
        description="JSON object of additional headers to set on successful auth",
    )
    deny_action: str = Field(
        "401", description="HTTP status to return on denial: '401' or '403'"
    )
    redirect_url: Optional[str] = Field(
        None, description="Redirect URL on denial (overrides deny_action)"
    )
    priority: int = Field(0, description="Rule evaluation priority (higher = first)")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Forward auth access rules"

    class Create(BaseModel):
        name: str
        path_pattern: str
        required_roles: Optional[str] = None
        allowed_methods: Optional[str] = None
        response_headers_json: str = "{}"
        deny_action: str = "401"
        redirect_url: Optional[str] = None
        priority: int = 0
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        path_pattern: Optional[str] = None
        required_roles: Optional[str] = None
        allowed_methods: Optional[str] = None
        response_headers_json: Optional[str] = None
        deny_action: Optional[str] = None
        redirect_url: Optional[str] = None
        priority: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        path_pattern: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ForwardAuthProviderManager(AbstractBLLManager):
    _model = ForwardAuthRuleModel
