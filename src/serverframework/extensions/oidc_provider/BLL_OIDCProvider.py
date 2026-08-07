"""OpenID Connect provider BLL: this server issues ID tokens.

Layers OIDC identity semantics on top of the OAuth2 provider: discovery
document, JWKS endpoint, ID token issuance, and userinfo endpoint.
The ``PyJWT`` and ``cryptography`` libraries are imported lazily.
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


class OIDCSigningKeyModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Signing key for ID tokens issued by this OIDC provider."""

    kid: str = Field(..., description="Key ID (kid) for JWKS")
    algorithm: str = Field("RS256", description="Signing algorithm (RS256, ES256)")
    public_key_pem: str = Field(
        ..., description="PEM-encoded public key for JWKS exposure"
    )
    private_key_path: str = Field(
        ..., description="Path to PEM-encoded private key on disk"
    )
    is_active: bool = Field(True, description="Whether this key is used for signing")
    is_published: bool = Field(
        True, description="Whether this key appears in the JWKS document"
    )

    table_comment: ClassVar[str] = "OIDC signing keys for ID token issuance"

    class Create(BaseModel):
        kid: str
        algorithm: str = "RS256"
        public_key_pem: str
        private_key_path: str
        is_active: bool = True
        is_published: bool = True

    class Update(BaseModel):
        algorithm: Optional[str] = None
        is_active: Optional[bool] = None
        is_published: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        kid: Optional[StringSearchModel] = None
        algorithm: Optional[StringSearchModel] = None
        is_active: Optional[bool] = None
        is_published: Optional[bool] = None


class OIDCProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side OIDC provider configuration."""

    issuer_url: str = Field(..., description="OIDC issuer URL (iss claim)")
    signing_algorithm: str = Field("RS256", description="Default signing algorithm")
    id_token_ttl_minutes: int = Field(60, description="ID token lifetime in minutes")
    supported_scopes: str = Field(
        "openid profile email", description="Space-delimited supported OIDC scopes"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "OIDC provider server configuration"

    class Create(BaseModel):
        issuer_url: str
        signing_algorithm: str = "RS256"
        id_token_ttl_minutes: int = 60
        supported_scopes: str = "openid profile email"
        is_enabled: bool = True

    class Update(BaseModel):
        issuer_url: Optional[str] = None
        signing_algorithm: Optional[str] = None
        id_token_ttl_minutes: Optional[int] = None
        supported_scopes: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        issuer_url: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class OIDCProviderManager(AbstractBLLManager):
    _model = OIDCProviderConfigModel
