# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in through a forward-auth service (Authelia, oauth2-proxy,
Authentik): the browser's own session there vouches for it here.

``GET /v1/auth/forward-auth/login`` asks a ``forward_auth_verifier``
instance (see ``PRV_ForwardAuthVerifier``) who the browser is, sending it
only the cookies and headers the instance names. Only instances an
operator created (ROOT or SYSTEM, owned by no user or team) are used,
since whoever controls the verifier decides whose identity is believed.
With several, ``?instance=<name>`` picks one; each verifier only ever sees
its own credentials. The identity comes from the verifier's answer, never
from the browser's request: a client sending ``Remote-User`` itself is
ignored.

An identity maps to a local user through a :class:`ForwardAuthIdentityModel`
link keyed by (instance, identity): the verifier is the authority that
vouched for the name, so the same name from another verifier is another
person. With no link, an instance ``trusted_for_email`` may link the
identity to the account with the email the verifier sent; otherwise a new
account is made as ``REGISTRATION_MODE`` allows (``invite`` needs a
pending invitation for an email the verifier vouches for). Links are made
only by a verified sign-in or by ROOT/SYSTEM, never claimed by a user. A
success issues the session password login issues (or its MFA challenge).
"""

from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    PermanentExternalError,
)
from zephyrex.extensions.forward_auth_consumer.PRV_ForwardAuthVerifier import (
    OriginalRequest,
    PRV_ForwardAuthVerifier,
    VerifiedIdentity,
    Verifier,
)
from zephyrex.lib import Environment
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
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
from zephyrex.logic.AbstractLogicManager.ownership import server_side
from zephyrex.logic.BLL_Auth import (
    UserManager,
    UserModel,
    _invitation_hooks,
    refuse_internal_account,
)
from zephyrex.logic.BLL_Providers import ProviderInstanceModel, ProviderManager
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin, RouteType
from zephyrex.pydantic2.registry import BaseModel

ROUTE_PREFIX = "/v1/auth/forward-auth"
FORWARD_AUTH_TAGS = ("Forward-auth Sign-in",)
INSTANCE_QUERY_PARAMETER = "instance"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _not_signed_in() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not signed in at the forward-auth service",
    )


class ForwardAuthIdentityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    """An identity a forward-auth verifier vouches for, signing in as a user."""

    provider_instance_id: str = Field(
        ..., description="The verifier instance that vouches for it"
    )
    identity: str = Field(
        ..., description="The user the verifier names, as its user header gives it"
    )
    last_login_at: Optional[datetime] = Field(
        None, description="When the identity last signed in"
    )

    table_comment: ClassVar[str] = "Forward-auth identities linked to local users"

    class Create(BaseModel, UserModel.Reference.ID):
        provider_instance_id: str
        identity: str

    class Update(BaseModel):
        last_login_at: Optional[datetime] = Field(
            None, description="Set by sign-in only"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
    ):
        provider_instance_id: Optional[StringSearchModel] = None
        identity: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class ForwardAuthLoginResponse(RouteModel):
    """The password-login response (``user``, ``token``, ``preferences``,
    ``teams``, ``session_key``) for the verified identity's user or, for a
    user with a verified second factor, the MFA challenge to complete at
    POST /v1/user/authorize/mfa, exactly as a password login would."""

    identity: str
    provider_instance_id: str
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


class ForwardAuthIdentityManager(AbstractBLLManager, RouterMixin):
    """A user's forward-auth identities (read and unlink), and the sign-in."""

    _model = ForwardAuthIdentityModel
    prefix: ClassVar[Optional[str]] = ROUTE_PREFIX
    tags: ClassVar[Optional[List[str]]] = list(FORWARD_AUTH_TAGS)
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # No CREATE or UPDATE: links come from a verified sign-in, or from ROOT
    # and SYSTEM through ``create``; a user could otherwise claim another's
    # identity.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create(self, **kwargs: Any) -> Any:
        """Link an identity to a user: ROOT and SYSTEM only. The link is
        written as its user, so they can see and remove it."""
        if not server_side(str(self.requester.id)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forward-auth identities are linked by signing in with them",
            )
        if isinstance(kwargs.get("entities"), list):
            return [self.create(**dict(entity)) for entity in kwargs["entities"]]
        user_id = str(kwargs.get("user_id") or "")
        instance_id = str(kwargs.get("provider_instance_id") or "")
        identity = str(kwargs.get("identity") or "").strip()
        if not user_id or not instance_id or not identity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A link names its user, verifier instance and identity",
            )
        refuse_internal_account(user_id)
        self._user(user_id)
        if not any(str(row.id) == instance_id for row in self._verifier_rows()):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No such forward-auth verifier",
            )
        if self.linked(instance_id, identity) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That identity is already linked",
            )
        return self._link(instance_id, identity, user_id)

    def update(self, id: str, **kwargs: Any) -> Any:
        """Only sign-in stamps a link (its ``last_login_at``); a link never
        moves to another identity or user."""
        if not server_side(self.requester.id) or set(kwargs) - {"last_login_at"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forward-auth identity links are not edited",
            )
        return super().update(id, **kwargs)

    def _link(self, instance_id: str, identity: str, user_id: str) -> Any:
        return self.DB.create(
            requester_id=user_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(ForwardAuthIdentityModel),
            provider_instance_id=instance_id,
            identity=identity,
            user_id=user_id,
        )

    def linked(self, instance_id: str, identity: str) -> Optional[Any]:
        """The live link for ``identity`` under the instance (exact match)."""
        links = self.DB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=ForwardAuthIdentityModel,
            filters=[
                self.DB.provider_instance_id == instance_id,
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

    # -- the verifiers ------------------------------------------------------

    def _verifier_rows(self) -> List[ProviderInstanceModel]:
        """The live verifier instances an operator owns, oldest first. An
        instance a user or team created is never trusted: its verifier
        would let them vouch for anyone."""
        root_id = env("ROOT_ID")
        provider = ProviderManager(
            model_registry=self.model_registry, requester_id=root_id
        ).get(name=PRV_ForwardAuthVerifier.name)
        # ROOT's reads include deleted rows; a deleted instance is gone.
        InstanceDB = ProviderInstanceModel.DB(self.model_registry.DB.manager.Base)
        rows: List[ProviderInstanceModel] = InstanceDB.list(
            requester_id=root_id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=ProviderInstanceModel,
            filters=[
                InstanceDB.provider_id == provider.id,
                InstanceDB.deleted_at.is_(None),
            ],
        )
        return sorted(
            (
                row
                for row in rows or []
                if row.user_id is None
                and row.team_id is None
                and server_side(str(row.created_by_user_id))
            ),
            key=lambda row: row.created_at or _now(),
        )

    def trusted_verifier(self, name: Optional[str]) -> Tuple[str, Verifier]:
        """The enabled, configured operator verifier named ``name``, or the
        only one when no name is given: its instance id and its verifier.
        503 when none is usable; 400 when a choice is needed; 404 for a
        name that is not one."""
        usable = [
            row
            for row in self._verifier_rows()
            if row.enabled is not False
            and PRV_ForwardAuthVerifier.is_configured_instance(row)
        ]
        if not usable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Forward-auth sign-in is not configured",
            )
        if name:
            usable = [row for row in usable if row.name == name]
            if not usable:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No such forward-auth verifier",
                )
        elif len(usable) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Name the verifier with ?{INSTANCE_QUERY_PARAMETER}=",
            )
        row = usable[0]
        try:
            return str(row.id), PRV_ForwardAuthVerifier.verifier(row)
        except PermanentExternalError as exc:
            logger.error("forward_auth_consumer: instance %s: %s", row.name, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Forward-auth sign-in is misconfigured",
            ) from None

    async def verified(
        self, verifier: Verifier, original: OriginalRequest
    ) -> VerifiedIdentity:
        """Who ``verifier`` says sent ``original``: 401 when it denies, or
        when the browser sent none of the credentials it reads; 502 when it
        fails (fail closed)."""
        if not verifier.carries_credentials(original):
            raise _not_signed_in()
        try:
            return await PRV_ForwardAuthVerifier.verify(verifier, original)
        except AuthExternalError as exc:
            logger.info(
                "forward_auth_consumer: %s denied a sign-in: %s",
                verifier.instance_name,
                exc,
            )
            raise _not_signed_in() from None
        except BaseExternalError as exc:
            logger.error(
                "forward_auth_consumer: %s failed: %s", verifier.instance_name, exc
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The forward-auth service could not verify the sign-in",
            ) from None

    # -- the account ----------------------------------------------------------

    def _registered(
        self, verified: VerifiedIdentity, email: Optional[str]
    ) -> Dict[str, Any]:
        """A new account for ``verified``, as REGISTRATION_MODE allows.
        ``email`` is one the verifier vouches for, or None."""
        # Read through the module: the settings snapshot is replaced when
        # the environment is reloaded, so an imported name goes stale.
        mode = Environment.settings.REGISTRATION_MODE
        invitations: List[Dict[str, Any]] = []
        if mode == "closed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No account is linked to this identity",
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
            display_name=verified.display_name or verified.identity,
        )
        apply_invitation = _invitation_hooks["apply_to_user"]
        for invitation in invitations:
            if apply_invitation is not None:
                apply_invitation(invitation, str(created.id), self.model_registry)
        logger.info(
            "forward_auth_consumer: created user %s for %s",
            created.id,
            verified.identity,
        )
        return self._user(str(created.id))

    def resolve_user(
        self, instance_id: str, verifier: Verifier, verified: VerifiedIdentity
    ) -> Dict[str, Any]:
        """The active local user ``verified`` signs in as, linked (or
        created) on first sign-in."""
        link = self.linked(instance_id, verified.identity)
        if link is None:
            # Stored emails are normalized at registration; match the same.
            email = (
                UserManager._normalize_identifier(verified.email)
                if verifier.trusted_for_email and verified.email
                else None
            )
            user_id = UserManager.user_id_for_verified_email(email, self.model_registry)
            if user_id is None:
                user_id = str(self._registered(verified, email)["id"])
            link = self._link(instance_id, verified.identity, user_id)
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
            new_properties={"last_login_at": _now()},
        )
        return user

    @custom_route(
        method="GET",
        path="/login",
        output_model=ForwardAuthLoginResponse,
        authentication_type="none",
        openapi_tags=FORWARD_AUTH_TAGS,
        expose_in=(ExposeIn.REST,),
        summary="Sign in with the browser's session at the forward-auth service",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def login_route(
        self, request: Request, response: Response
    ) -> ForwardAuthLoginResponse:
        instance_id, verifier = self.trusted_verifier(
            request.query_params.get(INSTANCE_QUERY_PARAMETER)
        )
        original = OriginalRequest(
            cookie_header=request.headers.get("cookie"),
            headers=dict(request.headers),
            client_host=request.client.host if request.client else None,
            url=str(request.url),
            method=request.method,
        )
        verified = await self.verified(verifier, original)
        user = self.resolve_user(instance_id, verifier, verified)
        user_id = str(user["id"])
        challenge = UserManager.mfa_challenge(user_id, self.model_registry)
        if challenge is not None:
            return ForwardAuthLoginResponse(
                identity=verified.identity,
                provider_instance_id=instance_id,
                user_id=user_id,
                **challenge,
            )
        login = UserManager._complete_login(user, self.model_registry, response)
        return ForwardAuthLoginResponse(
            identity=verified.identity,
            provider_instance_id=instance_id,
            user_id=user_id,
            **login,
        )
