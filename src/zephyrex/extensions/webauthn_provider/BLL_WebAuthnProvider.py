"""WebAuthn provider BLL: this server acts as a WebAuthn relying party.

Exposes WebAuthn/FIDO2 registration and assertion endpoints so third-party
applications can delegate passkey authentication to this server. Manages
relying-party configuration and registered credential records on behalf of
external consumers. The ``fido2`` library is imported lazily at usage time.
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
# Database models
# ---------------------------------------------------------------------------


class WebAuthnRelyingPartyModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Relying-party configuration for this WebAuthn provider."""

    rp_id: str = Field(..., description="Relying party identifier (domain)")
    rp_name: str = Field(..., description="Human-readable relying party name")
    origin: str = Field(
        ..., description="Expected origin for ceremony validation"
    )
    attestation: str = Field(
        "none",
        description="Attestation conveyance preference (none, indirect, direct, enterprise)",
    )
    user_verification: str = Field(
        "preferred",
        description="User verification requirement (required, preferred, discouraged)",
    )
    timeout_ms: int = Field(
        60000, description="Ceremony timeout in milliseconds"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "WebAuthn relying-party configuration served by this instance"

    class Create(BaseModel):
        rp_id: str
        rp_name: str
        origin: str
        attestation: str = "none"
        user_verification: str = "preferred"
        timeout_ms: int = 60000
        is_enabled: bool = True

    class Update(BaseModel):
        rp_name: Optional[str] = None
        origin: Optional[str] = None
        attestation: Optional[str] = None
        user_verification: Optional[str] = None
        timeout_ms: Optional[int] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        rp_id: Optional[StringSearchModel] = None
        rp_name: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class WebAuthnProviderCredentialModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A WebAuthn credential registered through this relying party on behalf of an external consumer."""

    rp_id: str = Field(..., description="Relying party this credential belongs to")
    external_user_id: str = Field(
        ..., description="User identifier from the consuming application"
    )
    credential_id: str = Field(
        ..., description="Base64url-encoded credential ID"
    )
    public_key: str = Field(
        ..., description="Base64url-encoded COSE public key"
    )
    sign_count: int = Field(
        0, description="Authenticator signature counter for clone detection"
    )
    aaguid: Optional[str] = Field(
        None, description="Authenticator Attestation GUID"
    )
    device_name: Optional[str] = Field(
        None, description="User-assigned name for this authenticator"
    )
    transports: Optional[str] = Field(
        None,
        description="Space-delimited transport hints (usb, nfc, ble, internal)",
    )
    is_discoverable: bool = Field(
        False, description="Whether this is a discoverable (resident) credential"
    )
    last_used_at: Optional[datetime] = Field(
        None, description="Last successful authentication with this credential"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "WebAuthn credentials registered via this relying party"

    class Create(BaseModel):
        rp_id: str
        external_user_id: str
        credential_id: str
        public_key: str
        sign_count: int = 0
        aaguid: Optional[str] = None
        device_name: Optional[str] = None
        transports: Optional[str] = None
        is_discoverable: bool = False

    class Update(BaseModel):
        sign_count: Optional[int] = None
        device_name: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        rp_id: Optional[StringSearchModel] = None
        external_user_id: Optional[StringSearchModel] = None
        credential_id: Optional[StringSearchModel] = None
        aaguid: Optional[StringSearchModel] = None
        is_discoverable: Optional[bool] = None
        is_enabled: Optional[bool] = None
        last_used_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class WebAuthnProviderManager(AbstractBLLManager):
    _model = WebAuthnRelyingPartyModel
