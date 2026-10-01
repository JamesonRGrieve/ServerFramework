"""WebAuthn consumer BLL: authenticate via FIDO2/Passkeys.

Manages WebAuthn credential registration and authentication ceremonies.
Users register authenticators (security keys, platform biometrics) and
authenticate via cryptographic challenge-response. The ``fido2`` library
is imported lazily at usage time.
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


class WebAuthnCredentialModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A WebAuthn credential registered to a local user."""

    user_id: str = Field(..., description="Local user this credential belongs to")
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
        None, description="Space-delimited transport hints (usb, nfc, ble, internal)"
    )
    is_discoverable: bool = Field(
        False, description="Whether this is a discoverable (resident) credential"
    )
    last_used_at: Optional[datetime] = Field(
        None, description="Last successful authentication with this credential"
    )
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "WebAuthn/FIDO2 credentials registered to users"

    class Create(BaseModel):
        user_id: str
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
        user_id: Optional[StringSearchModel] = None
        credential_id: Optional[StringSearchModel] = None
        aaguid: Optional[StringSearchModel] = None
        is_discoverable: Optional[bool] = None
        is_enabled: Optional[bool] = None
        last_used_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class WebAuthnConsumerManager(AbstractBLLManager):
    _model = WebAuthnCredentialModel
