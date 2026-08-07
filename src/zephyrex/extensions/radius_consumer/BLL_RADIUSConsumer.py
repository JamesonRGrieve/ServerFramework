"""RADIUS consumer BLL: authenticate users against an external RADIUS server.

Supports PAP, CHAP, and EAP authentication methods. The ``pyrad``
library is imported lazily at usage time.
"""

from datetime import datetime
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


# ---------------------------------------------------------------------------
# Database model
# ---------------------------------------------------------------------------


class RADIUSServerConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Configuration for an external RADIUS server."""

    host: str = Field(..., description="RADIUS server hostname or IP")
    auth_port: int = Field(1812, description="Authentication port")
    acct_port: int = Field(1813, description="Accounting port")
    shared_secret: str = Field(..., description="Shared secret (encrypted at rest)")
    timeout_seconds: int = Field(5, description="Request timeout")
    retries: int = Field(3, description="Number of retries on timeout")
    nas_identifier: Optional[str] = Field(
        None, description="NAS-Identifier attribute value"
    )
    auth_method: str = Field(
        "PAP", description="Authentication method: PAP, CHAP, or EAP"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "RADIUS server configurations for consumer auth"

    class Create(BaseModel):
        host: str
        auth_port: int = 1812
        acct_port: int = 1813
        shared_secret: str
        timeout_seconds: int = 5
        retries: int = 3
        nas_identifier: Optional[str] = None
        auth_method: str = "PAP"
        is_enabled: bool = True

    class Update(BaseModel):
        host: Optional[str] = None
        auth_port: Optional[int] = None
        acct_port: Optional[int] = None
        shared_secret: Optional[str] = None
        timeout_seconds: Optional[int] = None
        retries: Optional[int] = None
        nas_identifier: Optional[str] = None
        auth_method: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        host: Optional[StringSearchModel] = None
        auth_method: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserRADIUSLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Link between a local user and their RADIUS identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    radius_server_id: str = Field(..., description="FK to RADIUSServerConfigModel")
    radius_username: str = Field(..., description="Username sent in RADIUS requests")
    last_login_at: Optional[datetime] = Field(None, description="Last successful auth")

    table_comment: ClassVar[str] = "Links a local user to a RADIUS identity"

    class Create(BaseModel):
        user_id: str
        radius_server_id: str
        radius_username: str

    class Update(BaseModel):
        radius_username: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        radius_server_id: Optional[StringSearchModel] = None
        radius_username: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class RADIUSConsumerManager(AbstractBLLManager):
    _model = RADIUSServerConfigModel
