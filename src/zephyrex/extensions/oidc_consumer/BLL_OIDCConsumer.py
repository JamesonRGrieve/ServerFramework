"""OpenID Connect consumer BLL: authenticate via external OIDC providers.

Implements the OIDC authorization code flow with PKCE, provider discovery
via .well-known/openid-configuration, ID token validation (signature,
issuer, audience, expiry), and claim mapping. The ``PyJWT`` and
``cryptography`` libraries are imported lazily at usage time.
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


class OIDCProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Configuration for an external OIDC identity provider."""

    issuer_url: str = Field(
        ..., description="OIDC issuer URL (used for discovery)"
    )
    client_id: str = Field(..., description="Client ID registered at the provider")
    client_secret: Optional[str] = Field(
        None, description="Client secret (plaintext; use CredentialRef for production; None for public clients)"
    )
    redirect_uri: str = Field(..., description="Redirect URI for the callback")
    scopes: str = Field(
        "openid profile email", description="Space-delimited OIDC scopes"
    )
    use_pkce: bool = Field(True, description="Use PKCE for the authorization code flow")
    claim_mapping_email: str = Field(
        "email", description="ID token claim for user email"
    )
    claim_mapping_name: str = Field(
        "name", description="ID token claim for display name"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "OIDC provider configurations for consumer auth"

    class Create(BaseModel):
        issuer_url: str
        client_id: str
        client_secret: Optional[str] = None
        redirect_uri: str
        scopes: str = "openid profile email"
        use_pkce: bool = True
        claim_mapping_email: str = "email"
        claim_mapping_name: str = "name"
        is_enabled: bool = True

    class Update(BaseModel):
        issuer_url: Optional[str] = None
        client_id: Optional[str] = None
        client_secret: Optional[str] = None
        redirect_uri: Optional[str] = None
        scopes: Optional[str] = None
        use_pkce: Optional[bool] = None
        claim_mapping_email: Optional[str] = None
        claim_mapping_name: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        issuer_url: Optional[StringSearchModel] = None
        client_id: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserOIDCLinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Link between a local user and their OIDC identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    provider_config_id: str = Field(..., description="FK to OIDCProviderConfigModel")
    subject: str = Field(..., description="OIDC 'sub' claim (stable user identifier)")
    email: Optional[str] = Field(None, description="Email from ID token claims")
    display_name: Optional[str] = Field(None, description="Name from ID token claims")
    last_login_at: Optional[datetime] = Field(None, description="Last successful OIDC auth")

    table_comment: ClassVar[str] = "Links a local user to an OIDC identity"

    class Create(BaseModel):
        user_id: str
        provider_config_id: str
        subject: str
        email: Optional[str] = None
        display_name: Optional[str] = None

    class Update(BaseModel):
        email: Optional[str] = None
        display_name: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        provider_config_id: Optional[StringSearchModel] = None
        subject: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class OIDCConsumerManager(AbstractBLLManager):
    _model = OIDCProviderConfigModel
