"""SCIM 2.0 provider BLL: push provisioning events to downstream SPs.

Manages SCIM target registrations and pushes user/group lifecycle events
(create, update, deactivate, delete) to registered service providers.
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


class SCIMTargetModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A downstream SCIM service provider that receives provisioning events."""

    name: str = Field(..., description="Friendly name for this target SP")
    base_url: str = Field(..., description="SCIM base URL of the target SP")
    bearer_token: str = Field(
        ..., description="Bearer token for authenticating to the target (encrypted at rest)"
    )
    timeout_seconds: int = Field(10, description="Request timeout")
    retry_count: int = Field(3, description="Retry count on failure")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "SCIM target service provider registrations"

    class Create(BaseModel):
        name: str
        base_url: str
        bearer_token: str
        timeout_seconds: int = 10
        retry_count: int = 3
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        base_url: Optional[str] = None
        bearer_token: Optional[str] = None
        timeout_seconds: Optional[int] = None
        retry_count: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        base_url: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class SCIMSyncLogModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Audit log of outbound SCIM provisioning events."""

    target_id: str = Field(..., description="FK to SCIMTargetModel")
    resource_type: str = Field(..., description="'User' or 'Group'")
    operation: str = Field(..., description="'create', 'replace', 'patch', 'delete'")
    local_user_id: Optional[str] = Field(None, description="Local user ID")
    scim_id: Optional[str] = Field(None, description="SCIM ID at the target")
    status: str = Field("success", description="'success' or 'error'")
    error_detail: Optional[str] = Field(None)
    synced_at: datetime = Field(..., description="When the sync was attempted")

    table_comment: ClassVar[str] = "Outbound SCIM provisioning audit log"

    class Create(BaseModel):
        target_id: str
        resource_type: str
        operation: str
        local_user_id: Optional[str] = None
        scim_id: Optional[str] = None
        status: str = "success"
        error_detail: Optional[str] = None
        synced_at: Optional[datetime] = None

    class Update(BaseModel):
        status: Optional[str] = None
        error_detail: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        target_id: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        operation: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        synced_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SCIMProviderManager(AbstractBLLManager):
    _model = SCIMTargetModel
