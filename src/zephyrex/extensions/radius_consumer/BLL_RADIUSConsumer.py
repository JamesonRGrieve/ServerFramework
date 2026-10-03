# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in to this server through RADIUS.

Root configures RADIUS servers (``RadiusServerModel``): a service's hosts in
fail-over order, which share its users and secret, and how to reach them.
RADIUS over TLS (RadSec) is the default; plain UDP must first be enabled
for the deployment with ``RADIUS_CONSUMER_ALLOW_UDP=true``. The shared
secret and the TLS client key are write-only: stored encrypted, never
returned.

A user signs in at ``POST /v1/auth/radius/login`` with a server's id, a
username and a password. The server's Access-Accept signs them in with the
same session password sign-in issues (token in the body, session cookies
for a browser), or the MFA challenge password sign-in yields for a user
with a second factor. An Access-Reject is 401. An Access-Challenge (a
one-time code, a push to a token) yields ``radius_challenge`` and an opaque
``radius_challenge_token``; the answer goes to ``POST
/v1/auth/radius/challenge``. The RADIUS State behind the token stays on
this server, which sends the answer to the host that asked.

The account is the identity (``RadiusIdentityModel``) for the server and
the username. A first sign-in with no identity creates the account and its
identity when ``REGISTRATION_MODE`` is ``open`` and the server allows it;
under ``invite`` or ``closed`` only identities root has linked sign in.

Failures of the RADIUS servers (none answers, replies fail verification)
are 502 with a generic message; the reason goes to the log.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request, Response, status
from pydantic import BaseModel as RouteModel
from pydantic import Field

from zephyrex.database.StaticPermissions import is_root_id
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
)
from zephyrex.extensions.radius_consumer.RADIUSClient import (
    RADIUS_PORT,
    RADSEC_PORT,
    RADSEC_SECRET,
    TRANSPORTS,
    Endpoint,
    RADIUSClient,
    Reply,
    ServiceSettings,
    TLSSettings,
    Transport,
)
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.SecretEncryption import decrypt_secret, encrypt_secret
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import UserManager, UserModel, refuse_internal_account
from zephyrex.pydantic2.fastapi import AuthType, RouterMixin
from zephyrex.pydantic2.registry import BaseModel

ALLOW_UDP_ENV = "RADIUS_CONSUMER_ALLOW_UDP"
MAX_HOSTS = 8
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 30
MAX_RETRIES = 5
# Blast-RADIUS advisories: a UDP shared secret should be long and random.
MIN_UDP_SECRET_LENGTH = 16
CHALLENGE_TTL_SECONDS = 300
CHALLENGE_TOKEN_BYTES = 32
LOCKOUT_SCOPE = "radius_login"
ROOT_ONLY = "Only root manages RADIUS sign-in"
SERVICE_FAILED = "The RADIUS server could not confirm the sign-in"
INVALID_CREDENTIALS = "Invalid credentials"
USERNAME_TAKEN = "That username on that server already signs in to an account"
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_RETRIES = 2


def udp_allowed() -> bool:
    return (env(ALLOW_UDP_ENV) or "").strip().lower() == "true"


def _secret(stored: Optional[str]) -> Optional[str]:
    return decrypt_secret(stored) if stored else None


def _live(db_cls: Any) -> List[Any]:
    """Root reads soft-deleted rows too; this keeps them out."""
    return [db_cls.deleted_at.is_(None)]


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
    )


def service_settings(fields: Dict[str, Any]) -> ServiceSettings:
    """The client settings ``fields`` (a server's columns, its secrets in
    plain text) describe; 422 naming the first thing wrong with them."""
    named = fields.get("transport") or "radsec"
    transport: Transport
    if named == "radsec":
        transport = "radsec"
    elif named == "udp":
        transport = "udp"
    else:
        raise _unprocessable(f"transport is one of {', '.join(TRANSPORTS)}")
    if transport == "udp" and not udp_allowed():
        raise _unprocessable(
            f"Plain RADIUS over UDP is off; set {ALLOW_UDP_ENV}=true to allow it"
        )
    hosts = fields.get("hosts") or []
    if not isinstance(hosts, list) or not 1 <= len(hosts) <= MAX_HOSTS:
        raise _unprocessable(f"hosts lists 1-{MAX_HOSTS} RADIUS servers")
    default_port = RADSEC_PORT if transport == "radsec" else RADIUS_PORT
    timeout = fields.get("timeout_seconds")
    timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
    retries = fields.get("retries")
    retries = DEFAULT_RETRIES if retries is None else retries
    if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise _unprocessable(
            f"timeout_seconds is {MIN_TIMEOUT_SECONDS}-{MAX_TIMEOUT_SECONDS}"
        )
    if not 0 <= retries <= MAX_RETRIES:
        raise _unprocessable(f"retries is 0-{MAX_RETRIES}")
    try:
        endpoints = tuple(Endpoint.parse(str(h), default_port) for h in hosts)
    except InvalidInputExternalError as exc:
        raise _unprocessable(exc.message) from exc
    secret = fields.get("shared_secret") or ""
    tls: Optional[TLSSettings] = None
    if transport == "udp":
        if len(secret) < MIN_UDP_SECRET_LENGTH:
            raise _unprocessable(
                f"Over UDP the shared secret is at least "
                f"{MIN_UDP_SECRET_LENGTH} characters"
            )
    else:
        secret = secret or RADSEC_SECRET
        pems = [
            fields.get(key)
            for key in ("tls_ca_pem", "tls_client_cert_pem", "tls_client_key_pem")
        ]
        if not all(pems):
            raise _unprocessable(
                "RadSec needs tls_ca_pem, tls_client_cert_pem and tls_client_key_pem"
            )
        tls = TLSSettings(
            ca_pem=str(pems[0]),
            client_cert_pem=str(pems[1]),
            client_key_pem=str(pems[2]),
            server_name=fields.get("tls_server_name") or None,
        )
    return ServiceSettings(
        endpoints=endpoints,
        transport=transport,
        secret=secret.encode("utf-8"),
        timeout_seconds=float(timeout),
        retries=int(retries),
        nas_identifier=fields.get("nas_identifier") or None,
        tls=tls,
    )


def checked_service_settings(fields: Dict[str, Any]) -> ServiceSettings:
    """:func:`service_settings`, with the TLS material loaded once to prove
    it is usable."""
    settings = service_settings(fields)
    if settings.tls is not None:
        try:
            settings.tls.check()
        except InvalidInputExternalError as exc:
            raise _unprocessable(exc.message) from exc
    return settings


class RadiusServerModel(ApplicationModel, UpdateMixinModel, metaclass=ModelMeta):
    """A RADIUS service users sign in through."""

    name: str = Field(..., description="Shown on the sign-in page")
    hosts: List[str] = Field(
        ...,
        description="host, host:port or [v6]:port, in fail-over order; "
        "they share this service's users and secret",
    )
    transport: str = Field(
        "radsec", description="radsec (RADIUS over TLS, RFC 6614) or udp"
    )
    shared_secret: Optional[str] = Field(
        None,
        exclude=True,
        description="Shared secret (write-only); over RadSec it defaults to 'radsec'",
    )
    timeout_seconds: int = Field(
        DEFAULT_TIMEOUT_SECONDS, description="Seconds to wait for each reply"
    )
    retries: int = Field(
        DEFAULT_RETRIES,
        description="UDP retransmissions to a host before failing over",
    )
    nas_identifier: Optional[str] = Field(
        None, description="NAS-Identifier sent with each request"
    )
    tls_ca_pem: Optional[str] = Field(
        None, description="RadSec: CA certificate(s) the servers' certificates chain to"
    )
    tls_client_cert_pem: Optional[str] = Field(
        None, description="RadSec: this server's client certificate"
    )
    tls_client_key_pem: Optional[str] = Field(
        None, exclude=True, description="RadSec: its private key (write-only)"
    )
    tls_server_name: Optional[str] = Field(
        None, description="RadSec: the name the servers' certificates carry"
    )
    is_enabled: bool = Field(True, description="Offered for sign-in")
    jit_create_users: bool = Field(
        True,
        description="A first sign-in creates the account (when registration is open)",
    )

    table_comment: ClassVar[str] = "RADIUS services users sign in through"

    class Create(BaseModel):
        name: str
        hosts: List[str]
        transport: str = "radsec"
        shared_secret: Optional[str] = None
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
        retries: int = DEFAULT_RETRIES
        nas_identifier: Optional[str] = None
        tls_ca_pem: Optional[str] = None
        tls_client_cert_pem: Optional[str] = None
        tls_client_key_pem: Optional[str] = None
        tls_server_name: Optional[str] = None
        is_enabled: bool = True
        jit_create_users: bool = True

    class Update(BaseModel):
        name: Optional[str] = None
        hosts: Optional[List[str]] = None
        transport: Optional[str] = None
        shared_secret: Optional[str] = None
        timeout_seconds: Optional[int] = None
        retries: Optional[int] = None
        nas_identifier: Optional[str] = None
        tls_ca_pem: Optional[str] = None
        tls_client_cert_pem: Optional[str] = None
        tls_client_key_pem: Optional[str] = None
        tls_server_name: Optional[str] = None
        is_enabled: Optional[bool] = None
        jit_create_users: Optional[bool] = None

    class Search(ApplicationModel.Search, UpdateMixinModel.Search):
        name: Optional[StringSearchModel] = None
        transport: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None


SECRET_FIELDS: Tuple[str, ...] = ("shared_secret", "tls_client_key_pem")


def plain_fields(server: RadiusServerModel) -> Dict[str, Any]:
    """``server``'s columns, its secrets decrypted."""
    fields = {name: getattr(server, name) for name in RadiusServerModel.model_fields}
    for name in SECRET_FIELDS:
        fields[name] = _secret(fields[name])
    return fields


class RadiusIdentityModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    RadiusServerModel.Reference,
    metaclass=ModelMeta,
):
    """The account a RADIUS server's username signs in to."""

    username: str = Field(..., description="User-Name the RADIUS server knows")
    last_login_at: Optional[datetime] = Field(
        None, description="Last sign-in through this identity"
    )

    table_comment: ClassVar[str] = "Accounts RADIUS usernames sign in to"
    permission_references: ClassVar[List[str]] = ["radius_server"]

    class Create(BaseModel, UserModel.Reference.ID, RadiusServerModel.Reference.ID):
        username: str

    class Update(BaseModel):
        username: Optional[str] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        RadiusServerModel.Reference.ID.Search,
    ):
        username: Optional[StringSearchModel] = None
        last_login_at: Optional[DateSearchModel] = None


class RadiusChallengeModel(
    ApplicationModel,
    UpdateMixinModel,
    RadiusServerModel.Reference,
    metaclass=ModelMeta,
):
    """An Access-Challenge awaiting its answer: the RADIUS State, kept here
    and never sent to the browser, behind a hashed opaque token."""

    token_hash: str = Field(..., description="SHA-256 of the challenge token")
    host_index: int = Field(..., description="Which host asked (its index)")
    username: str = Field(..., description="Who is signing in")
    state_hex: str = Field(..., description="The RADIUS State to send back")
    expires_at: datetime = Field(..., description="When the answer is too late")

    table_comment: ClassVar[str] = "Access-Challenges awaiting an answer"
    permission_references: ClassVar[List[str]] = ["radius_server"]

    class Create(BaseModel, RadiusServerModel.Reference.ID):
        token_hash: str
        host_index: int
        username: str
        state_hex: str
        expires_at: datetime

    class Update(BaseModel):
        pass

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        RadiusServerModel.Reference.ID.Search,
    ):
        expires_at: Optional[DateSearchModel] = None


class RootManagedManager(AbstractBLLManager):
    """Writes by root only. Reads follow from that: a record root created
    is visible to root alone."""

    def _require_root(self) -> None:
        if not is_root_id(self.requester.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ROOT_ONLY)

    def _create_single_entity(self, **kwargs: Any) -> Any:
        self._require_root()
        return super()._create_single_entity(**self.prepare_create(kwargs))

    def update(self, id: str, **kwargs: Any) -> Any:
        self._require_root()
        return super().update(id, **self.prepare_update(id, kwargs))

    def delete(self, id: str) -> None:
        self._require_root()
        super().delete(id)

    def prepare_create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return fields

    def prepare_update(self, id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        return fields


class RadiusServerManager(RootManagedManager, RouterMixin):
    _model = RadiusServerModel

    prefix: ClassVar[Optional[str]] = "/v1/radius-consumer/servers"
    tags: ClassVar[Optional[List[str]]] = ["RADIUS Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    @staticmethod
    def _sealed(fields: Dict[str, Any]) -> Dict[str, Any]:
        for name in SECRET_FIELDS:
            if fields.get(name):
                fields[name] = encrypt_secret(fields[name])
        return fields

    def prepare_create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        checked_service_settings(fields)
        return self._sealed(fields)

    def prepare_update(self, id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        current = plain_fields(self.get(id=id))
        changed = {k: v for k, v in fields.items() if v is not None}
        checked_service_settings({**current, **changed})
        return self._sealed(fields)


class RadiusIdentityManager(RootManagedManager, RouterMixin):
    _model = RadiusIdentityModel

    prefix: ClassVar[Optional[str]] = "/v1/radius-consumer/identities"
    tags: ClassVar[Optional[List[str]]] = ["RADIUS Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def _linked(self, server_id: Any, username: str) -> List[RadiusIdentityModel]:
        found: List[RadiusIdentityModel] = self.list(
            filters=_live(self.DB), radius_server_id=server_id, username=username
        )
        return found

    def prepare_create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        server_id, username = fields.get("radius_server_id"), fields.get("username")
        if not username:
            raise _unprocessable("username is required")
        RadiusServerManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=server_id)
        refuse_internal_account(fields.get("user_id"))
        UserManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        ).get(id=fields.get("user_id"))
        if self._linked(server_id, username):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN
            )
        return fields

    def prepare_update(self, id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        if fields.get("user_id") is not None:
            refuse_internal_account(fields["user_id"])
        username = fields.get("username")
        if username is not None:
            current = self.get(id=id)
            taken = self._linked(current.radius_server_id, username)
            if any(other.id != id for other in taken):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=USERNAME_TAKEN
                )
        return fields


class RadiusServerChoice(RouteModel):
    id: str
    name: str


class RadiusServerList(RouteModel):
    servers: List[RadiusServerChoice]


class RadiusLogin(RouteModel):
    server_id: str = Field(..., description="The RADIUS server's id")
    username: str = Field(..., min_length=1, max_length=253)
    password: str = Field(..., min_length=1, max_length=128)


class RadiusChallengeAnswer(RouteModel):
    radius_challenge_token: str = Field(..., min_length=1, max_length=256)
    response: str = Field(
        ..., min_length=1, max_length=128, description="The code, or other answer"
    )


class RadiusLoginResult(RouteModel):
    """What password sign-in answers (``token``, ``user``, ``teams``,
    ``preferences``, ``session_key``), or its MFA challenge
    (``mfa_required``, ``challenge_token``, ``methods``), or a RADIUS
    challenge to answer (``radius_challenge``, ``radius_challenge_token``,
    ``reply_message``)."""

    token: Optional[str] = None
    session_key: Optional[str] = None
    user: Optional[Dict[str, Any]] = None
    teams: Optional[List[Dict[str, Any]]] = None
    preferences: Optional[Dict[str, Any]] = None
    mfa_required: bool = False
    challenge_token: Optional[str] = None
    methods: Optional[List[Dict[str, str]]] = None
    radius_challenge: bool = False
    radius_challenge_token: Optional[str] = None
    reply_message: Optional[str] = None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _client_ip(request: Optional[Request]) -> str:
    if request is not None and request.client is not None:
        return str(request.client.host)
    return "unknown"


class RadiusLoginManager(AbstractBLLManager, RouterMixin):
    """The sign-in routes, and the challenges they hold."""

    _model = RadiusChallengeModel

    prefix: ClassVar[Optional[str]] = "/v1/auth/radius"
    tags: ClassVar[Optional[List[str]]] = ["RADIUS Sign-in"]
    auth_type: ClassVar[AuthType] = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[Any]]] = []

    def _root(self, manager: Any) -> Any:
        return manager(requester_id=env("ROOT_ID"), model_registry=self.model_registry)

    def _servers(self, **criteria: Any) -> List[RadiusServerModel]:
        """Enabled servers that are not deleted, matching ``criteria``."""
        manager = self._root(RadiusServerManager)
        found: List[RadiusServerModel] = manager.list(
            filters=_live(manager.DB), is_enabled=True, **criteria
        )
        return found

    def _enabled_server(self, server_id: str) -> RadiusServerModel:
        found = self._servers(id=server_id)
        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="No such RADIUS server"
            )
        return found[0]

    def enabled_servers(self) -> RadiusServerList:
        servers = self._servers()
        return RadiusServerList(
            servers=[RadiusServerChoice(id=s.id, name=s.name) for s in servers]
        )

    @staticmethod
    def _client(server: RadiusServerModel) -> RADIUSClient:
        try:
            return RADIUSClient(service_settings(plain_fields(server)))
        except HTTPException as exc:
            logger.warning(
                "RADIUS server %s is misconfigured: %s", server.id, exc.detail
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=SERVICE_FAILED
            ) from exc

    @staticmethod
    def _ask(
        server: RadiusServerModel,
        username: str,
        password: str,
        state: Optional[bytes] = None,
        host_index: Optional[int] = None,
    ) -> Reply:
        client = RadiusLoginManager._client(server)
        try:
            return client.authenticate(
                username, password, state=state, endpoint_index=host_index
            )
        except InvalidInputExternalError as exc:
            raise _unprocessable(exc.message) from exc
        except BaseExternalError as exc:
            logger.warning("RADIUS sign-in via %s failed: %s", server.id, exc.message)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=SERVICE_FAILED
            ) from exc

    @staticmethod
    def _guard(ip: str) -> None:
        tracker = UserManager._lockout_tracker
        if tracker.is_locked(ip, LOCKOUT_SCOPE):
            remaining = tracker.remaining_lockout_seconds(ip, LOCKOUT_SCOPE)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts. Try again later.",
                headers={"Retry-After": str(int(remaining or 60))},
            )

    def sign_in(
        self, body: RadiusLogin, response: Response, ip: str
    ) -> RadiusLoginResult:
        self._guard(ip)
        server = self._enabled_server(body.server_id)
        reply = self._ask(server, body.username, body.password)
        return self._conclude(server, body.username, reply, response, ip)

    def answer(
        self, body: RadiusChallengeAnswer, response: Response, ip: str
    ) -> RadiusLoginResult:
        self._guard(ip)
        challenge = self._redeem(body.radius_challenge_token)
        server = self._enabled_server(challenge.radius_server_id)
        reply = self._ask(
            server,
            challenge.username,
            body.response,
            state=bytes.fromhex(challenge.state_hex),
            host_index=challenge.host_index,
        )
        return self._conclude(server, challenge.username, reply, response, ip)

    def _conclude(
        self,
        server: RadiusServerModel,
        username: str,
        reply: Reply,
        response: Response,
        ip: str,
    ) -> RadiusLoginResult:
        tracker = UserManager._lockout_tracker
        if reply.outcome == "reject":
            tracker.record_failure(ip, LOCKOUT_SCOPE)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS
            )
        if reply.outcome == "challenge":
            if not reply.state:
                logger.warning("RADIUS server %s challenged without State", server.id)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=SERVICE_FAILED
                )
            return RadiusLoginResult(
                radius_challenge=True,
                radius_challenge_token=self._hold(
                    server, username, reply.endpoint_index, reply.state
                ),
                reply_message=reply.reply_message,
            )
        tracker.clear(ip, LOCKOUT_SCOPE)
        user = self._account(server, username)
        challenge = UserManager.mfa_challenge(str(user["id"]), self.model_registry)
        if challenge is not None:
            return RadiusLoginResult(**challenge)
        return RadiusLoginResult(
            **UserManager._complete_login(user, self.model_registry, response)
        )

    def _hold(
        self, server: RadiusServerModel, username: str, host_index: int, state: bytes
    ) -> str:
        """Keep a challenge's State; the token that redeems it."""
        token = secrets.token_urlsafe(CHALLENGE_TOKEN_BYTES)
        RadiusChallengeModel.DB(self.model_registry.DB.manager.Base).create(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            radius_server_id=server.id,
            token_hash=_token_hash(token),
            host_index=host_index,
            username=username,
            state_hex=state.hex(),
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        )
        return token

    def _redeem(self, token: str) -> RadiusChallengeModel:
        """The challenge ``token`` stands for, spent; 401 when there is
        none, it expired, or it was answered already."""
        ChallengeDB = RadiusChallengeModel.DB(self.model_registry.DB.manager.Base)
        found = ChallengeDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            filters=[ChallengeDB.token_hash == _token_hash(token), *_live(ChallengeDB)],
            return_type="dto",
            override_dto=RadiusChallengeModel,
        )
        if not found:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired RADIUS challenge",
            )
        challenge: RadiusChallengeModel = found[0]
        ChallengeDB.delete(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=challenge.id,
        )
        if _aware(challenge.expires_at) <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired RADIUS challenge",
            )
        return challenge

    def _account(self, server: RadiusServerModel, username: str) -> Dict[str, Any]:
        """The active account ``username`` on ``server`` signs in to,
        created on a first sign-in when registration allows."""
        from zephyrex.lib.Environment import settings

        root_id = env("ROOT_ID")
        base = self.model_registry.DB.manager.Base
        IdentityDB = RadiusIdentityModel.DB(base)
        UserDB = UserModel.DB(base)
        identities = IdentityDB.list(
            requester_id=root_id,
            model_registry=self.model_registry,
            filters=[
                IdentityDB.radius_server_id == server.id,
                IdentityDB.username == username,
                *_live(IdentityDB),
            ],
            return_type="dto",
            override_dto=RadiusIdentityModel,
        )
        now = datetime.now(timezone.utc)
        if identities:
            identity: RadiusIdentityModel = identities[0]
            refuse_internal_account(identity.user_id)
            users = UserDB.list(
                requester_id=root_id,
                model_registry=self.model_registry,
                id=identity.user_id,
            )
            # The account may be disabled or deleted; the identity then
            # signs no one in.
            if len(users) != 1 or not users[0]["active"] or users[0]["deleted_at"]:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=INVALID_CREDENTIALS,
                )
            IdentityDB.update(
                requester_id=root_id,
                model_registry=self.model_registry,
                id=identity.id,
                new_properties={"last_login_at": now},
            )
            user: Dict[str, Any] = users[0]
            return user
        mode = settings.REGISTRATION_MODE
        if mode != "open" or not server.jit_create_users:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No account is linked to this RADIUS identity",
            )
        created = UserDB.create(
            requester_id=root_id,
            model_registry=self.model_registry,
            override_dto=self.model_registry.apply(UserModel),
            return_type="dto",
            display_name=username,
        )
        IdentityDB.create(
            requester_id=root_id,
            model_registry=self.model_registry,
            user_id=created.id,
            radius_server_id=server.id,
            username=username,
            last_login_at=now,
        )
        logger.info("RADIUS sign-in created account %s via %s", created.id, server.id)
        fresh: List[Dict[str, Any]] = UserDB.list(
            requester_id=root_id, model_registry=self.model_registry, id=created.id
        )
        return fresh[0]

    @custom_route(
        method="GET",
        path="/servers",
        output_model=RadiusServerList,
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("RADIUS Sign-in",),
        summary="The RADIUS servers users can sign in through",
    )
    def servers_route(self) -> RadiusServerList:
        return self.enabled_servers()

    @custom_route(
        method="POST",
        path="/login",
        input_model=RadiusLogin,
        output_model=RadiusLoginResult,
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("RADIUS Sign-in",),
        summary="Sign in with a username and password a RADIUS server checks",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def login_route(
        self, body: RadiusLogin, request: Request, response: Response
    ) -> RadiusLoginResult:
        return self.sign_in(body, response, _client_ip(request))

    @custom_route(
        method="POST",
        path="/challenge",
        input_model=RadiusChallengeAnswer,
        output_model=RadiusLoginResult,
        authentication_type="none",
        expose_in=(ExposeIn.REST,),
        openapi_tags=("RADIUS Sign-in",),
        summary="Answer a RADIUS server's challenge (a one-time code)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def challenge_route(
        self, body: RadiusChallengeAnswer, request: Request, response: Response
    ) -> RadiusLoginResult:
        return self.answer(body, response, _client_ip(request))
