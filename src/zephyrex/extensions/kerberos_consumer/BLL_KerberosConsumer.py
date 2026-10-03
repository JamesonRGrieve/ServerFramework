# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in with Kerberos: HTTP Negotiate (RFC 4559) over SPNEGO or bare
Kerberos GSSAPI tokens, for desktop single sign-on against Active Directory
or MIT realms.

``GET /v1/auth/kerberos/login/negotiate`` without credentials answers 401
``WWW-Authenticate: Negotiate``; the browser (or ``curl --negotiate``)
retries with ``Authorization: Negotiate <token>``. The token is accepted
by the first configured service keytab whose key opens it (see
``PRV_KerberosKeytab``); only instances an operator created (ROOT or
SYSTEM, owned by no user or team) are trusted, since whoever controls a
keytab controls whose tickets are believed. A success answers with the
mutual-authentication token in ``WWW-Authenticate`` and the same session
password login issues: the token in the body and the session cookies.

The client principal, realm-qualified, must be in the instance's allowed
realms. It maps to a local user through a :class:`KerberosPrincipalModel`
link, keyed by the principal. An unknown principal gets a new account
when ``REGISTRATION_MODE`` is ``open`` and is refused otherwise; a user's
links are made only by a proven sign-in or by ROOT/SYSTEM, never claimed
by a user, which would let them take over another's principal.
"""

import asyncio
import base64
import binascii
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.kerberos_consumer.PRV_KerberosKeytab import (
    AcceptedPrincipal,
    Acceptor,
    PRV_KerberosKeytab,
    principal_realm,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib import Environment
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserManager, UserModel, refuse_internal_account
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderManager
from zephyrex.pydantic2.fastapi import AuthType, RouteType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

NEGOTIATE = "Negotiate"
NEGOTIATE_CHALLENGE = {"WWW-Authenticate": NEGOTIATE}
KERBEROS_TAGS = ("Kerberos Sign-in",)


def _server_side(requester_id: str) -> bool:
    """ROOT and SYSTEM act on others' behalf; users act as themselves."""
    return is_root_id(requester_id) or is_system_id(requester_id)


def negotiate_token(authorization: Optional[str]) -> Optional[bytes]:
    """The GSSAPI token of an ``Authorization: Negotiate <base64>`` header;
    None when the header is absent or another scheme. A Negotiate header
    whose token is not base64 is the client's error (400)."""
    scheme, _, credentials = (authorization or "").strip().partition(" ")
    if scheme.lower() != NEGOTIATE.lower() or not credentials.strip():
        return None
    try:
        return base64.b64decode(credentials.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Negotiate token is not base64",
        ) from None


def unauthorized(detail: str) -> HTTPException:
    """401 with the Negotiate challenge, so the client can (re)try."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=NEGOTIATE_CHALLENGE,
    )


class KerberosPrincipalModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """A Kerberos principal that signs in as a local user."""

    principal: str = Field(
        ..., description="Realm-qualified Kerberos principal (alice@EXAMPLE.COM)"
    )
    realm: str = Field(..., description="The principal's realm")
    last_login_at: Optional[datetime] = Field(
        None, description="When the principal last signed in"
    )

    table_comment: ClassVar[str] = "Kerberos principals linked to local users"

    class Create(BaseModel, UserModel.Reference.ID):
        principal: str
        realm: Optional[str] = Field(
            None, description="Computed from the principal; any value sent is replaced"
        )

    class Update(BaseModel):
        last_login_at: Optional[datetime] = Field(
            None, description="Set by sign-in only"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        principal: Optional[StringSearchModel] = None
        realm: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class KerberosLoginResponse(RouteModel):
    """The password-login response (``user``, ``token``, ``preferences``,
    ``teams``, ``session_key``) for the signed-in principal or, for a user
    with a verified second factor, the MFA challenge to complete at POST
    /v1/user/authorize/mfa, exactly as a password login would."""

    principal: str
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


class KerberosPrincipalManager(AbstractBLLManager, RouterMixin):
    """A user's linked principals (read and unlink), and Negotiate sign-in."""

    _model = KerberosPrincipalModel
    prefix: ClassVar[Optional[str]] = "/v1/auth/kerberos"
    tags: ClassVar[Optional[List[str]]] = list(KERBEROS_TAGS)
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CREATE or UPDATE: links come from a proven sign-in, or from ROOT
    # and SYSTEM through ``create``; a user could otherwise claim a principal.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Link a principal to a user: ROOT and SYSTEM only. The link is
        written as its user, so they can see and remove it."""
        if not _server_side(self.requester.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Kerberos principals are linked by signing in with them",
            )
        if isinstance(kwargs.get("entities"), list):
            return [self.create(**dict(entity)) for entity in kwargs["entities"]]
        user_id = str(kwargs.get("user_id") or "")
        principal = str(kwargs.get("principal") or "").strip()
        if not user_id or not principal:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A link names its principal and its user",
            )
        refuse_internal_account(user_id)
        if self._user(user_id)["deleted_at"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such user"
            )
        try:
            realm = principal_realm(principal)
        except InvalidInputExternalError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if self.linked(principal) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That principal is already linked",
            )
        return self._link(principal, realm, user_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only sign-in stamps a link (its ``last_login_at``); a link never
        moves to another principal or user."""
        if not _server_side(self.requester.id) or set(kwargs) - {"last_login_at"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Kerberos principal links are not edited",
            )
        return super().update(id, **kwargs)

    def _link(self, principal: str, realm: str, user_id: str) -> Any:
        return self.DB.create(
            requester_id=user_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(KerberosPrincipalModel),
            principal=principal,
            realm=realm,
            user_id=user_id,
        )

    def linked(self, principal: str) -> Optional[Any]:
        """The live link for ``principal`` (exact, case-sensitive)."""
        links = self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=KerberosPrincipalModel,
            filters=[
                self.DB.principal == principal,
                self.DB.deleted_at.is_(None),
            ],
        )
        return links[0] if links else None

    def _user(self, user_id: str) -> Dict[str, Any]:
        users = UserModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_id,
        )
        if len(users) != 1:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such user"
            )
        user: Dict[str, Any] = users[0]
        return user

    def trusted_acceptors(self) -> List[Tuple[str, Acceptor]]:
        """The configured keytab instances an operator owns, in creation
        order. An instance a user or team created is never trusted: its
        keytab would let them vouch for any principal."""
        root_id = env("ROOT_ID")
        provider = ProviderManager(
            model_registry=self.model_registry, requester_id=root_id
        ).get(name=PRV_KerberosKeytab.name)
        # ROOT's reads include deleted rows; a deleted instance is gone.
        InstanceDB = ProviderInstanceModel.DB(self.model_registry.DB.manager.Base)
        instances: List[ProviderInstanceModel] = InstanceDB.list(
            requester_id=root_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=ProviderInstanceModel,
            filters=[
                InstanceDB.provider_id == provider.id,
                InstanceDB.deleted_at.is_(None),
            ],
        )
        acceptors: List[Tuple[str, Acceptor]] = []
        for instance in sorted(instances or [], key=lambda i: i.created_at):
            if not (
                instance.enabled is not False
                and instance.user_id is None
                and instance.team_id is None
                and _server_side(str(instance.created_by_user_id))
                and PRV_KerberosKeytab.is_configured_instance(instance)
            ):
                continue
            try:
                acceptors.append((instance.name, PRV_KerberosKeytab.acceptor(instance)))
            except PermanentExternalError as exc:
                logger.error("kerberos_consumer: instance %s: %s", instance.name, exc)
        return acceptors

    async def accept(self, token: bytes) -> Tuple[AcceptedPrincipal, Acceptor]:
        """The principal ``token`` proves, by the first trusted keytab that
        opens it. 503 when no keytab is configured; 401 when none accepts."""
        acceptors = self.trusted_acceptors()
        if not acceptors:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kerberos sign-in is not configured",
            )
        for instance_name, acceptor in acceptors:
            try:
                accepted = await asyncio.to_thread(acceptor.accept, token)
            except InvalidInputExternalError as exc:
                logger.info(
                    "kerberos_consumer: %s refused a token: %s", instance_name, exc
                )
                continue
            except PermanentExternalError as exc:
                logger.error("kerberos_consumer: %s: %s", instance_name, exc)
                continue
            return accepted, acceptor
        raise unauthorized("Kerberos authentication failed")

    def resolve_user(self, accepted: AcceptedPrincipal) -> Dict[str, Any]:
        """The local user ``accepted`` signs in as, created on first sign-in
        when registration is open."""
        link = self.linked(accepted.principal)
        if link is None:
            # Read at call time: the settings object is rebuilt when the
            # environment is reloaded.
            if Environment.settings.REGISTRATION_MODE != "open":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No account is linked to this Kerberos principal",
                )
            user = UserModel.DB(self.model_registry.DB.manager.Base).create(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                return_type="dto",
                override_dto=self.model_registry.apply(UserModel),
                display_name=accepted.principal,
            )
            link = self._link(accepted.principal, accepted.realm, str(user.id))
            logger.info(
                "kerberos_consumer: created user %s for %s", user.id, accepted.principal
            )
        refuse_internal_account(link.user_id)
        found = self._user(str(link.user_id))
        if not found["active"] or found["deleted_at"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="The account is disabled"
            )
        self.DB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=link.id,
            new_properties={"last_login_at": datetime.now(timezone.utc)},
        )
        return found

    @custom_route(
        method="GET",
        path="/login/negotiate",
        output_model=KerberosLoginResponse,
        authentication_type="none",
        openapi_tags=KERBEROS_TAGS,
        expose_in=(ExposeIn.REST,),
        summary="Sign in with Kerberos (HTTP Negotiate, RFC 4559)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def negotiate_route(
        self, request: Request, response: Response
    ) -> KerberosLoginResponse:
        token = negotiate_token(request.headers.get("authorization"))
        if token is None:
            raise unauthorized("Kerberos sign-in: send Authorization: Negotiate")
        accepted, acceptor = await self.accept(token)
        if accepted.realm not in acceptor.allowed_realms:
            logger.info(
                "kerberos_consumer: realm %s is not allowed (%s)",
                accepted.realm,
                accepted.principal,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Sign-in from this Kerberos realm is not allowed",
            )
        user = self.resolve_user(accepted)
        if accepted.reply_token:
            response.headers["WWW-Authenticate"] = (
                f"{NEGOTIATE} {base64.b64encode(accepted.reply_token).decode()}"
            )
        user_id = str(user["id"])
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return KerberosLoginResponse(
                principal=accepted.principal, user_id=user_id, **challenge
            )
        login = UserManager._complete_login(user, self.model_registry, response)
        return KerberosLoginResponse(
            principal=accepted.principal, user_id=user_id, **login
        )
