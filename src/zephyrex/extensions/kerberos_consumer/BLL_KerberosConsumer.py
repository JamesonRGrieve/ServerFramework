"""Kerberos consumer BLL: authenticate via Kerberos/SPNEGO/GSSAPI.

Validates user credentials against an external KDC. Supports password-based
kinit flow and SPNEGO Negotiate header flow. The ``gssapi`` library is
imported lazily at usage time.
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


class KerberosRealmConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Configuration for an external Kerberos realm/KDC."""

    realm: str = Field(..., description="Kerberos realm name (e.g. EXAMPLE.COM)")
    kdc_host: str = Field(..., description="KDC hostname or IP")
    kdc_port: int = Field(88, description="KDC port")
    service_principal: Optional[str] = Field(
        None, description="Service principal name (e.g. HTTP/host@REALM)"
    )
    keytab_path: Optional[str] = Field(
        None, description="Path to keytab file for service authentication"
    )
    allow_password_auth: bool = Field(
        True, description="Allow password-based (kinit-style) authentication"
    )
    allow_spnego: bool = Field(
        True, description="Allow SPNEGO Negotiate header authentication"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Kerberos realm configurations for consumer auth"

    class Create(BaseModel):
        realm: str
        kdc_host: str
        kdc_port: int = 88
        service_principal: Optional[str] = None
        keytab_path: Optional[str] = None
        allow_password_auth: bool = True
        allow_spnego: bool = True
        is_enabled: bool = True

    class Update(BaseModel):
        kdc_host: Optional[str] = None
        kdc_port: Optional[int] = None
        service_principal: Optional[str] = None
        keytab_path: Optional[str] = None
        allow_password_auth: Optional[bool] = None
        allow_spnego: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        realm: Optional[StringSearchModel] = None
        kdc_host: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserKerberosLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Link between a local user and their Kerberos principal."""

    user_id: str = Field(..., description="Local user this link belongs to")
    realm_config_id: str = Field(..., description="FK to KerberosRealmConfigModel")
    principal: str = Field(..., description="Kerberos principal (user@REALM)")
    last_login_at: Optional[datetime] = Field(None, description="Last successful auth")

    table_comment: ClassVar[str] = "Links a local user to a Kerberos principal"

    class Create(BaseModel):
        user_id: str
        realm_config_id: str
        principal: str

    class Update(BaseModel):
        principal: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        realm_config_id: Optional[StringSearchModel] = None
        principal: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class KerberosConsumerManager(AbstractBLLManager):
    _model = KerberosRealmConfigModel
