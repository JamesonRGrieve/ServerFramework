# SPDX-License-Identifier: AGPL-3.0-or-later
"""This server as an internal certificate authority issuing TLS client
certificates to its users and their devices.

Routes (all under ``/v1/x509_provider``):

- ``POST /certificate/issue``: a signed-in user submits a PKCS#10 CSR (the
  private key never leaves them) and gets a certificate for their own
  account. The CSR is checked (its signature, its key: see
  ``CertificateAuthority``); its subject and extensions are ignored, the
  certificate naming the account as the server knows it. Lifetimes are
  short by policy (``X509_PROVIDER_CERT_LIFETIME_DAYS``, 30 by default; a
  request may ask for less), and a user gets at most
  ``X509_PROVIDER_MAX_ISSUANCES_PER_DAY`` (10 by default) in any 24 hours.
  Root or system may issue for another user by naming ``user_id``.
- ``GET /certificate``, ``/certificate/{id}``, ``/certificate/search``:
  the issuance record, which only its owner (and root/system) sees. It is
  never edited or deleted, only revoked.
- ``POST /certificate/{id}/revoke``: the owner, root or system revokes it.
- ``GET /crl/{ca fingerprint}``: the CA's CRL (DER), signed when asked for,
  listing every unexpired revoked certificate it issued. Issued
  certificates carry this URL as their CRL distribution point when
  ``X509_PROVIDER_BASE_URL`` (else ``SERVER_URI``) is set.
- ``GET /ca`` and ``GET /ca/{fingerprint}`` (DER): the CA certificates, to
  install as trust anchors; the latter is the certificates' caIssuers URL.
- ``POST /ca/generate`` and ``POST /ca/import`` (root or system): create or
  import a CA. Its key is held as a secret provider setting (encrypted at
  rest, never returned; see ``PRV_X509CertificateAuthority``).

There is no OCSP responder: custom routes take JSON bodies, and OCSP
clients POST DER.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, List, NoReturn, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException, Response
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import (
    is_any_internal_id,
    is_root_id,
    is_system_id,
)
from zephyrex.extensions.x509_provider.CertificateAuthority import (
    DEFAULT_CA_KEY_TYPE,
    MAX_PEM_CHARS,
    Authority,
    CertificateRefused,
    Identity,
    KeyType,
    Publication,
    Revocation,
    RevocationReason,
    certificate_pem,
    checked_csr,
    fingerprint,
)
from zephyrex.extensions.x509_provider.PRV_X509CertificateAuthority import (
    CERTIFICATE_SETTING,
    KEY_SETTING,
    AuthorityMissing,
    PRV_X509CertificateAuthority,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserManager, UserModel
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

PREFIX = "/v1/x509_provider"
TAGS = ("X.509 Certificate Authority",)
READ_ONLY = [RouteType.GET, RouteType.LIST, RouteType.SEARCH]
CERTIFICATE_MEDIA_TYPE = "application/pkix-cert"
CRL_MEDIA_TYPE = "application/pkix-crl"
FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"

DEFAULT_CERT_LIFETIME_DAYS = 30
CERT_LIFETIME_BOUNDS = (1, 365)
DEFAULT_MAX_ISSUANCES_PER_DAY = 10
MAX_ISSUANCES_BOUNDS = (1, 1000)
ISSUANCE_WINDOW = timedelta(hours=24)
DEFAULT_CRL_LIFETIME_HOURS = 24
CRL_LIFETIME_BOUNDS = (1, 168)
DEFAULT_CA_LIFETIME_DAYS = 3650
MAX_CA_LIFETIME_DAYS = 7300
MAX_NAME_LENGTH = 255
# Generating an RSA-4096 CA key takes seconds; it runs off the event loop.
KEY_GENERATION_TIMEOUT_SECONDS = 60.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _root() -> str:
    return env("ROOT_ID")


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM administer the CA and act for other users."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def _bounded(name: str, default: int, bounds: Tuple[int, int]) -> int:
    """The integer environment value ``name`` within ``bounds``
    (``default`` when unset or not a number)."""
    try:
        value = int(env(name) or default)
    except ValueError:
        value = default
    low, high = bounds
    return min(max(value, low), high)


def lifetime_policy_days() -> int:
    return _bounded(
        "X509_PROVIDER_CERT_LIFETIME_DAYS",
        DEFAULT_CERT_LIFETIME_DAYS,
        CERT_LIFETIME_BOUNDS,
    )


def max_issuances_per_day() -> int:
    return _bounded(
        "X509_PROVIDER_MAX_ISSUANCES_PER_DAY",
        DEFAULT_MAX_ISSUANCES_PER_DAY,
        MAX_ISSUANCES_BOUNDS,
    )


def crl_lifetime() -> timedelta:
    return timedelta(
        hours=_bounded(
            "X509_PROVIDER_CRL_LIFETIME_HOURS",
            DEFAULT_CRL_LIFETIME_HOURS,
            CRL_LIFETIME_BOUNDS,
        )
    )


def publication(authority: Authority) -> Publication:
    """The CRL and CA URLs issued certificates carry: none without a base
    URL, since a relative one is useless to a relying party."""
    base = (env("X509_PROVIDER_BASE_URL") or env("SERVER_URI")).rstrip("/")
    if not base:
        return Publication()
    return Publication(
        crl_url=f"{base}{PREFIX}/crl/{authority.fingerprint}",
        ca_issuers_url=f"{base}{PREFIX}/ca/{authority.fingerprint}",
    )


def _refused(exc: CertificateRefused) -> NoReturn:
    raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class X509IssuedCertificateModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A client certificate this CA issued: whose it is, what it says, and
    whether it is revoked."""

    subject_dn: str = Field(..., description="The certificate's subject DN")
    serial_number: str = Field(..., description="The serial number (hex)")
    fingerprint_sha256: str = Field(..., description="The certificate's SHA-256")
    ca_fingerprint_sha256: str = Field(..., description="The issuing CA's SHA-256")
    not_before: datetime = Field(..., description="Valid from")
    not_after: datetime = Field(..., description="Valid until")
    certificate_pem: str = Field(..., description="The certificate (PEM)")
    is_revoked: bool = Field(False, description="Whether it has been revoked")
    revoked_at: Optional[datetime] = Field(None, description="When it was revoked")
    revocation_reason: Optional[str] = Field(None, description="Why it was revoked")

    table_comment: ClassVar[str] = "Client certificates issued by this server's CA"

    class Create(BaseModel):
        user_id: Optional[str] = None
        subject_dn: str
        serial_number: str
        fingerprint_sha256: str
        ca_fingerprint_sha256: str
        not_before: datetime
        not_after: datetime
        certificate_pem: str

    class Update(BaseModel):
        is_revoked: Optional[bool] = None
        revoked_at: Optional[datetime] = None
        revocation_reason: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        serial_number: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        ca_fingerprint_sha256: Optional[StringSearchModel] = None
        is_revoked: Optional[bool] = None
        not_after: Optional[DateSearchModel] = None


class IssueRequest(RouteModel):
    csr_pem: str = Field(
        ..., max_length=MAX_PEM_CHARS, description="A PKCS#10 CSR (PEM)"
    )
    lifetime_days: Optional[int] = Field(
        None, ge=1, description="Days valid (at most the policy's)"
    )
    user_id: Optional[str] = Field(
        None, description="Whom to issue for (root or system only)"
    )


class IssuedCertificate(RouteModel):
    certificate: X509IssuedCertificateModel
    chain_pem: str = Field(..., description="The issuing CA's certificate (PEM)")


class RevokeRequest(RouteModel):
    reason: RevocationReason = Field("unspecified", description="The CRL reason")


class AuthorityView(RouteModel):
    """A CA as anyone may see it: never its key."""

    id: Optional[str] = Field(
        None, description="Its provider instance (None: the environment's)"
    )
    name: str
    fingerprint_sha256: str
    subject_dn: str
    not_before: datetime
    not_after: datetime
    certificate_pem: str
    enabled: bool
    active: bool = Field(..., description="Whether it is the one issuing")


class AuthorityList(RouteModel):
    authorities: List[AuthorityView]


class GenerateAuthorityRequest(RouteModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    common_name: str = Field(..., min_length=1, description="The CA's subject CN")
    key_type: KeyType = Field(DEFAULT_CA_KEY_TYPE)
    lifetime_days: int = Field(DEFAULT_CA_LIFETIME_DAYS, ge=1, le=MAX_CA_LIFETIME_DAYS)


class ImportAuthorityRequest(RouteModel):
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LENGTH)
    certificate_pem: str = Field(..., max_length=MAX_PEM_CHARS)
    key_pem: str = Field(..., max_length=MAX_PEM_CHARS)
    key_passphrase: Optional[str] = Field(
        None, description="When the key PEM is encrypted"
    )


# ---------------------------------------------------------------------------
# The CAs (provider instances)
# ---------------------------------------------------------------------------


class Authorities:
    """The CA provider instances, read and written as root."""

    def __init__(self, model_registry: Any) -> None:
        self.model_registry = model_registry

    def _provider_id(self) -> str:
        provider = ProviderManager(
            model_registry=self.model_registry, requester_id=_root()
        ).get(name=PRV_X509CertificateAuthority.name)
        return str(provider.id)

    def instances(self) -> List[ProviderInstanceModel]:
        found: List[ProviderInstanceModel] = ProviderInstanceManager(
            model_registry=self.model_registry, requester_id=_root()
        ).list(provider_id=self._provider_id())
        return found

    def _active_instance(self) -> Optional[ProviderInstanceModel]:
        enabled = [i for i in self.instances() if i.enabled]
        if not enabled:
            return None
        return max(enabled, key=lambda i: ensure_utc(i.created_at))

    def active(self) -> Authority:
        """The CA that issues: the newest enabled instance's, else the
        environment's. 503 when there is none or it is unusable."""
        try:
            return PRV_X509CertificateAuthority.authority(self._active_instance())
        except AuthorityMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    def _holders(self) -> List[Tuple[Optional[ProviderInstanceModel], Any]]:
        """Each instance (and the environment, as None) with the CA
        certificate it holds, keys untouched."""
        holders: List[Tuple[Optional[ProviderInstanceModel], Any]] = []
        for instance in [*self.instances(), None]:
            certificate = PRV_X509CertificateAuthority.certificate(instance)
            if certificate is not None:
                holders.append((instance, certificate))
        return holders

    def by_fingerprint(self, ca_fingerprint: str) -> Authority:
        """The CA whose certificate has this fingerprint, enabled or not.
        404 when none does."""
        for instance, certificate in self._holders():
            if fingerprint(certificate) == ca_fingerprint:
                try:
                    return PRV_X509CertificateAuthority.authority(instance)
                except AuthorityMissing as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from None
        raise HTTPException(status_code=404, detail="No such certificate authority")

    def published(self) -> List[AuthorityView]:
        active = self._active_instance()
        views = []
        for instance, certificate in self._holders():
            views.append(
                AuthorityView(
                    id=str(instance.id) if instance is not None else None,
                    name=instance.name if instance is not None else "environment",
                    fingerprint_sha256=fingerprint(certificate),
                    subject_dn=certificate.subject.rfc4514_string(),
                    not_before=certificate.not_valid_before_utc,
                    not_after=certificate.not_valid_after_utc,
                    certificate_pem=certificate_pem(certificate),
                    enabled=bool(instance.enabled) if instance is not None else True,
                    active=(
                        instance.id == active.id
                        if instance is not None and active is not None
                        else instance is None and active is None
                    ),
                )
            )
        return views

    def store(self, name: str, authority: Authority) -> AuthorityView:
        """``authority`` as a new instance (the newest, so the one issuing).
        409 when that CA is already held."""
        if any(
            fingerprint(certificate) == authority.fingerprint
            for _, certificate in self._holders()
        ):
            raise HTTPException(
                status_code=409, detail="That certificate authority is already held"
            )
        instances = ProviderInstanceManager(
            model_registry=self.model_registry, requester_id=_root()
        )
        instance = instances.create(
            name=name, provider_id=self._provider_id(), scope="root"
        )
        settings = ProviderInstanceSettingManager(
            model_registry=self.model_registry, requester_id=_root()
        )
        try:
            settings.create(
                provider_instance_id=instance.id,
                key=CERTIFICATE_SETTING,
                value=authority.certificate_pem,
            )
            settings.create(
                provider_instance_id=instance.id,
                key=KEY_SETTING,
                value=authority.key_pem(),
            )
        except Exception:
            # An instance holding a certificate but no key would publish a
            # CA that cannot sign; leave nothing behind.
            instances.delete(id=instance.id)
            raise
        for view in self.published():
            if view.id == str(instance.id):
                return view
        raise HTTPException(status_code=500, detail="The stored CA cannot be read")


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class X509IssuedCertificateManager(AbstractBLLManager, RouterMixin):
    """Issuance records: each is its owner's. They are made only by
    :meth:`issue` and changed only by :meth:`revoke`; nothing deletes one
    (a deleted record would drop its serial from the CRL)."""

    _model = X509IssuedCertificateModel
    prefix: ClassVar[Optional[str]] = f"{PREFIX}/certificate"
    tags: ClassVar[Optional[List[str]]] = list(TAGS)
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = READ_ONLY

    def create(self, **kwargs: Any) -> Any:
        raise HTTPException(
            status_code=405, detail="Certificates are issued from a CSR, at /issue"
        )

    def update(self, id: str, **kwargs: Any) -> Any:
        raise HTTPException(
            status_code=405, detail="An issued certificate is only ever revoked"
        )

    def delete(self, id: str) -> None:
        raise HTTPException(
            status_code=405, detail="Issued certificates are kept; revoke instead"
        )

    def _owner(self, user_id: Optional[str]) -> Any:
        """The account to issue for: the requester, or for root or system
        the user named, looked up as the requester."""
        requester_id = self.requester.id
        owner_id = user_id if user_id and _server_side(requester_id) else requester_id
        if is_any_internal_id(owner_id):
            raise HTTPException(
                status_code=400,
                detail="Client certificates are issued to user accounts",
            )
        user = UserManager(
            model_registry=self.model_registry, requester_id=requester_id
        ).get(id=owner_id)
        if user.active is False:
            raise HTTPException(status_code=403, detail="The account is not active")
        return user

    def _lifetime(self, lifetime_days: Optional[int]) -> timedelta:
        policy = lifetime_policy_days()
        days = policy if lifetime_days is None else lifetime_days
        if not 1 <= days <= policy:
            raise HTTPException(status_code=400, detail=f"lifetime_days is 1-{policy}")
        return timedelta(days=days)

    def _within_rate(self, owner_id: str, now: datetime) -> None:
        issued = X509IssuedCertificateManager(
            model_registry=self.model_registry, requester_id=_root()
        ).count(user_id=owner_id, created_at={"after": now - ISSUANCE_WINDOW})
        limit = max_issuances_per_day()
        if issued >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"At most {limit} certificates are issued per user per day",
                headers={"Retry-After": str(int(ISSUANCE_WINDOW.total_seconds()))},
            )

    def issue(
        self,
        csr_pem: str,
        lifetime_days: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> IssuedCertificate:
        """A certificate for the CSR's key, naming the account (never the
        CSR's claims), and its record."""
        user = self._owner(user_id)
        try:
            csr = checked_csr(csr_pem)
        except CertificateRefused as exc:
            _refused(exc)
        lifetime = self._lifetime(lifetime_days)
        now = _now()
        self._within_rate(str(user.id), now)
        authority = Authorities(self.model_registry).active()
        identity = Identity(
            user_id=str(user.id), username=user.username, email=user.email
        )
        try:
            certificate = authority.issue(
                csr.public_key(), identity, lifetime, now, publication(authority)
            )
        except CertificateRefused as exc:
            _refused(exc)
        record = super().create(
            user_id=str(user.id),
            subject_dn=certificate.subject.rfc4514_string(),
            serial_number=format(certificate.serial_number, "x"),
            fingerprint_sha256=fingerprint(certificate),
            ca_fingerprint_sha256=authority.fingerprint,
            not_before=certificate.not_valid_before_utc,
            not_after=certificate.not_valid_after_utc,
            certificate_pem=certificate_pem(certificate),
        )
        return IssuedCertificate(
            certificate=record, chain_pem=authority.certificate_pem
        )

    def revoke(self, id: str, reason: str = "unspecified") -> Any:
        """Revoke a certificate the requester owns (root and system: any).
        404 for one they cannot see; 409 when it is already revoked."""
        record = self.get(id=id)
        if not _server_side(self.requester.id) and record.user_id != self.requester.id:
            raise HTTPException(status_code=404, detail="No such certificate")
        if record.is_revoked:
            raise HTTPException(status_code=409, detail="It is already revoked")
        return super().update(
            id, is_revoked=True, revoked_at=_now(), revocation_reason=reason
        )

    @custom_route(
        method="POST",
        path="/issue",
        input_model=IssueRequest,
        output_model=IssuedCertificate,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Issue a client certificate for a CSR",
        expose_in=(ExposeIn.REST,),
    )
    def issue_route(self, body: IssueRequest) -> IssuedCertificate:
        return self.issue(body.csr_pem, body.lifetime_days, body.user_id)

    @custom_route(
        method="POST",
        path="/{certificate_id}/revoke",
        input_model=RevokeRequest,
        output_model=X509IssuedCertificateModel,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Revoke an issued certificate",
        expose_in=(ExposeIn.REST,),
    )
    def revoke_route(
        self, certificate_id: str, body: RevokeRequest
    ) -> X509IssuedCertificateModel:
        revoked: X509IssuedCertificateModel = self.revoke(certificate_id, body.reason)
        return revoked


def revocation_list_der(model_registry: Any, ca_fingerprint: str) -> bytes:
    """The CRL of the CA with this fingerprint, signed now (DER)."""
    authority = Authorities(model_registry).by_fingerprint(ca_fingerprint)
    now = _now()
    records = X509IssuedCertificateManager(
        model_registry=model_registry, requester_id=_root()
    ).list(ca_fingerprint_sha256=ca_fingerprint, is_revoked=True)
    revoked = [
        Revocation(
            serial_number=int(record.serial_number, 16),
            revoked_at=ensure_utc(record.revoked_at or record.updated_at or now),
            reason=record.revocation_reason or "unspecified",
        )
        for record in records
        if ensure_utc(record.not_after) > now
    ]
    crl = authority.revocation_list(revoked, now, crl_lifetime())
    return crl.public_bytes(serialization.Encoding.DER)


class X509AuthorityManager(AbstractBLLManager, RouterMixin):
    """The CAs: published to anyone, administered by root and system."""

    prefix: ClassVar[Optional[str]] = PREFIX
    tags: ClassVar[Optional[List[str]]] = list(TAGS)
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[Any]]] = []

    def _administrator(self) -> None:
        if not _server_side(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Certificate authorities are managed by the administrator",
            )

    @staticmethod
    def _fingerprint(value: str) -> str:
        normalized = value.lower()
        if not re.match(FINGERPRINT_PATTERN, normalized):
            raise HTTPException(status_code=404, detail="No such certificate authority")
        return normalized

    @custom_route(
        method="GET",
        path="/ca",
        output_model=AuthorityList,
        authentication_type="none",
        openapi_tags=TAGS,
        summary="The certificate authorities (trust anchors)",
        expose_in=(ExposeIn.REST,),
    )
    def list_route(self) -> AuthorityList:
        return AuthorityList(authorities=Authorities(self.model_registry).published())

    @custom_route(
        method="GET",
        path="/ca/{ca_fingerprint}",
        authentication_type="none",
        response_class=Response,
        openapi_tags=TAGS,
        summary="A CA certificate (DER)",
        expose_in=(ExposeIn.REST,),
    )
    def certificate_route(self, ca_fingerprint: str) -> Response:
        wanted = self._fingerprint(ca_fingerprint)
        authority = Authorities(self.model_registry).by_fingerprint(wanted)
        return Response(
            content=authority.certificate.public_bytes(serialization.Encoding.DER),
            media_type=CERTIFICATE_MEDIA_TYPE,
        )

    @custom_route(
        method="GET",
        path="/crl/{ca_fingerprint}",
        authentication_type="none",
        response_class=Response,
        openapi_tags=TAGS,
        summary="A CA's certificate revocation list (DER)",
        expose_in=(ExposeIn.REST,),
    )
    def crl_route(self, ca_fingerprint: str) -> Response:
        return Response(
            content=revocation_list_der(
                self.model_registry, self._fingerprint(ca_fingerprint)
            ),
            media_type=CRL_MEDIA_TYPE,
        )

    @custom_route(
        method="POST",
        path="/ca/generate",
        input_model=GenerateAuthorityRequest,
        output_model=AuthorityView,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Generate a CA (its key never leaves the server)",
        expose_in=(ExposeIn.REST,),
    )
    async def generate_route(self, body: GenerateAuthorityRequest) -> AuthorityView:
        self._administrator()
        lifetime = timedelta(days=body.lifetime_days)
        try:
            authority = await asyncio.wait_for(
                asyncio.to_thread(
                    Authority.generated,
                    body.common_name,
                    body.key_type,
                    lifetime,
                    _now(),
                ),
                timeout=KEY_GENERATION_TIMEOUT_SECONDS,
            )
        except CertificateRefused as exc:
            _refused(exc)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503, detail="CA key generation timed out"
            ) from None
        return Authorities(self.model_registry).store(body.name, authority)

    @custom_route(
        method="POST",
        path="/ca/import",
        input_model=ImportAuthorityRequest,
        output_model=AuthorityView,
        authentication_type="jwt",
        openapi_tags=TAGS,
        summary="Import a CA certificate and its key",
        expose_in=(ExposeIn.REST,),
    )
    def import_route(self, body: ImportAuthorityRequest) -> AuthorityView:
        self._administrator()
        try:
            authority = Authority.loaded(
                body.certificate_pem, body.key_pem, body.key_passphrase
            )
        except CertificateRefused as exc:
            _refused(exc)
        return Authorities(self.model_registry).store(body.name, authority)
