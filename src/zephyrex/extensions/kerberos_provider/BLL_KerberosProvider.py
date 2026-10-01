"""Kerberos provider BLL: this server issues and validates Kerberos tickets.

Manages service principals and serves as a SPNEGO acceptor. The ``gssapi``
library is imported lazily at usage time.
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


class KerberosPrincipalModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A Kerberos principal managed by this KDC."""

    principal: str = Field(..., description="Full principal name (name@REALM)")
    principal_type: str = Field(
        "user", description="Type: 'user', 'service', or 'host'"
    )
    user_id: Optional[str] = Field(
        None, description="Local user ID if this is a user principal"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Kerberos principals managed by this KDC"

    class Create(BaseModel):
        principal: str
        principal_type: str = "user"
        user_id: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        principal_type: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        principal: Optional[StringSearchModel] = None
        principal_type: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class KerberosProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side Kerberos KDC configuration."""

    realm: str = Field(..., description="Kerberos realm name")
    kdc_port: int = Field(88, description="KDC listener port")
    service_principal: str = Field(
        ..., description="This server's service principal"
    )
    keytab_path: str = Field(..., description="Path to the server keytab")
    ticket_lifetime_hours: int = Field(10, description="TGT lifetime in hours")
    renew_lifetime_days: int = Field(7, description="Maximum renewal lifetime in days")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Kerberos KDC server configuration"

    class Create(BaseModel):
        realm: str
        kdc_port: int = 88
        service_principal: str
        keytab_path: str
        ticket_lifetime_hours: int = 10
        renew_lifetime_days: int = 7
        is_enabled: bool = True

    class Update(BaseModel):
        kdc_port: Optional[int] = None
        service_principal: Optional[str] = None
        keytab_path: Optional[str] = None
        ticket_lifetime_hours: Optional[int] = None
        renew_lifetime_days: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        realm: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class KerberosProviderManager(AbstractBLLManager):
    _model = KerberosProviderConfigModel
