# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in with a client certificate (mutual TLS).

``POST /v1/auth/x509/login`` signs in whoever presented a client
certificate during the TLS handshake. The certificate reaches the app one of
two ways, and only one is believed for a given request:

- through a TLS terminator (nginx, HAProxy, Traefik, Envoy) that forwards it
  in ``X509_CONSUMER_CERT_HEADER`` (default ``X-SSL-Client-Cert``), believed
  only when the connection comes from an address in
  ``X509_CONSUMER_TRUSTED_PROXIES``. From anyone else the header is ignored:
  any client can send it. The terminator must set (or clear) the header on
  every request. From a trusted proxy, the TLS connection's own client
  certificate is the proxy's, and never the user's;
- from the ASGI server, when it terminates TLS itself, through the ASGI TLS
  extension (``scope["extensions"]["tls"]["client_cert_chain"]``).

Either way the certificate is verified here again, whatever the terminator
decided: its path to one of the trust anchors ROOT configured (an
:class:`X509TrustAnchorModel`, a root or an issuing CA), its validity period,
an extendedKeyUsage naming clientAuth, and, as the anchor says, its
revocation by CRL and/or OCSP, failing closed when the anchor requires a
status. The anchor's ``identity_attribute`` (a subject DN component, a
subjectAltName email, UPN, URI or DNS name, or the whole subject) is read
from the leaf.

A user is found by a :class:`UserX509LinkModel` keyed by (trust anchor,
identity): the anchor is the authority that vouched for the attribute, so
the same name under another anchor is another person, and keying on the
attribute rather than the serial lets a renewed certificate keep its
account. With no link, an anchor ``trusted_for_email`` may link the
certificate's email to the account that has it; otherwise a new account is
made as ``REGISTRATION_MODE`` allows (``invite`` needs a pending invitation
for an email the anchor vouches for). Links are made only by a verified
sign-in or by ROOT/SYSTEM, never claimed by a user. A success issues the
session password login issues (or its MFA challenge).
"""

import ipaddress
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from cryptography import x509
from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.x509_consumer.X509Verification import (
    DEFAULT_IDENTITY_ATTRIBUTE,
    CertificateEncodingError,
    CertificateRefusedError,
    RevocationCheck,
    RevocationChecker,
    TrustAnchor,
    check_leaf,
    email_of,
    fingerprint_sha256,
    identity_of,
    load_ca_certificate,
    parse_forwarded_chain,
    parse_tls_extension_chain,
    pem_of,
    validate_identity_attribute,
    verify_chain,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib import Environment
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    _parse_trusted_proxies,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    UserManager,
    UserModel,
    _invitation_hooks,
    refuse_internal_account,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

ROUTE_PREFIX = "/v1/auth/x509"
X509_TAGS = ("X.509 Sign-in",)
DEFAULT_CERT_HEADER = "X-SSL-Client-Cert"
_ASGI_TLS_EXTENSION = "tls"


def _server_side(requester_id: Optional[str]) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return bool(requester_id) and (
        is_root_id(str(requester_id)) or is_system_id(str(requester_id))
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _refused(exc: CertificateRefusedError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class X509TrustAnchorModel(
    ApplicationModel,
    UpdateMixinModel,
    metaclass=ModelMeta,
):
    """A trust anchor for client certificates, managed by ROOT: certificates
    with a valid path to it sign in, identified by ``identity_attribute``."""

    name: str = Field(..., description="Friendly name for this trust anchor")
    ca_cert_pem: str = Field(..., description="PEM-encoded CA certificate")
    fingerprint_sha256: Optional[str] = Field(
        None, description="SHA-256 fingerprint of the CA certificate (computed)"
    )
    subject_dn: Optional[str] = Field(
        None, description="The CA certificate's subject DN (computed)"
    )
    identity_attribute: str = Field(
        DEFAULT_IDENTITY_ATTRIBUTE,
        description="What names the user: subject, subject:CN|UID|emailAddress|"
        "serialNumber, or san:email|upn|uri|dns",
    )
    trusted_for_email: bool = Field(
        False,
        description="The CA vouches for the emails in its certificates: a "
        "certificate may sign in to the account with its email",
    )
    revocation_check: str = Field(
        "crl", description="How revocation is checked: none, crl, ocsp, ocsp_then_crl"
    )
    revocation_required: bool = Field(
        True,
        description="Refuse a certificate whose revocation status cannot be "
        "established (fail closed)",
    )
    crl_url: Optional[str] = Field(
        None,
        description="CRL for the certificates this CA issues itself, tried before "
        "their own distribution points",
    )
    ocsp_url: Optional[str] = Field(
        None,
        description="OCSP responder for the certificates this CA issues itself, "
        "tried before their own AIA",
    )
    is_enabled: bool = Field(True, description="Whether certificates under it sign in")

    table_comment: ClassVar[str] = "Trust anchors for X.509 client certificate sign-in"
    is_system_entity: ClassVar[bool] = True

    class Create(BaseModel):
        name: str
        ca_cert_pem: str
        fingerprint_sha256: Optional[str] = Field(
            None,
            description="Computed from the certificate; any value sent is replaced",
        )
        subject_dn: Optional[str] = Field(
            None,
            description="Computed from the certificate; any value sent is replaced",
        )
        identity_attribute: str = DEFAULT_IDENTITY_ATTRIBUTE
        trusted_for_email: bool = False
        revocation_check: RevocationCheck = "crl"
        revocation_required: bool = True
        crl_url: Optional[str] = None
        ocsp_url: Optional[str] = None
        is_enabled: bool = True

    # The certificate and identity attribute are fixed: links are keyed on
    # what they name. Replace an anchor by making a new one.
    class Update(BaseModel):
        name: Optional[str] = None
        trusted_for_email: Optional[bool] = None
        revocation_check: Optional[RevocationCheck] = None
        revocation_required: Optional[bool] = None
        crl_url: Optional[str] = None
        ocsp_url: Optional[str] = None
        is_enabled: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


class UserX509LinkModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A certificate identity, under one trust anchor, that signs in as a user."""

    trust_anchor_id: str = Field(
        ..., description="The trust anchor that vouches for it"
    )
    identity: str = Field(
        ..., description="The anchor's identity attribute, as the certificate gives it"
    )
    subject_dn: Optional[str] = Field(None, description="Last certificate's subject DN")
    issuer_dn: Optional[str] = Field(None, description="Last certificate's issuer DN")
    serial_number: Optional[str] = Field(
        None, description="Last certificate's serial number (hex)"
    )
    fingerprint_sha256: Optional[str] = Field(
        None, description="Last certificate's SHA-256 fingerprint"
    )
    not_after: Optional[datetime] = Field(None, description="Last certificate's expiry")
    last_login_at: Optional[datetime] = Field(
        None, description="Last successful certificate sign-in"
    )

    table_comment: ClassVar[str] = "Links a local user to an X.509 certificate identity"

    class Create(BaseModel, UserModel.Reference.ID):
        trust_anchor_id: str
        identity: str

    class Update(BaseModel):
        subject_dn: Optional[str] = Field(None, description="Set by sign-in only")
        issuer_dn: Optional[str] = Field(None, description="Set by sign-in only")
        serial_number: Optional[str] = Field(None, description="Set by sign-in only")
        fingerprint_sha256: Optional[str] = Field(
            None, description="Set by sign-in only"
        )
        not_after: Optional[datetime] = Field(None, description="Set by sign-in only")
        last_login_at: Optional[datetime] = Field(
            None, description="Set by sign-in only"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        trust_anchor_id: Optional[StringSearchModel] = None
        identity: Optional[StringSearchModel] = None
        fingerprint_sha256: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class X509LoginRequest(RouteModel):
    """Nothing: the credential is the TLS connection's client certificate."""


class X509LoginResponse(RouteModel):
    """The password-login response (``user``, ``token``, ``preferences``,
    ``teams``, ``session_key``) for the certificate's user or, for a user
    with a verified second factor, the MFA challenge to complete at POST
    /v1/user/authorize/mfa, exactly as a password login would."""

    identity: str
    trust_anchor_id: str
    user_id: str
    user: Optional[Dict[str, Any]] = None
    token: Optional[str] = Field(
        None, description="JWT to present as Authorization: Bearer"
    )
    session_key: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None
    teams: Optional[List[Dict[str, Any]]] = None
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None


# ---------------------------------------------------------------------------
# The presented certificate
# ---------------------------------------------------------------------------


def _from_trusted_proxy(request: Request) -> bool:
    """The connection's peer is a configured TLS terminator. A malformed
    allow-list is a misconfiguration that trusts no one (503)."""
    peer = request.client.host if request.client else None
    try:
        networks = _parse_trusted_proxies(
            (env("X509_CONSUMER_TRUSTED_PROXIES") or "").strip()
        )
    except ValueError as exc:
        logger.error("x509_consumer: X509_CONSUMER_TRUSTED_PROXIES: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Certificate sign-in is misconfigured",
        ) from None
    if not peer or not networks:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def presented_chain(request: Request) -> List[x509.Certificate]:
    """The certificate chain the client presented, leaf first: from the
    header when a trusted proxy forwarded the request, else from the ASGI
    TLS extension. 401 when there is none; 400 when it is not one."""
    header = env("X509_CONSUMER_CERT_HEADER") or DEFAULT_CERT_HEADER
    forwarded = request.headers.get(header)
    tls: Dict[str, Any] = (request.scope.get("extensions") or {}).get(
        _ASGI_TLS_EXTENSION
    ) or {}
    try:
        if _from_trusted_proxy(request):
            if not forwarded:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="No client certificate was presented",
                )
            return parse_forwarded_chain(forwarded)
        if forwarded:
            logger.warning(
                "x509_consumer: ignored %s from %s, not a trusted proxy",
                header,
                request.client.host if request.client else "an unknown peer",
            )
        if tls.get("client_cert_error"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="The client certificate was refused during the handshake",
            )
        if not tls.get("client_cert_chain"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No client certificate was presented",
            )
        return parse_tls_extension_chain(tls["client_cert_chain"])
    except CertificateEncodingError as exc:
        raise _bad_request(str(exc)) from exc


def _anchor(row: X509TrustAnchorModel) -> TrustAnchor:
    return TrustAnchor(
        id=str(row.id),
        certificate=load_ca_certificate(row.ca_cert_pem),
        identity_attribute=row.identity_attribute,
        trusted_for_email=row.trusted_for_email,
        revocation_check=row.revocation_check,
        revocation_required=row.revocation_required,
        crl_url=row.crl_url,
        ocsp_url=row.ocsp_url,
    )


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class X509TrustAnchorManager(AbstractBLLManager, RouterMixin):
    """Trust anchors: ROOT and SYSTEM add, change and remove them, since
    whoever adds one can sign in as anyone a certificate under it names."""

    _model = X509TrustAnchorModel
    prefix: ClassVar[Optional[str]] = f"{ROUTE_PREFIX}/trust-anchor"
    tags: ClassVar[Optional[List[str]]] = list(X509_TAGS)
    auth_type: ClassVar[AuthType] = AuthType.API_KEY
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.SEARCH,
        RouteType.CREATE,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

    def _require_server_side(self) -> None:
        requester = self.optional_requester
        if requester is None or not _server_side(str(requester.id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only ROOT or SYSTEM manage X.509 trust anchors",
            )

    def _prepared(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """``fields`` with the certificate checked and normalized and the
        computed fields set; 409 for a certificate already an anchor."""
        try:
            certificate = load_ca_certificate(str(fields.get("ca_cert_pem") or ""))
            validate_identity_attribute(
                str(fields.get("identity_attribute") or DEFAULT_IDENTITY_ATTRIBUTE)
            )
        except CertificateEncodingError as exc:
            raise _bad_request(str(exc)) from exc
        fingerprint = fingerprint_sha256(certificate)
        if self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                self.DB.fingerprint_sha256 == fingerprint,
                self.DB.deleted_at.is_(None),
            ],
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That certificate is already a trust anchor",
            )
        fields["ca_cert_pem"] = pem_of(certificate)
        fields["fingerprint_sha256"] = fingerprint
        fields["subject_dn"] = certificate.subject.rfc4514_string()
        return fields

    def create(self, **kwargs: Any) -> Any:
        self._require_server_side()
        if isinstance(kwargs.get("entities"), list):
            kwargs["entities"] = [self._prepared(dict(e)) for e in kwargs["entities"]]
            return super().create(**kwargs)
        return super().create(**self._prepared(dict(kwargs)))

    def update(self, id: str, **kwargs: Any) -> Any:
        self._require_server_side()
        for fixed in ("ca_cert_pem", "identity_attribute", "fingerprint_sha256"):
            kwargs.pop(fixed, None)
        kwargs.pop("subject_dn", None)
        return super().update(id, **kwargs)

    def delete(self, id: str) -> None:
        self._require_server_side()
        super().delete(id)

    def enabled_anchors(self) -> List[TrustAnchor]:
        """The enabled anchors a sign-in may verify against, oldest first."""
        rows: List[X509TrustAnchorModel] = self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=X509TrustAnchorModel,
            filters=[self.DB.is_enabled.is_(True), self.DB.deleted_at.is_(None)],
        )
        anchors: List[TrustAnchor] = []
        for row in sorted(rows, key=lambda r: r.created_at or _now()):
            try:
                anchors.append(_anchor(row))
            except CertificateEncodingError as exc:
                logger.error("x509_consumer: trust anchor %s: %s", row.id, exc)
        return anchors


class UserX509LinkManager(AbstractBLLManager, RouterMixin):
    """A user's certificate identities (read and unlink), and the sign-in."""

    _model = UserX509LinkModel
    prefix: ClassVar[Optional[str]] = ROUTE_PREFIX
    tags: ClassVar[Optional[List[str]]] = list(X509_TAGS)
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CREATE or UPDATE: links come from a verified sign-in, or from ROOT
    # and SYSTEM through ``create``; a user could otherwise claim another's
    # certificate identity.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Link a certificate identity to a user: ROOT and SYSTEM only. The
        link is written as its user, so they can see and remove it."""
        if not _server_side(str(self.requester.id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Certificate identities are linked by signing in with them",
            )
        if isinstance(kwargs.get("entities"), list):
            return [self.create(**dict(entity)) for entity in kwargs["entities"]]
        user_id = str(kwargs.get("user_id") or "")
        trust_anchor_id = str(kwargs.get("trust_anchor_id") or "")
        identity = str(kwargs.get("identity") or "").strip()
        if not user_id or not trust_anchor_id or not identity:
            raise _bad_request("A link names its user, trust anchor and identity")
        refuse_internal_account(user_id)
        self._user(user_id)
        if not self._anchor_exists(trust_anchor_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such trust anchor"
            )
        if self.linked(trust_anchor_id, identity) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That certificate identity is already linked",
            )
        return self._link(trust_anchor_id, identity, user_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only sign-in stamps a link (its last certificate and login)."""
        if not _server_side(str(self.requester.id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Certificate identity links are not edited",
            )
        kwargs.pop("user_id", None)
        return super().update(id, **kwargs)

    def _anchor_exists(self, trust_anchor_id: str) -> bool:
        return bool(
            X509TrustAnchorModel.DB(self.model_registry.DB.manager.Base).list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=trust_anchor_id,
            )
        )

    def _link(self, trust_anchor_id: str, identity: str, user_id: str) -> Any:
        return self.DB.create(
            requester_id=user_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(UserX509LinkModel),
            trust_anchor_id=trust_anchor_id,
            identity=identity,
            user_id=user_id,
        )

    def linked(self, trust_anchor_id: str, identity: str) -> Optional[Any]:
        """The live link for ``identity`` under the anchor (exact match)."""
        links = self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=UserX509LinkModel,
            filters=[
                self.DB.trust_anchor_id == trust_anchor_id,
                self.DB.identity == identity,
                self.DB.deleted_at.is_(None),
            ],
        )
        return links[0] if links else None

    def _users(self, **filters: Any) -> List[Dict[str, Any]]:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        users: List[Dict[str, Any]] = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[UserDB.deleted_at.is_(None)]
            + [getattr(UserDB, key) == value for key, value in filters.items()],
        )
        return users

    def _user(self, user_id: str) -> Dict[str, Any]:
        users = self._users(id=user_id)
        if len(users) != 1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such user"
            )
        return users[0]

    # -- signing in ---------------------------------------------------------

    async def verified(
        self, chain: List[x509.Certificate]
    ) -> Tuple[TrustAnchor, List[x509.Certificate]]:
        """The anchor that vouches for the presented chain, and the validated
        path to it, revocation checked. 503 without any anchor; 401 when
        the certificate does not verify."""
        anchors = X509TrustAnchorManager(
            model_registry=self.model_registry, requester_id=env("ROOT_ID")
        ).enabled_anchors()
        if not anchors:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Certificate sign-in is not configured",
            )
        leaf, intermediates = chain[0], chain[1:]
        now = _now()
        try:
            check_leaf(leaf, now)
            for anchor in anchors:
                path = verify_chain(leaf, intermediates, anchor.certificate, now)
                if path is None:
                    continue
                await RevocationChecker().check_chain(path, anchor, now)
                return anchor, path
        except CertificateRefusedError as exc:
            logger.info("x509_consumer: refused %s: %s", exc.reason, exc)
            raise _refused(exc) from exc
        logger.info(
            "x509_consumer: no trusted path for %s", leaf.subject.rfc4514_string()
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The client certificate is not issued by a trusted CA",
        )

    def _registered(self, identity: str, email: Optional[str]) -> Dict[str, Any]:
        """A new account for ``identity``, as REGISTRATION_MODE allows.
        ``email`` is one the anchor vouches for, or None."""
        # Read through the module: the settings snapshot is replaced when
        # the environment is reloaded, so an imported name goes stale.
        mode = Environment.settings.REGISTRATION_MODE
        invitations: List[Dict[str, Any]] = []
        if mode == "closed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No account is linked to this certificate",
            )
        if mode == "invite":
            pending_for = _invitation_hooks["pending_invitations_for_user"]
            if email and pending_for is not None:
                invitations = pending_for("", email, self.model_registry)
            if not invitations:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User registration requires an invitation",
                )
        created = UserModel.DB(self.model_registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(UserModel),
            email=email,
            display_name=identity,
        )
        apply_invitation = _invitation_hooks["apply_to_user"]
        for invitation in invitations:
            if apply_invitation is not None:
                apply_invitation(invitation, str(created.id), self.model_registry)
        logger.info("x509_consumer: created user %s for %s", created.id, identity)
        return self._user(str(created.id))

    def resolve_user(
        self, anchor: TrustAnchor, leaf: x509.Certificate
    ) -> Tuple[str, Dict[str, Any]]:
        """The identity ``leaf`` names under ``anchor`` and the active local
        user it signs in as, linked (or created) on first sign-in."""
        try:
            identity = identity_of(leaf, anchor.identity_attribute)
        except CertificateRefusedError as exc:
            raise _refused(exc) from exc
        link = self.linked(anchor.id, identity)
        if link is None:
            vouched = email_of(leaf) if anchor.trusted_for_email else None
            # Stored emails are normalized at registration; match the same.
            email = UserManager._normalize_identifier(vouched) if vouched else None
            user_id = UserManager.user_id_for_verified_email(email, self.model_registry)
            if user_id is None:
                user_id = str(self._registered(identity, email)["id"])
            link = self._link(anchor.id, identity, user_id)
        refuse_internal_account(link.user_id)
        user = self._user(str(link.user_id))
        if user.get("active") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="The account is disabled"
            )
        self.DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=link.id,
            new_properties={
                "subject_dn": leaf.subject.rfc4514_string(),
                "issuer_dn": leaf.issuer.rfc4514_string(),
                "serial_number": format(leaf.serial_number, "x"),
                "fingerprint_sha256": fingerprint_sha256(leaf),
                "not_after": leaf.not_valid_after_utc,
                "last_login_at": _now(),
            },
        )
        return identity, user

    @custom_route(
        method="POST",
        path="/login",
        input_model=X509LoginRequest,
        output_model=X509LoginResponse,
        authentication_type="none",
        openapi_tags=X509_TAGS,
        expose_in=(ExposeIn.REST,),
        summary="Sign in with the client certificate of this TLS connection",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def login_route(
        self, body: X509LoginRequest, request: Request, response: Response
    ) -> X509LoginResponse:
        chain = presented_chain(request)
        anchor, path = await self.verified(chain)
        identity, user = self.resolve_user(anchor, path[0])
        user_id = str(user["id"])
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return X509LoginResponse(
                identity=identity,
                trust_anchor_id=anchor.id,
                user_id=user_id,
                **challenge,
            )
        login = UserManager._complete_login(user, self.model_registry, response)
        return X509LoginResponse(
            identity=identity, trust_anchor_id=anchor.id, user_id=user_id, **login
        )
