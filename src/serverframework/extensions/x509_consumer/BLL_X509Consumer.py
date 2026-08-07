"""X.509 client certificate consumer BLL.

Maps X.509 client certificate identities (subject DN, fingerprint) to
local users. Certificates are validated by the TLS terminator; this
extension trusts the headers it sets. The ``cryptography`` library is
imported lazily for certificate parsing.
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


class X509TrustedCAModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A trusted Certificate Authority for client certificate validation."""

    name: str = Field(..., description="Friendly name for this CA")
    ca_cert_pem: str = Field(..., description="PEM-encoded CA certificate")
    fingerprint_sha256: str = Field(
        ..., description="SHA-256 fingerprint of the CA certificate"
    )
    crl_url: Optional[str] = Field(None, description="CRL distribution point URL")
    is_enabled: bool = Field(True)

    table_comment: ClassVar[str] = "Trusted CAs for X.509 client certificate auth"

    class Create(BaseModel):
        name: str
        ca_cert_pem: str
        fingerprint_sha256: str
        crl_url: Optional[str] = None
        is_enabled: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        crl_url: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserX509LinkModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """Link between a local user and their X.509 certificate identity."""

    user_id: str = Field(..., description="Local user this link belongs to")
    subject_dn: str = Field(..., description="Certificate subject DN")
    fingerprint_sha256: str = Field(..., description="Certificate SHA-256 fingerprint")
    issuer_dn: Optional[str] = Field(None, description="Certificate issuer DN")
    serial_number: Optional[str] = Field(None, description="Certificate serial number")
    not_after: Optional[datetime] = Field(None, description="Certificate expiry")
    last_login_at: Optional[datetime] = Field(None, description="Last successful cert auth")

    table_comment: ClassVar[str] = "Links a local user to an X.509 certificate identity"

    class Create(BaseModel):
        user_id: str
        subject_dn: str
        fingerprint_sha256: str
        issuer_dn: Optional[str] = None
        serial_number: Optional[str] = None
        not_after: Optional[datetime] = None

    class Update(BaseModel):
        issuer_dn: Optional[str] = None
        serial_number: Optional[str] = None
        not_after: Optional[datetime] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        user_id: Optional[StringSearchModel] = None
        subject_dn: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class X509ConsumerManager(AbstractBLLManager):
    _model = X509TrustedCAModel
