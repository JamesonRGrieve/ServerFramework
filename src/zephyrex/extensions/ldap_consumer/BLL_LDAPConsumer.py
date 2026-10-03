# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in to this server with directory credentials (OpenLDAP, Active
Directory, FreeIPA, 389 DS).

**Directories** (``/v1/ldap/directory``) are the server's own
configuration: only ROOT or SYSTEM (the root API key) creates, reads,
changes or deletes one. The service account's DN and password are
write-only: stored encrypted, and never returned. A directory is reached
over LDAPS or StartTLS with its certificate verified (see ``LDAPClient``).

**Signing in** (``POST /v1/auth/ldap/login``, no credentials needed): the
person's username is looked up as the service account, then their password
is proven by binding as their entry. They are known by the pair
(directory, the entry's ``entryUUID``/``objectGUID``) recorded as an
**identity** (``/v1/ldap/identity``, ROOT only), never by DN. Their first
sign-in links them:

- to the local account an administrator linked them to (an identity
  created through the API, for accounts that already exist);
- else to the local account with their directory email, when the
  directory is trusted to vouch for email (``link_existing_by_email``);
  otherwise such a sign-in is refused, since the directory's email claim
  would otherwise take over any local account;
- else to a new account, when ``REGISTRATION_MODE`` is ``open`` (as for
  self-registration; ``invite`` and ``closed`` refuse it).

A signed-in person gets exactly what a password login gives (the same
session, token, cookies and teams, through ``UserManager``), or the same
MFA challenge when they have a second factor. Failures count against the
same lockouts as password login: the per-IP tracker, and the per-user
threshold of the ``auth_lockout`` extension when it is loaded. The
directory's groups are recorded on the identity at each sign-in.
"""

import asyncio
import secrets
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel as RouteModel
from pydantic import Field, ValidationError

from zephyrex.database.StaticPermissions import is_root_id, is_system_id
from zephyrex.extensions.ExternalErrors import BaseExternalError
from zephyrex.extensions.ldap_consumer.LDAPClient import (
    CredentialsRefused,
    DirectoryAccount,
    DirectorySettings,
    GroupSource,
    LDAPDirectoryClient,
    Security,
    configuration_problems,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    rate_limit,
    resolve_client_ip,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NameMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    UserCredentialManager,
    UserManager,
    UserModel,
    _lockout_hooks,
)
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.fastapi.types import RouteType
from zephyrex.pydantic2.registry import BaseModel

# The per-IP tracker's flow name for directory sign-ins.
LOCKOUT_FLOW = "ldap_login"
DEFAULT_USER_OBJECT_FILTER = "(objectClass=person)"
DEFAULT_GROUP_OBJECT_FILTER = (
    "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)"
    "(objectClass=group))"
)
DEFAULT_TIMEOUT_SECONDS = 10
SECRET_FIELDS = ("bind_dn", "bind_password")
# A directory's settings an update may change but never empty.
REQUIRED_FIELDS = (
    "name",
    "host",
    "security",
    "bind_dn",
    "bind_password",
    "base_dn",
    "username_attribute",
    "id_attribute",
    "group_source",
    "link_existing_by_email",
    "timeout_seconds",
    "enabled",
)
UNUSABLE_PASSWORD_BYTES = 32
INVALID_CREDENTIALS = "Invalid credentials"
DIRECTORY_UNAVAILABLE = "The directory is unavailable"
ADMIN_ROUTES = [
    RouteType.GET,
    RouteType.LIST,
    RouteType.SEARCH,
    RouteType.CREATE,
    RouteType.UPDATE,
    RouteType.DELETE,
]


def _server_side(requester_id: Optional[str]) -> bool:
    if not requester_id:
        return False
    return is_root_id(requester_id) or is_system_id(requester_id)


def _require_server_side(manager: AbstractBLLManager) -> None:
    """Directories and identities are the server's own configuration: an
    API key issued to a user is not enough to touch them."""
    requester = manager.optional_requester
    if not _server_side(requester.id if requester is not None else None):
        raise HTTPException(
            status_code=403, detail="Only the server's administrator manages this"
        )


class LdapDirectoryModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    """A directory people sign in with."""

    host: str = Field(..., description="Directory host name or address")
    port: Optional[int] = Field(
        None, description="Port; 636 for ldaps and 389 otherwise when unset"
    )
    security: Security = Field(
        "ldaps",
        description=(
            "ldaps, or starttls; plain only for a loopback directory with "
            "LDAP_CONSUMER_ALLOW_PLAINTEXT_LOOPBACK=true"
        ),
    )
    ca_certificate: Optional[str] = Field(
        None,
        description="PEM CA certificate(s) to trust instead of the system store",
    )
    bind_dn: Optional[str] = Field(
        None, exclude=True, description="Service account DN (write-only)"
    )
    bind_password: Optional[str] = Field(
        None, exclude=True, description="Service account password (write-only)"
    )
    base_dn: str = Field(..., description="Where people are searched for")
    user_object_filter: Optional[str] = Field(
        DEFAULT_USER_OBJECT_FILTER, description="Filter every person matches"
    )
    username_attribute: str = Field(
        "uid", description="Attribute holding the username (sAMAccountName on AD)"
    )
    id_attribute: str = Field(
        "entryUUID", description="Attribute holding the stable id (objectGUID on AD)"
    )
    email_attribute: Optional[str] = Field("mail", description="Email attribute")
    display_name_attribute: Optional[str] = Field(
        "displayName", description="Display name attribute"
    )
    group_source: GroupSource = Field(
        "none", description="none, member_of (the memberOf attribute) or search"
    )
    group_search_base: Optional[str] = Field(
        None, description="Where groups are searched for (group_source=search)"
    )
    group_object_filter: Optional[str] = Field(
        DEFAULT_GROUP_OBJECT_FILTER, description="Filter every group matches"
    )
    group_member_attribute: Optional[str] = Field(
        "member", description="Group attribute listing member DNs"
    )
    link_existing_by_email: bool = Field(
        False,
        description=(
            "Trust the directory's email: a first sign-in links to the local "
            "account with that email"
        ),
    )
    timeout_seconds: int = Field(
        DEFAULT_TIMEOUT_SECONDS, description="Connect and operation timeout"
    )
    enabled: bool = Field(True, description="Whether people may sign in with it")

    table_comment: ClassVar[str] = "Directories (LDAP, AD) people sign in with"
    is_system_entity: ClassVar[bool] = True

    class Create(BaseModel, NameMixinModel):
        host: str
        port: Optional[int] = None
        security: Security = "ldaps"
        ca_certificate: Optional[str] = None
        bind_dn: str
        bind_password: str
        base_dn: str
        user_object_filter: Optional[str] = DEFAULT_USER_OBJECT_FILTER
        username_attribute: str = "uid"
        id_attribute: str = "entryUUID"
        email_attribute: Optional[str] = "mail"
        display_name_attribute: Optional[str] = "displayName"
        group_source: GroupSource = "none"
        group_search_base: Optional[str] = None
        group_object_filter: Optional[str] = DEFAULT_GROUP_OBJECT_FILTER
        group_member_attribute: Optional[str] = "member"
        link_existing_by_email: bool = False
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
        enabled: bool = True

    class Update(BaseModel, NameMixinModel.Optional):
        host: Optional[str] = None
        port: Optional[int] = None
        security: Optional[Security] = None
        ca_certificate: Optional[str] = None
        bind_dn: Optional[str] = None
        bind_password: Optional[str] = None
        base_dn: Optional[str] = None
        user_object_filter: Optional[str] = None
        username_attribute: Optional[str] = None
        id_attribute: Optional[str] = None
        email_attribute: Optional[str] = None
        display_name_attribute: Optional[str] = None
        group_source: Optional[GroupSource] = None
        group_search_base: Optional[str] = None
        group_object_filter: Optional[str] = None
        group_member_attribute: Optional[str] = None
        link_existing_by_email: Optional[bool] = None
        timeout_seconds: Optional[int] = None
        enabled: Optional[bool] = None

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, NameMixinModel.Search
    ):
        host: Optional[StringSearchModel] = None
        base_dn: Optional[StringSearchModel] = None
        enabled: Optional[bool] = None


class LdapIdentityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    LdapDirectoryModel.Reference,
    metaclass=ModelMeta,
):
    """A local user's identity in a directory: who (directory, external id)
    signs in as them."""

    external_id: str = Field(
        ..., description="The entry's stable id (entryUUID, objectGUID)"
    )
    dn: Optional[str] = Field(None, description="The entry's DN at last sign-in")
    username: Optional[str] = Field(None, description="Directory username")
    email: Optional[str] = Field(None, description="Directory email")
    groups: Optional[List[str]] = Field(None, description="Group DNs at last sign-in")
    last_login_at: Optional[datetime] = Field(
        None, description="Last successful directory sign-in"
    )

    table_comment: ClassVar[str] = "Local users' identities in sign-in directories"

    class Create(BaseModel, UserModel.Reference.ID, LdapDirectoryModel.Reference.ID):
        external_id: str
        dn: Optional[str] = None
        username: Optional[str] = None
        email: Optional[str] = None

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        LdapDirectoryModel.Reference.ID.Search,
    ):
        external_id: Optional[StringSearchModel] = None
        username: Optional[StringSearchModel] = None
        email: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


def _sealed(fields: Dict[str, Any]) -> Dict[str, Any]:
    """``fields`` with the service account's DN and password encrypted."""
    sealed = dict(fields)
    for key in SECRET_FIELDS:
        if sealed.get(key):
            sealed[key] = encrypt_secret(str(sealed[key]))
    return sealed


def _invalid(problems: List[str]) -> HTTPException:
    return HTTPException(status_code=422, detail="; ".join(problems))


def directory_settings(directory: LdapDirectoryModel) -> DirectorySettings:
    """How to reach ``directory``, its secrets decrypted."""
    return DirectorySettings(
        host=directory.host,
        port=directory.port,
        security=directory.security,
        ca_certificate=directory.ca_certificate,
        bind_dn=decrypt_secret(directory.bind_dn) or "",
        bind_password=decrypt_secret(directory.bind_password) or "",
        base_dn=directory.base_dn,
        user_object_filter=directory.user_object_filter or "",
        username_attribute=directory.username_attribute,
        id_attribute=directory.id_attribute,
        email_attribute=directory.email_attribute,
        display_name_attribute=directory.display_name_attribute,
        group_source=directory.group_source,
        group_search_base=directory.group_search_base,
        group_object_filter=directory.group_object_filter or "",
        group_member_attribute=directory.group_member_attribute or "member",
        timeout_seconds=directory.timeout_seconds,
    )


def load_directory(model_registry: Any, directory_id: str) -> LdapDirectoryModel:
    """The directory (with its sealed secrets), read as ROOT. 404 when there
    is none, or it was deleted (ROOT reads deleted rows too)."""
    DirectoryDB = LdapDirectoryModel.DB(model_registry.DB.manager.Base)
    found: List[LdapDirectoryModel] = DirectoryDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[DirectoryDB.id == directory_id, DirectoryDB.deleted_at.is_(None)],
        return_type="dto",
        override_dto=LdapDirectoryModel,
    )
    if not found:
        raise HTTPException(status_code=404, detail="No such directory")
    return found[0]


def client_for(model_registry: Any, directory_id: str) -> LDAPDirectoryClient:
    return LDAPDirectoryClient(
        directory_settings(load_directory(model_registry, directory_id))
    )


class DirectoryAccountView(RouteModel):
    dn: str
    external_id: str
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    groups: List[str] = Field(default_factory=list)

    @classmethod
    def of(cls, account: DirectoryAccount) -> "DirectoryAccountView":
        return cls(
            dn=account.dn,
            external_id=account.external_id,
            username=account.username,
            email=account.email,
            display_name=account.display_name,
            groups=list(account.groups),
        )


class DirectoryLookupRequest(RouteModel):
    username: str = Field(..., min_length=1, description="Directory username")


class DirectoryCheckResponse(RouteModel):
    reachable: bool
    security: str


async def _directory_call(call: Any, *args: Any) -> Any:
    """Run a blocking directory call off the event loop, its failures as
    HTTP errors an administrator can act on."""
    try:
        return await asyncio.to_thread(call, *args)
    except CredentialsRefused:
        raise HTTPException(status_code=404, detail="No such directory account")
    except BaseExternalError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class LdapDirectoryManager(AbstractBLLManager, RouterMixin):
    _model = LdapDirectoryModel

    prefix: ClassVar[Optional[str]] = "/v1/ldap/directory"
    tags: ClassVar[Optional[List[str]]] = ["LDAP Directories"]
    auth_type: ClassVar[AuthType] = AuthType.API_KEY
    routes_to_register: ClassVar[Optional[List[RouteType]]] = ADMIN_ROUTES

    def create(self, **kwargs: Any) -> Any:
        _require_server_side(self)

        def prepare(fields: Dict[str, Any]) -> Dict[str, Any]:
            try:
                complete = LdapDirectoryModel.Create(**fields).model_dump()
            except ValidationError:
                return _sealed(fields)
            problems = configuration_problems(complete)
            if problems:
                raise _invalid(problems)
            return _sealed(fields)

        if isinstance(kwargs.get("entities"), list):
            kwargs = {
                **kwargs,
                "entities": [prepare(dict(e)) for e in kwargs["entities"]],
            }
        else:
            kwargs = prepare(kwargs)
        return super().create(**kwargs)

    def update(self, id: str, **kwargs: Any) -> Any:
        _require_server_side(self)
        current = load_directory(self.model_registry, id)
        problems = [
            f"{key} cannot be cleared"
            for key in REQUIRED_FIELDS
            if key in kwargs and kwargs[key] is None
        ] + configuration_problems({**current.model_dump(), **kwargs})
        if problems:
            raise _invalid(problems)
        return super().update(id, **_sealed(kwargs))

    def delete(self, id: str) -> None:
        _require_server_side(self)
        super().delete(id)

    @custom_route(
        method="GET",
        path="/{directory_id}/check",
        output_model=DirectoryCheckResponse,
        authentication_type="api_key",
        openapi_tags=("LDAP Directories",),
        summary="Reach the directory securely and bind as its service account",
        expose_in=(ExposeIn.REST,),
    )
    async def check_route(self, directory_id: str) -> DirectoryCheckResponse:
        _require_server_side(self)
        client = client_for(self.model_registry, directory_id)
        await _directory_call(client.check)
        return DirectoryCheckResponse(reachable=True, security=client.settings.security)

    @custom_route(
        method="POST",
        path="/{directory_id}/lookup",
        input_model=DirectoryLookupRequest,
        output_model=DirectoryAccountView,
        authentication_type="api_key",
        openapi_tags=("LDAP Directories",),
        summary="Find a person in the directory (to link an existing account)",
        expose_in=(ExposeIn.REST,),
    )
    async def lookup_route(
        self, directory_id: str, body: DirectoryLookupRequest
    ) -> DirectoryAccountView:
        _require_server_side(self)
        client = client_for(self.model_registry, directory_id)
        account: DirectoryAccount = await _directory_call(client.find, body.username)
        return DirectoryAccountView.of(account)


def _identity_db(model_registry: Any) -> Any:
    return LdapIdentityModel.DB(model_registry.DB.manager.Base)


def find_identity(
    model_registry: Any, directory_id: str, external_id: str
) -> Optional[LdapIdentityModel]:
    IdentityDB = _identity_db(model_registry)
    rows: List[LdapIdentityModel] = IdentityDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[
            IdentityDB.ldap_directory_id == directory_id,
            IdentityDB.external_id == external_id,
            IdentityDB.deleted_at.is_(None),
        ],
        return_type="dto",
        override_dto=LdapIdentityModel,
    )
    return rows[0] if rows else None


def _users(model_registry: Any, **match: Any) -> List[Dict[str, Any]]:
    """Users (not deleted) matching ``match``, read as ROOT, as rows the
    way password login reads them."""
    UserDB = UserModel.DB(model_registry.DB.manager.Base)
    found: List[Dict[str, Any]] = UserDB.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[UserDB.deleted_at.is_(None)],
        **match,
    )
    return found


def _active(user: Dict[str, Any]) -> bool:
    return bool(user.get("active"))


class LdapIdentityManager(AbstractBLLManager, RouterMixin):
    _model = LdapIdentityModel

    prefix: ClassVar[Optional[str]] = "/v1/ldap/identity"
    tags: ClassVar[Optional[List[str]]] = ["LDAP Directories"]
    auth_type: ClassVar[AuthType] = AuthType.API_KEY
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.CREATE,
        RouteType.DELETE,
    ]

    def create_validation(self, entity: Any) -> None:
        load_directory(self.model_registry, entity.ldap_directory_id)
        if not _users(self.model_registry, id=entity.user_id):
            raise HTTPException(status_code=404, detail="No such user")
        if find_identity(
            self.model_registry, entity.ldap_directory_id, entity.external_id
        ):
            raise HTTPException(
                status_code=409,
                detail="That directory account is already linked to a user",
            )

    def create(self, **kwargs: Any) -> Any:
        """Link a directory account to an existing local user."""
        _require_server_side(self)
        return super().create(**kwargs)

    def delete(self, id: str) -> None:
        _require_server_side(self)
        super().delete(id)


class LdapDirectoryEntry(RouteModel):
    id: str
    name: str


class LdapDirectoryList(RouteModel):
    directories: List[LdapDirectoryEntry]


class LdapLoginRequest(RouteModel):
    directory_id: str = Field(..., description="The directory to sign in with")
    username: str = Field(..., description="Directory username")
    password: str = Field(..., description="Directory password")


class LdapLoginResponse(RouteModel):
    """What a password login answers: the session (``user``, ``token``,
    ``teams``, ``preferences``, ``session_key``), or, for a user with a
    second factor, the challenge to complete at POST
    /v1/user/authorize/mfa."""

    user: Optional[Dict[str, Any]] = None
    token: Optional[str] = None
    preferences: Dict[str, str] = Field(default_factory=dict)
    teams: List[Dict[str, Any]] = Field(default_factory=list)
    session_key: Optional[str] = None
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None


def _refused() -> HTTPException:
    return HTTPException(status_code=401, detail=INVALID_CREDENTIALS)


class LdapLoginManager(AbstractBLLManager, RouterMixin):
    """Directory sign-in: no table of its own."""

    prefix: ClassVar[Optional[str]] = "/v1/auth/ldap"
    tags: ClassVar[Optional[List[str]]] = ["LDAP Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []

    def enabled_directories(self) -> List[LdapDirectoryModel]:
        DirectoryDB = LdapDirectoryModel.DB(self.model_registry.DB.manager.Base)
        found: List[LdapDirectoryModel] = DirectoryDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[DirectoryDB.enabled.is_(True), DirectoryDB.deleted_at.is_(None)],
            return_type="dto",
            override_dto=LdapDirectoryModel,
        )
        return found

    def _known_user(
        self, directory: LdapDirectoryModel, account: DirectoryAccount
    ) -> Optional[str]:
        """The local user this account signs in as, if any yet."""
        identity = find_identity(self.model_registry, directory.id, account.external_id)
        if identity is not None:
            return identity.user_id
        if directory.link_existing_by_email and account.email:
            users = _users(
                self.model_registry,
                email=UserManager._normalize_identifier(account.email),
            )
            if len(users) == 1:
                return str(users[0]["id"])
        return None

    def sign_in(
        self,
        directory_id: str,
        username: str,
        password: str,
        ip_address: Optional[str],
        response: Response,
    ) -> Dict[str, Any]:
        tracker = UserManager._lockout_tracker
        actor = ip_address or "unknown"
        if tracker.is_locked(actor, LOCKOUT_FLOW):
            remaining = tracker.remaining_lockout_seconds(actor, LOCKOUT_FLOW)
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Try again later.",
                headers={"Retry-After": str(int(remaining or 60))},
            )
        directory = load_directory(self.model_registry, directory_id)
        if not directory.enabled:
            raise HTTPException(status_code=404, detail="No such directory")

        user_ids: List[str] = []

        def admit(account: DirectoryAccount) -> None:
            user_id = self._known_user(directory, account)
            if user_id is None:
                return
            user_ids.append(user_id)
            assert_within = _lockout_hooks["assert_within_threshold"]
            if assert_within is not None:
                assert_within(user_id, self.model_registry)

        client = LDAPDirectoryClient(directory_settings(directory))
        try:
            account = client.authenticate(username, password, admit)
        except CredentialsRefused as refused:
            logger.info("ldap_consumer: sign-in refused: %s", refused.reason)
            tracker.record_failure(actor, LOCKOUT_FLOW)
            record = _lockout_hooks["record_failure"]
            if record is not None:
                for user_id in user_ids:
                    record(user_id, ip_address, self.model_registry)
            raise _refused()
        except BaseExternalError as exc:
            logger.warning("ldap_consumer: directory %s failed: %s", directory_id, exc)
            raise HTTPException(status_code=503, detail=DIRECTORY_UNAVAILABLE)
        tracker.clear(actor, LOCKOUT_FLOW)

        user = self._local_user(directory, account)
        challenge = UserManager.mfa_challenge(str(user["id"]), self.model_registry)
        if challenge is not None:
            return challenge
        completed: Dict[str, Any] = UserManager._complete_login(
            user, self.model_registry, response
        )
        return completed

    def _local_user(
        self, directory: LdapDirectoryModel, account: DirectoryAccount
    ) -> Dict[str, Any]:
        """The local user ``account`` signs in as: linked, linked now, or
        created (see the module docstring), with the identity refreshed."""
        identity = find_identity(self.model_registry, directory.id, account.external_id)
        if identity is not None:
            users = _users(self.model_registry, id=identity.user_id)
            if len(users) != 1 or not _active(users[0]):
                raise _refused()
            self._refresh(identity, account)
            return users[0]

        email = (
            UserManager._normalize_identifier(account.email) if account.email else None
        )
        existing = _users(self.model_registry, email=email) if email else []
        if existing:
            if not directory.link_existing_by_email:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "An account with this email already exists; an "
                        "administrator must link it to this directory"
                    ),
                )
            user = existing[0]
            if not _active(user):
                raise _refused()
        else:
            user = self._register(account, email)
        self._link(directory, account, str(user["id"]))
        return user

    def _register(
        self, account: DirectoryAccount, email: Optional[str]
    ) -> Dict[str, Any]:
        """A new local user for ``account``, as self-registration would make
        one: only when registration is open."""
        from zephyrex.lib.Environment import settings

        if settings.REGISTRATION_MODE == "closed":
            raise HTTPException(
                status_code=403, detail="User registration is currently closed"
            )
        if settings.REGISTRATION_MODE == "invite":
            raise HTTPException(
                status_code=403, detail="User registration requires an invitation"
            )
        if not email:
            raise HTTPException(
                status_code=403,
                detail=(
                    "The directory account has no email address; an "
                    "administrator must link it to an account"
                ),
            )
        fields: Dict[str, Any] = {"email": email}
        if account.display_name:
            fields["display_name"] = account.display_name
        try:
            UserModel.Create(**fields)
        except ValidationError:
            raise HTTPException(
                status_code=403,
                detail="The directory account's email address is not valid",
            )
        root_id = env("ROOT_ID")
        created = UserModel.DB(self.model_registry.DB.manager.Base).create(
            requester_id=root_id,
            model_registry=self.model_registry,
            override_dto=self.model_registry.apply(UserModel),
            return_type="dto",
            **fields,
        )
        # A password nobody knows: the account signs in through the
        # directory, and a password login for it fails like any wrong one.
        UserCredentialManager(
            requester_id=created.id,
            target_id=created.id,
            model_registry=self.model_registry,
        ).create(user_id=created.id, password=_unusable_password())
        return _users(self.model_registry, id=created.id)[0]

    def _link(
        self, directory: LdapDirectoryModel, account: DirectoryAccount, user_id: str
    ) -> None:
        _identity_db(self.model_registry).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=user_id,
            ldap_directory_id=directory.id,
            external_id=account.external_id,
            dn=account.dn,
            username=account.username,
            email=account.email,
            groups=list(account.groups),
            last_login_at=datetime.now(timezone.utc),
        )

    def _refresh(self, identity: LdapIdentityModel, account: DirectoryAccount) -> None:
        _identity_db(self.model_registry).update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=identity.id,
            new_properties={
                "dn": account.dn,
                "username": account.username,
                "email": account.email,
                "groups": list(account.groups),
                "last_login_at": datetime.now(timezone.utc),
            },
        )

    @custom_route(
        method="GET",
        path="/directories",
        output_model=LdapDirectoryList,
        authentication_type="none",
        openapi_tags=("LDAP Sign-in",),
        summary="The directories people can sign in with",
        expose_in=(ExposeIn.REST,),
    )
    def directories_route(self) -> LdapDirectoryList:
        return LdapDirectoryList(
            directories=[
                LdapDirectoryEntry(id=d.id, name=d.name)
                for d in self.enabled_directories()
            ]
        )

    @custom_route(
        method="POST",
        path="/login",
        input_model=LdapLoginRequest,
        output_model=LdapLoginResponse,
        authentication_type="none",
        openapi_tags=("LDAP Sign-in",),
        summary="Sign in with directory credentials",
        expose_in=(ExposeIn.REST,),
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def login_route(
        self, body: LdapLoginRequest, request: Request, response: Response
    ) -> LdapLoginResponse:
        """The session in the body for API clients and in the session
        cookies for browsers, exactly as POST /v1/user/authorize."""
        result = await asyncio.to_thread(
            self.sign_in,
            body.directory_id,
            body.username,
            body.password,
            resolve_client_ip(request),
            response,
        )
        return LdapLoginResponse(**result)


def _unusable_password() -> str:
    """Random, and meeting the password policy (a letter and a digit)."""
    return f"ldap-{secrets.token_hex(UNUSABLE_PASSWORD_BYTES)}-0"
