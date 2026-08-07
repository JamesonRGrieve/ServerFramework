"""X.509 certificate provider BLL: this server issues client certificates.

Manages the CA lifecycle: certificate signing requests (CSR), certificate
issuance, revocation, and CRL generation. The ``cryptography`` library
is imported lazily.
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


class IssuedCertificateModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A client certificate issued by this CA."""

    user_id: str = Field(..., description="User the certificate was issued to")
    subject_dn: str = Field(..., description="Certificate subject DN")
    serial_number: str = Field(..., description="Certificate serial number (hex)")
    fingerprint_sha256: str = Field(..., description="Certificate SHA-256 fingerprint")
    not_before: datetime = Field(..., description="Certificate validity start")
    not_after: datetime = Field(..., description="Certificate validity end")
    is_revoked: bool = Field(False, description="Whether the certificate has been revoked")
    revoked_at: Optional[datetime] = Field(None, description="When the certificate was revoked")
    revocation_reason: Optional[str] = Field(None, description="CRL reason code")

    table_comment: ClassVar[str] = "Client certificates issued by this CA"

    class Create(BaseModel):
        user_id: str
        subject_dn: str
        serial_number: str
        fingerprint_sha256: str
        not_before: datetime
        not_after: datetime

    class Update(BaseModel):
        is_revoked: Optional[bool] = None
        revoked_at: Optional[datetime] = None
        revocation_reason: Optional[str] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        serial_number: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        is_revoked: Optional[bool] = None
        not_after: Optional[DateSearchModel] = None


class X509ProviderConfigModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Server-side CA configuration."""

    ca_cert_path: str = Field(..., description="Path to CA certificate PEM")
    ca_key_path: str = Field(..., description="Path to CA private key PEM")
    crl_distribution_url: Optional[str] = Field(
        None, description="CRL distribution point URL embedded in issued certs"
    )
    cert_lifetime_days: int = Field(365, description="Default certificate lifetime")
    key_size: int = Field(4096, description="RSA key size for generated certificates")
    signature_algorithm: str = Field("SHA256", description="Signature hash algorithm")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "X.509 CA provider configuration"

    class Create(BaseModel):
        ca_cert_path: str
        ca_key_path: str
        crl_distribution_url: Optional[str] = None
        cert_lifetime_days: int = 365
        key_size: int = 4096
        signature_algorithm: str = "SHA256"
        is_enabled: bool = True

    class Update(BaseModel):
        crl_distribution_url: Optional[str] = None
        cert_lifetime_days: Optional[int] = None
        key_size: Optional[int] = None
        signature_algorithm: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        is_enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class X509ProviderManager(AbstractBLLManager):
    _model = X509ProviderConfigModel
