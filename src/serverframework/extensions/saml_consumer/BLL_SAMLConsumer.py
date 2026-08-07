"""SAML 2.0 consumer (Service Provider) BLL.

Implements the SP side of SAML SSO: AuthnRequest generation, assertion
consumer service (ACS) response validation, single logout, and SP
metadata generation. The ``pysaml2`` library is imported lazily.
"""

from datetime import datetime
from typing import ClassVar, List, Optional

from pydantic import BaseModel, Field

from serverframework.logic.AbstractLogicManager import (
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


class SAMLIdPConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Configuration for an external SAML Identity Provider."""

    name: str = Field(..., description="Friendly name for this IdP")
    entity_id: str = Field(..., description="IdP entity ID")
    sso_url: str = Field(..., description="IdP SSO endpoint URL")
    slo_url: Optional[str] = Field(None, description="IdP SLO endpoint URL")
    idp_cert_pem: Optional[str] = Field(
        None, description="IdP signing certificate PEM"
    )
    metadata_url: Optional[str] = Field(
        None, description="URL to IdP metadata XML"
    )
    name_id_format: str = Field(
        "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        description="Requested NameID format",
    )
    want_assertions_signed: bool = Field(True)
    want_response_signed: bool = Field(True)
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "SAML IdP configurations for SP consumer auth"

    class Create(BaseModel):
        name: str
        entity_id: str
        sso_url: str
        slo_url: Optional[str] = None
        idp_cert_pem: Optional[str] = None
        metadata_url: Optional[str] = None
        name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        want_assertions_signed: bool = True
        want_response_signed: bool = True
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        sso_url: Optional[str] = None
        slo_url: Optional[str] = None
        idp_cert_pem: Optional[str] = None
        metadata_url: Optional[str] = None
        name_id_format: Optional[str] = None
        want_assertions_signed: Optional[bool] = None
        want_response_signed: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        entity_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserSAMLLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Link between a local user and their SAML identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    idp_config_id: str = Field(..., description="FK to SAMLIdPConfigModel")
    name_id: str = Field(..., description="SAML NameID value")
    session_index: Optional[str] = Field(None, description="SAML SessionIndex")
    last_login_at: Optional[datetime] = Field(None, description="Last successful SAML auth")

    table_comment: ClassVar[str] = "Links a local user to a SAML identity"

    class Create(BaseModel):
        user_id: str
        idp_config_id: str
        name_id: str
        session_index: Optional[str] = None

    class Update(BaseModel):
        session_index: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        idp_config_id: Optional[StringSearchModel] = None
        name_id: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SAMLConsumerManager(AbstractBLLManager):
    _model = SAMLIdPConfigModel
