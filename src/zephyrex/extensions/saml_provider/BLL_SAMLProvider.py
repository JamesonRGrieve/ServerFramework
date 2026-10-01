"""SAML 2.0 Identity Provider BLL.

Manages Service Provider registrations and issues signed SAML assertions.
Supports configurable attribute statements, NameID formats, and single
logout. The ``pysaml2`` library is imported lazily.
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


class SAMLServiceProviderModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A Service Provider registered with this SAML IdP."""

    name: str = Field(..., description="Friendly name for this SP")
    entity_id: str = Field(..., description="SP entity ID")
    acs_url: str = Field(..., description="Assertion Consumer Service URL")
    sls_url: Optional[str] = Field(None, description="Single Logout Service URL")
    sp_cert_pem: Optional[str] = Field(
        None, description="SP signing/encryption certificate PEM"
    )
    name_id_format: str = Field(
        "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        description="NameID format to use in assertions",
    )
    attribute_mapping_json: str = Field(
        "{}", description="JSON mapping of SAML attributes to local user fields"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "SAML Service Provider registrations"

    class Create(BaseModel):
        name: str
        entity_id: str
        acs_url: str
        sls_url: Optional[str] = None
        sp_cert_pem: Optional[str] = None
        name_id_format: str = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        attribute_mapping_json: str = "{}"
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        acs_url: Optional[str] = None
        sls_url: Optional[str] = None
        sp_cert_pem: Optional[str] = None
        name_id_format: Optional[str] = None
        attribute_mapping_json: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        entity_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class SAMLProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side SAML IdP configuration."""

    entity_id: str = Field(..., description="IdP entity ID")
    sso_url: str = Field(..., description="SSO endpoint URL")
    slo_url: Optional[str] = Field(None, description="SLO endpoint URL")
    cert_path: str = Field(..., description="Path to IdP signing certificate")
    key_path: str = Field(..., description="Path to IdP signing private key")
    assertion_ttl_minutes: int = Field(5, description="Assertion validity in minutes")
    sign_assertions: bool = Field(True)
    sign_responses: bool = Field(True)
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "SAML IdP server configuration"

    class Create(BaseModel):
        entity_id: str
        sso_url: str
        slo_url: Optional[str] = None
        cert_path: str
        key_path: str
        assertion_ttl_minutes: int = 5
        sign_assertions: bool = True
        sign_responses: bool = True
        is_enabled: bool = True

    class Update(BaseModel):
        sso_url: Optional[str] = None
        slo_url: Optional[str] = None
        cert_path: Optional[str] = None
        key_path: Optional[str] = None
        assertion_ttl_minutes: Optional[int] = None
        sign_assertions: Optional[bool] = None
        sign_responses: Optional[bool] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        entity_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class SAMLProviderManager(AbstractBLLManager):
    _model = SAMLProviderConfigModel
