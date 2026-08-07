"""Trusted proxy header authentication consumer BLL.

Reads authentication identity from headers set by a trusted reverse proxy
(X-Forwarded-User, X-Forwarded-Email, etc.). Only trusts headers from
configured proxy IP addresses to prevent header spoofing. No external
library dependencies.
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


class ProxyAuthTrustedSourceModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A trusted reverse proxy allowed to set authentication headers."""

    name: str = Field(..., description="Friendly name for this proxy")
    ip_address: str = Field(
        ..., description="IP address or CIDR range of the trusted proxy"
    )
    user_header: str = Field(
        "X-Forwarded-User", description="Header containing the username"
    )
    email_header: Optional[str] = Field(
        "X-Forwarded-Email", description="Header containing the email"
    )
    name_header: Optional[str] = Field(
        "X-Forwarded-Name", description="Header containing the display name"
    )
    groups_header: Optional[str] = Field(
        "X-Forwarded-Groups", description="Header containing comma-separated groups"
    )
    auto_create_users: bool = Field(
        False, description="Auto-create local users from proxy headers"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Trusted reverse proxies for header-based auth"

    class Create(BaseModel):
        name: str
        ip_address: str
        user_header: str = "X-Forwarded-User"
        email_header: Optional[str] = "X-Forwarded-Email"
        name_header: Optional[str] = "X-Forwarded-Name"
        groups_header: Optional[str] = "X-Forwarded-Groups"
        auto_create_users: bool = False
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        ip_address: Optional[str] = None
        user_header: Optional[str] = None
        email_header: Optional[str] = None
        name_header: Optional[str] = None
        groups_header: Optional[str] = None
        auto_create_users: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        ip_address: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ProxyAuthConsumerManager(AbstractBLLManager):
    _model = ProxyAuthTrustedSourceModel
