"""SCIM 2.0 consumer BLL: receive user/group provisioning events.

Exposes SCIM 2.0 endpoints (/Users, /Groups, /Schemas, /ServiceProviderConfig)
that an external IdP calls to provision and deprovision local users and groups.
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


class SCIMProvisioningLogModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Audit log of SCIM provisioning events received from external IdP."""

    scim_id: str = Field(..., description="SCIM resource ID from the IdP")
    resource_type: str = Field(..., description="'User' or 'Group'")
    operation: str = Field(
        ..., description="SCIM operation: 'create', 'replace', 'patch', 'delete'"
    )
    local_user_id: Optional[str] = Field(
        None, description="Local user ID created/updated by this event"
    )
    external_id: Optional[str] = Field(
        None, description="externalId attribute from the IdP"
    )
    idp_source: Optional[str] = Field(
        None, description="Identifier for the source IdP"
    )
    status: str = Field("success", description="Event outcome: 'success' or 'error'")
    error_detail: Optional[str] = Field(None, description="Error detail if status=error")
    received_at: datetime = Field(
        ..., description="When this provisioning event was received"
    )

    table_comment: ClassVar[str] = "SCIM provisioning event audit log"

    class Create(BaseModel):
        scim_id: str
        resource_type: str
        operation: str
        local_user_id: Optional[str] = None
        external_id: Optional[str] = None
        idp_source: Optional[str] = None
        status: str = "success"
        error_detail: Optional[str] = None
        received_at: Optional[datetime] = None

    class Update(BaseModel):
        status: Optional[str] = None
        error_detail: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        scim_id: Optional[StringSearchModel] = None
        resource_type: Optional[StringSearchModel] = None
        operation: Optional[StringSearchModel] = None
        local_user_id: Optional[StringSearchModel] = None
        status: Optional[StringSearchModel] = None
        received_at: Optional[DateSearchModel] = None


class SCIMConsumerConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """SCIM consumer endpoint configuration."""

    bearer_token_hash: str = Field(
        ..., description="Hashed bearer token for authenticating incoming SCIM requests"
    )
    base_path: str = Field("/scim/v2", description="Base path for SCIM endpoints")
    auto_create_users: bool = Field(True, description="Auto-create local users on SCIM push")
    auto_deactivate_users: bool = Field(
        True, description="Auto-deactivate users on SCIM delete/disable"
    )
    default_role: Optional[str] = Field(
        None, description="Default role assigned to SCIM-provisioned users"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "SCIM consumer endpoint configuration"

    class Create(BaseModel):
        bearer_token_hash: str
        base_path: str = "/scim/v2"
        auto_create_users: bool = True
        auto_deactivate_users: bool = True
        default_role: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        base_path: Optional[str] = None
        auto_create_users: Optional[bool] = None
        auto_deactivate_users: Optional[bool] = None
        default_role: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SCIMConsumerManager(AbstractBLLManager):
    _model = SCIMConsumerConfigModel
