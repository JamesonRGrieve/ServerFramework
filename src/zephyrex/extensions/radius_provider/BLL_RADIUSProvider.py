"""RADIUS provider BLL: this server acts as a RADIUS authenticator.

Manages NAS client registrations and processes Access-Request packets
against the local user store. The ``pyrad`` library is imported lazily.
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


class RADIUSNASClientModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A NAS client registered with this RADIUS server."""

    name: str = Field(..., description="Friendly name for this NAS client")
    ip_address: str = Field(..., description="NAS client IP address")
    shared_secret: str = Field(..., description="Shared secret (encrypted at rest)")
    vendor_id: Optional[int] = Field(None, description="IANA vendor ID for VSAs")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "RADIUS NAS client registrations"

    class Create(BaseModel):
        name: str
        ip_address: str
        shared_secret: str
        vendor_id: Optional[int] = None
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        ip_address: Optional[str] = None
        shared_secret: Optional[str] = None
        vendor_id: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        ip_address: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class RADIUSProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side RADIUS provider configuration."""

    auth_port: int = Field(1812, description="Authentication listener port")
    acct_port: int = Field(1813, description="Accounting listener port")
    dictionary_path: Optional[str] = Field(
        None, description="Path to RADIUS dictionary file"
    )
    max_clients: int = Field(256, description="Maximum concurrent NAS clients")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "RADIUS provider server configuration"

    class Create(BaseModel):
        auth_port: int = 1812
        acct_port: int = 1813
        dictionary_path: Optional[str] = None
        max_clients: int = 256
        is_enabled: bool = True

    class Update(BaseModel):
        auth_port: Optional[int] = None
        acct_port: Optional[int] = None
        dictionary_path: Optional[str] = None
        max_clients: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class RADIUSProviderManager(AbstractBLLManager):
    _model = RADIUSProviderConfigModel
