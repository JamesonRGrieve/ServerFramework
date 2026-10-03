# SPDX-License-Identifier: AGPL-3.0-or-later
"""Passkeys and security keys for this server's own users: this server is
the WebAuthn Relying Party (see ``RelyingParty``).

A signed-in user registers a credential in two steps: POST
``/v1/webauthn/register/options`` returns the creation options for
``navigator.credentials.create()``, and POST ``/v1/webauthn/register/verify``
takes the browser's response and stores the credential. Signing in is the
same pair under ``/v1/webauthn/authenticate``, and needs no username: a
passkey names its user. A verified passkey sign-in, which always requires
user verification, gives the same session as a password login. Every
enabled credential is also a second factor (an MFA source, method type
``webauthn``): a password or other first-factor login of a user with one
yields an MFA challenge, which ``/v1/webauthn/mfa`` completes with the
credential instead of a code. Internal accounts (ROOT, SYSTEM, the
template user) hold no credentials and never sign in.

Every ceremony's challenge is generated here, stored, spent by its first
use, valid for the configured timeout, and bound: a registration to the
user and the session that began it, an MFA step to the challenge token it
answers, a sign-in begun with an email to that user.

Credentials belong to their user, who lists, renames and removes them at
``/v1/webauthn/credential``. They are created only by a verified
registration. An assertion whose counter fails to advance disables the
credential and stamps ``clone_detected_at``.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Dict, List, Literal, Optional

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    UserVerificationRequirement,
)

import jwt
from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.webauthn_consumer.RelyingParty import (
    CHALLENGE_BYTES,
    Assertion,
    CounterRegression,
    Descriptor,
    RelyingPartyPolicy,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.DateTimeUtils import ensure_utc
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.SingleUseToken import (
    read_single_use_token,
    redeem_single_use_token,
)
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    MFAMethodSource,
    OneTimeTokenMixin,
    UserManager,
    UserModel,
    refuse_internal_account,
    register_mfa_source,
)
from zephyrex.logic.BLL_Auth.user import MFA_CHALLENGE_AUDIENCE
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

DEVICE_NAME_MAX_LENGTH = 100
# How an MFA challenge names a credential among the user's second factors.
MFA_METHOD_TYPE = "webauthn"
# A sign-in begun for an unknown email is offered this many bytes of a
# stable, keyed stand-in credential id, so the options do not reveal
# whether the account exists or has credentials.
_STAND_IN_CREDENTIAL_BYTES = 16


class Ceremony:
    REGISTRATION = "registration"
    SIGN_IN = "sign_in"
    SECOND_FACTOR = "second_factor"


def _server_side(requester_id: str) -> bool:
    return is_root_id(requester_id) or is_system_id(requester_id)


def _each(
    kwargs: Dict[str, Any], prepare: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """``kwargs`` for a create, or each of a batch's ``entities``, prepared."""
    if isinstance(kwargs.get("entities"), list):
        return {**kwargs, "entities": [prepare(dict(e)) for e in kwargs["entities"]]}
    return prepare(dict(kwargs))


def user_handle(user_id: str) -> bytes:
    """The WebAuthn user handle of a user: their opaque id."""
    return user_id.encode("utf-8")


def _binding(secret: Optional[str]) -> Optional[str]:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class WebAuthnCredentialModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A passkey or security key registered to a user."""

    credential_id: str = Field(
        ...,
        description="Base64url credential ID",
        json_schema_extra={"unique": True},
    )
    public_key: str = Field(..., description="Base64url COSE public key")
    sign_count: int = Field(0, description="Last signature counter seen")
    aaguid: Optional[str] = Field(None, description="Authenticator model GUID")
    attestation_format: str = Field(..., description="Attestation statement format")
    attestation_trust: str = Field(..., description="none, self, unverified or trusted")
    device_name: Optional[str] = Field(None, description="The user's name for it")
    transports: Optional[str] = Field(
        None, description="Space-separated transports (usb, nfc, ble, internal, …)"
    )
    is_discoverable: bool = Field(
        False, description="A discoverable credential (passkey), per credProps"
    )
    backup_eligible: bool = Field(False, description="May be synced across devices")
    backed_up: bool = Field(False, description="Is synced, as last asserted")
    last_used_at: Optional[datetime] = Field(
        None, description="Last successful assertion"
    )
    clone_detected_at: Optional[datetime] = Field(
        None, description="When a non-advancing counter disabled it"
    )
    is_enabled: bool = Field(True, description="Usable for sign-in")

    table_comment: ClassVar[str] = "WebAuthn credentials registered to users"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        credential_id: str
        public_key: str
        sign_count: int = 0
        aaguid: Optional[str] = None
        attestation_format: str
        attestation_trust: str
        device_name: Optional[str] = Field(None, max_length=DEVICE_NAME_MAX_LENGTH)
        transports: Optional[str] = None
        is_discoverable: bool = False
        backup_eligible: bool = False
        backed_up: bool = False

    class Update(BaseModel):
        device_name: Optional[str] = Field(None, max_length=DEVICE_NAME_MAX_LENGTH)

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        credential_id: Optional[StringSearchModel] = None
        aaguid: Optional[StringSearchModel] = None
        device_name: Optional[StringSearchModel] = None
        is_discoverable: Optional[bool] = None
        is_enabled: Optional[bool] = None
        last_used_at: Optional[DateSearchModel] = None


class WebAuthnCeremonyModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """A challenge issued for one ceremony. Server-only: read and written as
    ROOT, never routed."""

    ceremony: str = Field(..., description="registration, sign_in or second_factor")
    challenge: str = Field(..., description="Base64url challenge")
    binding: Optional[str] = Field(
        None, description="SHA-256 of the session or MFA token it is bound to"
    )
    expires_at: datetime = Field(..., description="When it stops being redeemable")
    used_at: Optional[datetime] = Field(None, description="When it was spent")

    table_comment: ClassVar[str] = "WebAuthn ceremony challenges, single use"

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        ceremony: str
        challenge: str
        binding: Optional[str] = None
        expires_at: datetime

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        ceremony: Optional[StringSearchModel] = None


def usable_credentials(
    model_registry: Any, user_id: str
) -> List[WebAuthnCredentialModel]:
    """``user_id``'s enabled credentials: those an assertion may use."""
    CredentialDB = WebAuthnCredentialModel.DB(model_registry.DB.manager.Base)
    found: List[WebAuthnCredentialModel] = CredentialDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[
            CredentialDB.user_id == user_id,
            CredentialDB.is_enabled == True,  # noqa: E712
            CredentialDB.deleted_at.is_(None),
        ],
        return_type="dto",
        override_dto=WebAuthnCredentialModel,
    )
    return found


def _second_factor_methods(user_id: str, model_registry: Any) -> List[Dict[str, str]]:
    """A user's credentials as second factors: password login challenges a
    user who has one, and ``/v1/webauthn/mfa`` answers it. The source table
    is process-global, so this checks that the app bound the extension."""
    if not model_registry.is_model_bound(WebAuthnCredentialModel):
        return []
    return [
        {"id": str(credential.id), "method_type": MFA_METHOD_TYPE}
        for credential in usable_credentials(model_registry, user_id)
    ]


register_mfa_source(
    "webauthn_consumer", MFAMethodSource(login_methods=_second_factor_methods)
)


# ---------------------------------------------------------------------------
# Route models
# ---------------------------------------------------------------------------


class RegistrationStart(RouteModel):
    authenticator_attachment: Optional[Literal["platform", "cross-platform"]] = Field(
        None, description="Ask for a platform or a roaming authenticator"
    )


class CeremonyOptions(RouteModel):
    ceremony_id: str = Field(..., description="Name it when finishing the ceremony")
    public_key: Dict[str, Any] = Field(
        ..., description="The options for navigator.credentials, as JSON"
    )
    expires_at: datetime


class RegistrationFinish(RouteModel):
    ceremony_id: str
    credential: Dict[str, Any] = Field(
        ..., description="PublicKeyCredential.toJSON() from create()"
    )
    device_name: Optional[str] = Field(None, max_length=DEVICE_NAME_MAX_LENGTH)


class CredentialRegistered(RouteModel):
    credential: WebAuthnCredentialModel


class SignInStart(RouteModel):
    email: Optional[str] = Field(
        None,
        description="Email or username; omit for a passkey that names its user",
    )


class SignInFinish(RouteModel):
    ceremony_id: str
    credential: Dict[str, Any] = Field(
        ..., description="PublicKeyCredential.toJSON() from get()"
    )


class SecondFactorStart(RouteModel):
    challenge_token: str = Field(..., description="From the password login")


class SecondFactorFinish(SignInFinish):
    challenge_token: str


class LoginResponse(RouteModel):
    """The password login's response, unchanged."""

    user: Dict[str, Any]
    token: str
    preferences: Dict[str, str]
    teams: List[Dict[str, Any]]
    session_key: str


# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------


class WebAuthnCredentialManager(AbstractBLLManager, RouterMixin):
    """A user's own credentials: read, rename, remove. Created only by a
    verified registration (WebAuthnCeremonyManager)."""

    _model = WebAuthnCredentialModel

    prefix: ClassVar[Optional[str]] = "/v1/webauthn/credential"
    tags: ClassVar[Optional[List[str]]] = ["WebAuthn"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """The owner is the requester; ROOT and SYSTEM may name another, but
        not an internal account (403): it would sign in as it."""
        requester_id = self.requester.id

        def owned(fields: Dict[str, Any]) -> Dict[str, Any]:
            if not _server_side(requester_id) or not fields.get("user_id"):
                fields["user_id"] = requester_id
            refuse_internal_account(fields["user_id"])
            return fields

        return super().create(**_each(kwargs, owned))


class WebAuthnCeremonyManager(AbstractBLLManager, RouterMixin):
    """The registration, sign-in and second-factor ceremonies."""

    _model = WebAuthnCeremonyModel

    prefix: ClassVar[Optional[str]] = "/v1/webauthn"
    tags: ClassVar[Optional[List[str]]] = ["WebAuthn"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    # -- Storage ------------------------------------------------------------

    @property
    def _credentials(self) -> Any:
        return WebAuthnCredentialModel.DB(self.model_registry.DB.manager.Base)

    def _open(
        self,
        policy: RelyingPartyPolicy,
        ceremony: str,
        user_id: Optional[str],
        binding: Optional[str],
    ) -> WebAuthnCeremonyModel:
        """Store a fresh challenge for ``ceremony``, and drop expired ones."""
        CeremonyDB = self.DB
        now = _now()
        with self.model_registry.DB.manager._get_db_session() as session:
            session.query(CeremonyDB).filter(
                CeremonyDB.expires_at < now.replace(tzinfo=None)
            ).delete(synchronize_session=False)
        opened: WebAuthnCeremonyModel = CeremonyDB.create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=WebAuthnCeremonyModel,
            ceremony=ceremony,
            challenge=bytes_to_base64url(secrets.token_bytes(CHALLENGE_BYTES)),
            user_id=user_id,
            binding=binding,
            expires_at=now + timedelta(seconds=policy.timeout_seconds),
        )
        return opened

    def _spend(
        self, ceremony_id: str, ceremony: str, binding: Optional[str]
    ) -> WebAuthnCeremonyModel:
        """The ceremony's challenge, spent: only its first redemption, within
        its lifetime and from what it is bound to, gets it."""
        CeremonyDB = self.DB
        found: List[WebAuthnCeremonyModel] = CeremonyDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                CeremonyDB.id == ceremony_id,
                CeremonyDB.ceremony == ceremony,
                CeremonyDB.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=WebAuthnCeremonyModel,
        )
        unusable = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown, expired or already used WebAuthn ceremony",
        )
        if len(found) != 1:
            raise unusable
        opened = found[0]
        bound = opened.binding is None or (
            binding is not None and secrets.compare_digest(opened.binding, binding)
        )
        if (
            not bound
            or opened.used_at is not None
            or ensure_utc(opened.expires_at) <= _now()
        ):
            raise unusable
        with self.model_registry.DB.manager._get_db_session() as session:
            spent = (
                session.query(CeremonyDB)
                .filter(CeremonyDB.id == opened.id, CeremonyDB.used_at.is_(None))
                .update(
                    {CeremonyDB.used_at: _now().replace(tzinfo=None)},
                    synchronize_session=False,
                )
            )
        if spent != 1:
            raise unusable
        return opened

    def _usable_credentials(self, user_id: str) -> List[WebAuthnCredentialModel]:
        return usable_credentials(self.model_registry, user_id)

    def _credential(self, credential_id: str) -> Optional[WebAuthnCredentialModel]:
        found: List[WebAuthnCredentialModel] = self._credentials.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                self._credentials.credential_id == credential_id,
                self._credentials.deleted_at.is_(None),
            ],
            return_type="dto",
            override_dto=WebAuthnCredentialModel,
        )
        return found[0] if found else None

    def _active_user(self, user_id: str) -> Dict[str, Any]:
        """The user as login reads them; 401 when disabled or deleted."""
        users: List[Dict[str, Any]] = UserModel.DB(
            self.model_registry.DB.manager.Base
        ).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_id,
        )
        if len(users) != 1 or not users[0]["active"] or users[0]["deleted_at"]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )
        return users[0]

    def _user_by_identifier(self, identifier: str) -> Optional[Dict[str, Any]]:
        UserDB = UserModel.DB(self.model_registry.DB.manager.Base)
        normalized = UserManager._normalize_identifier(identifier)
        users: List[Dict[str, Any]] = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[
                (UserDB.email == normalized) | (UserDB.username == normalized),
                UserDB.deleted_at.is_(None),
            ],
        )
        return users[0] if len(users) == 1 and users[0]["active"] else None

    @staticmethod
    def _descriptors(credentials: List[WebAuthnCredentialModel]) -> List[Descriptor]:
        return [
            Descriptor(
                credential_id=base64url_to_bytes(c.credential_id),
                transports=tuple((c.transports or "").split()),
            )
            for c in credentials
        ]

    @staticmethod
    def _options(
        opened: WebAuthnCeremonyModel, public_key: Dict[str, Any]
    ) -> CeremonyOptions:
        return CeremonyOptions(
            ceremony_id=opened.id,
            public_key=public_key,
            expires_at=ensure_utc(opened.expires_at),
        )

    # -- Assertions ---------------------------------------------------------

    def _assert(
        self,
        policy: RelyingPartyPolicy,
        opened: WebAuthnCeremonyModel,
        credential: Dict[str, Any],
        *,
        require_user_verification: bool,
    ) -> WebAuthnCredentialModel:
        """The stored credential that verifiably made ``credential`` over the
        ceremony's challenge, with its use recorded; 401 otherwise. A
        credential that is not the ceremony's user's is refused, and so is
        a user handle that is not its owner's."""
        assertion: Assertion = policy.parse_assertion(credential)
        refused = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WebAuthn authentication refused: unknown credential",
        )
        stored = self._credential(assertion.credential_id)
        if stored is None or not stored.is_enabled:
            raise refused
        if opened.user_id is not None and stored.user_id != opened.user_id:
            raise refused
        if assertion.user_handle is not None and not secrets.compare_digest(
            assertion.user_handle, user_handle(stored.user_id)
        ):
            raise refused
        try:
            verified = policy.verify_assertion(
                assertion,
                challenge=base64url_to_bytes(opened.challenge),
                public_key=base64url_to_bytes(stored.public_key),
                stored_sign_count=stored.sign_count,
                require_user_verification=require_user_verification,
            )
        except CounterRegression as regression:
            self._flag_clone(stored, regression)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="WebAuthn authentication refused: the credential appears "
                "cloned and has been disabled",
            ) from regression
        self._credentials.update(
            requester_id=stored.user_id,
            model_registry=self.model_registry,
            id=stored.id,
            new_properties={
                "sign_count": verified.new_sign_count,
                "backed_up": verified.backed_up,
                "last_used_at": _now(),
            },
        )
        return stored

    def _flag_clone(
        self, stored: WebAuthnCredentialModel, regression: CounterRegression
    ) -> None:
        logger.warning(
            "webauthn_consumer: credential %s of user %s presented %s; disabled",
            stored.id,
            stored.user_id,
            regression,
        )
        self._credentials.update(
            requester_id=stored.user_id,
            model_registry=self.model_registry,
            id=stored.id,
            new_properties={"is_enabled": False, "clone_detected_at": _now()},
        )

    # -- Registration -------------------------------------------------------

    @staticmethod
    def _session_binding(request: Request) -> Optional[str]:
        """The signed-in session (its JWT's ``jti``) a registration is bound
        to; None for an API-key requester, which has no session."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        try:
            return _binding(str(UserManager._decode_jwt(token)["jti"]))
        except jwt.PyJWTError:
            return None

    @custom_route(
        method="POST",
        path="/register/options",
        input_model=RegistrationStart,
        output_model=CeremonyOptions,
        authentication_type="jwt",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Begin registering a passkey or security key",
    )
    def register_options(
        self, body: RegistrationStart, request: Request
    ) -> CeremonyOptions:
        policy = RelyingPartyPolicy.from_env()
        user_id = self.requester.id
        user = self._active_user(user_id)
        opened = self._open(
            policy, Ceremony.REGISTRATION, user_id, self._session_binding(request)
        )
        name = user.get("username") or user.get("email") or user_id
        return self._options(
            opened,
            policy.registration_options(
                challenge=base64url_to_bytes(opened.challenge),
                user_handle=user_handle(user_id),
                user_name=name,
                display_name=user.get("display_name") or name,
                exclude=self._descriptors(self._usable_credentials(user_id)),
                attachment=(
                    AuthenticatorAttachment(body.authenticator_attachment)
                    if body.authenticator_attachment
                    else None
                ),
            ),
        )

    @custom_route(
        method="POST",
        path="/register/verify",
        input_model=RegistrationFinish,
        output_model=CredentialRegistered,
        authentication_type="jwt",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Store a verified passkey or security key",
    )
    def register_verify(
        self, body: RegistrationFinish, request: Request
    ) -> CredentialRegistered:
        policy = RelyingPartyPolicy.from_env()
        user_id = self.requester.id
        opened = self._spend(
            body.ceremony_id, Ceremony.REGISTRATION, self._session_binding(request)
        )
        if opened.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unknown, expired or already used WebAuthn ceremony",
            )
        registered = policy.verify_registration(
            body.credential, base64url_to_bytes(opened.challenge)
        )
        CredentialDB = self._credentials
        taken = CredentialDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[CredentialDB.credential_id == registered.credential_id],
        )
        if taken:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This credential is already registered",
            )
        credential = WebAuthnCredentialManager(
            requester_id=user_id, model_registry=self.model_registry
        ).create(
            credential_id=registered.credential_id,
            public_key=registered.public_key,
            sign_count=registered.sign_count,
            aaguid=registered.aaguid,
            attestation_format=registered.attestation_format,
            attestation_trust=registered.attestation_trust,
            device_name=body.device_name,
            transports=registered.transports,
            is_discoverable=registered.is_discoverable,
            backup_eligible=registered.backup_eligible,
            backed_up=registered.backed_up,
        )
        return CredentialRegistered(credential=credential)

    # -- Sign-in ------------------------------------------------------------

    @custom_route(
        method="POST",
        path="/authenticate/options",
        input_model=SignInStart,
        output_model=CeremonyOptions,
        authentication_type="none",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Begin signing in with a passkey",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def authenticate_options(self, body: SignInStart) -> CeremonyOptions:
        """Options naming the account's credentials when an email is given,
        none (any passkey) otherwise. An unknown account, or one without
        credentials, is offered a keyed stand-in so the answer looks alike."""
        policy = RelyingPartyPolicy.from_env()
        user_id: Optional[str] = None
        allow: List[Descriptor] = []
        if body.email:
            user = self._user_by_identifier(body.email)
            if user is not None:
                user_id = str(user["id"])
                allow = self._descriptors(self._usable_credentials(user_id))
            if not allow:
                stand_in = OneTimeTokenMixin.fingerprint(
                    "webauthn_consumer:" + UserManager._normalize_identifier(body.email)
                )
                allow = [
                    Descriptor(
                        credential_id=bytes.fromhex(stand_in)[
                            :_STAND_IN_CREDENTIAL_BYTES
                        ]
                    )
                ]
        opened = self._open(policy, Ceremony.SIGN_IN, user_id, None)
        return self._options(
            opened,
            policy.authentication_options(
                challenge=base64url_to_bytes(opened.challenge),
                allow=allow,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )

    @custom_route(
        method="POST",
        path="/authenticate/verify",
        input_model=SignInFinish,
        output_model=LoginResponse,
        authentication_type="none",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Sign in with a verified passkey",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def authenticate_verify(
        self, body: SignInFinish, response: Response
    ) -> Dict[str, Any]:
        """The password login's response and session cookies. The passkey
        is both factors (possession, and the user verification it must
        perform), so no MFA challenge follows."""
        policy = RelyingPartyPolicy.from_env()
        opened = self._spend(body.ceremony_id, Ceremony.SIGN_IN, None)
        stored = self._assert(
            policy, opened, body.credential, require_user_verification=True
        )
        user = self._active_user(stored.user_id)
        return UserManager._complete_login(user, self.model_registry, response)

    # -- Second factor ------------------------------------------------------

    @staticmethod
    def _mfa_claims(challenge_token: str) -> Dict[str, Any]:
        try:
            return read_single_use_token(
                challenge_token, audience=MFA_CHALLENGE_AUDIENCE
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired MFA challenge",
            ) from None

    @custom_route(
        method="POST",
        path="/mfa/options",
        input_model=SecondFactorStart,
        output_model=CeremonyOptions,
        authentication_type="none",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Begin answering an MFA challenge with a security key",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def mfa_options(self, body: SecondFactorStart) -> CeremonyOptions:
        policy = RelyingPartyPolicy.from_env()
        claims = self._mfa_claims(body.challenge_token)
        user_id = str(claims["sub"])
        allow = self._descriptors(self._usable_credentials(user_id))
        if not allow:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No WebAuthn credential is registered for this account",
            )
        opened = self._open(
            policy, Ceremony.SECOND_FACTOR, user_id, _binding(str(claims["jti"]))
        )
        return self._options(
            opened,
            policy.authentication_options(
                challenge=base64url_to_bytes(opened.challenge),
                allow=allow,
                user_verification=policy.user_verification,
            ),
        )

    @custom_route(
        method="POST",
        path="/mfa/verify",
        input_model=SecondFactorFinish,
        output_model=LoginResponse,
        authentication_type="none",
        openapi_tags=("WebAuthn",),
        expose_in=(ExposeIn.REST,),
        summary="Complete an MFA login with a security key",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def mfa_verify(
        self, body: SecondFactorFinish, response: Response
    ) -> Dict[str, Any]:
        """The MFA challenge answered with the user's own credential: the
        password login's response. The challenge token is spent only by a
        verified assertion."""
        policy = RelyingPartyPolicy.from_env()
        claims = self._mfa_claims(body.challenge_token)
        opened = self._spend(
            body.ceremony_id, Ceremony.SECOND_FACTOR, _binding(str(claims["jti"]))
        )
        self._assert(
            policy,
            opened,
            body.credential,
            require_user_verification=policy.user_verification
            == UserVerificationRequirement.REQUIRED,
        )
        if not redeem_single_use_token(claims):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="MFA challenge already used",
            )
        user = self._active_user(str(claims["sub"]))
        return UserManager._complete_login(user, self.model_registry, response)
