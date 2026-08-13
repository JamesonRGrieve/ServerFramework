"""API-key authentication BLL.

Owns ``APIKeyModel`` plus the issue/validate/revoke/rotate flow. Keys are
generated from ``secrets.token_urlsafe(32)``, returned to the user once at
issuance, and persisted as a SHA-256 hash for indexed lookup. Constant-time
comparison guards validation.

Pattern reference: ``auth_session/BLL_Session.py`` (canonical model
shape) and ``auth_magic_link/BLL_Auth_MagicLink.py`` (token issuance).

Endpoint surface: CRUD via ``RouterMixin`` is restricted to read/list/search
plus DELETE (revocation). Issue and rotate are surfaced through
``@custom_route`` because the raw key never persists — the issuance flow
must run server-side and return the secret exactly once. ``key_hash`` is
not in any client-facing input model: see ``APIKeyModel.Create``.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import ClassVar, List, Optional, Type

from zephyrex.lib.DateTimeUtils import ensure_utc

from fastapi import HTTPException
from pydantic import BaseModel, Field

from zephyrex.lib.CustomRoute import custom_route
from zephyrex.lib.Environment import env
from zephyrex.lib.InboundSecurity import DEFAULT_AUTH_RATE_LIMIT, rate_limit
from zephyrex.lib.Pydantic2FastAPI import AuthType, RouteType, RouterMixin
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth import (
    RoleModel,
    TeamModel,
    UserModel,
)


def _hash_key(raw: str) -> str:
    """SHA-256 hex digest of the raw API key. The raw key is high-entropy
    random so a per-key salt buys nothing material; using a uniform hash
    keeps the lookup index simple."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class APIKeyModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """Persistent API key record. The raw key is returned exactly once at
    issuance; only its SHA-256 hash is persisted."""

    Manager: ClassVar[Type["APIKeyManager"]] = None  # type: ignore[assignment]
    name: str = Field(..., description="Human-readable label for this key")
    key_hash: str = Field(..., description="SHA-256 hex digest of the issued key")
    last_used_at: Optional[datetime] = Field(
        None, description="When the key was most recently presented and accepted"
    )
    expires_at: Optional[datetime] = Field(
        None, description="When the key stops being honoured. None = no expiry."
    )
    is_revoked: bool = Field(False, description="Soft-revoke flag")

    table_comment: ClassVar[str] = (
        "API keys for programmatic access; hashed at rest, scoped to user/team/role"
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
        RoleModel.Reference.ID.Optional,
    ):
        # ``key_hash`` is intentionally NOT writable here. Direct CRUD
        # creation is allowed for administrative imports — but the hash
        # must come from a server-generated raw key via ``issue_key``,
        # never from client input. CRUD POST is gated to ROOT_ID by the
        # manager's ``create_validation``; non-root callers must use the
        # ``/issue`` custom route.
        name: str
        expires_at: Optional[datetime] = None

    class Update(BaseModel):
        name: Optional[str] = None
        expires_at: Optional[datetime] = None

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        name: Optional[StringSearchModel] = None
        is_revoked: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None


class APIKeyIssueResponse(BaseModel):
    """Returned exactly once at issuance. ``key`` is the raw value to
    present to the API; the server only stores its hash from this point on."""

    id: str
    name: str
    key: str = Field(
        ..., description="Raw API key — store securely; never returned again"
    )
    expires_at: Optional[datetime] = None


class APIKeyIssueRequest(BaseModel):
    name: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    role_id: Optional[str] = None
    expires_at: Optional[datetime] = None


class APIKeyValidateRequest(BaseModel):
    api_key: str = Field(..., description="Raw API key to validate")


class APIKeyValidateResponse(BaseModel):
    valid: bool
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    role_id: Optional[str] = None


class APIKeyRotateRequest(BaseModel):
    key_id: str = Field(..., description="ID of the key to rotate")


class APIKeyManager(AbstractBLLManager, RouterMixin):
    _model = APIKeyModel
    prefix: ClassVar[Optional[str]] = "/v1/auth/api-keys"
    tags: ClassVar[Optional[List[str]]] = ["API Keys"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # ``CREATE`` is intentionally absent from the public CRUD surface — see
    # the custom routes below. Direct creation through the BLL ``create``
    # path is still possible for administrative scripts but is gated by
    # ``create_validation`` to ROOT_ID. ``UPDATE`` is dropped so callers
    # cannot flip ``is_revoked`` or ``last_used_at`` directly; revocation
    # goes through ``DELETE`` (soft-revoke) and rotation through ``/rotate``.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.SEARCH,
        RouteType.DELETE,
    ]

    def create_validation(self, entity) -> None:
        """Refuse non-root direct CREATE; raw keys must flow through ``issue_key``.

        The CRUD ``Create`` schema deliberately omits ``key_hash``, so a
        bypass through that channel cannot mint a usable key — but we
        also reject the call outright so the resulting "shell" rows never
        exist. ``issue_key`` calls ``self.DB.create`` directly with the
        server-generated hash, bypassing this guard.
        """
        from zephyrex.database.StaticPermissions import is_root_id

        if not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Direct CREATE on /v1/auth/api-keys is not permitted; "
                "use POST /v1/auth/api-keys/issue",
            )

    def _assert_can_act_on(
        self,
        user_id: Optional[str],
        team_id: Optional[str],
    ) -> None:
        """Caller must be acting on themselves (user_id == requester) or be
        ROOT_ID. Team-scoped issuance requires either ROOT_ID or a real
        membership check; that is delegated to the ``UserTeamManager``
        once available, and conservatively rejects unauthenticated
        cross-tenant issuance until then."""
        from zephyrex.database.StaticPermissions import is_root_id

        if is_root_id(self.requester.id):
            return
        if user_id is not None and user_id != self.requester.id:
            raise HTTPException(
                status_code=403,
                detail="Cannot issue an API key for another user",
            )
        if team_id is not None:
            try:
                from zephyrex.logic.BLL_Auth import UserTeamModel
            except ImportError:
                raise HTTPException(
                    status_code=403,
                    detail="Team-scoped API-key issuance requires team membership",
                )
            UTDB = UserTeamModel.DB(self.model_registry.DB.manager.Base)
            if not UTDB.exists(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                user_id=self.requester.id,
                team_id=team_id,
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Caller is not a member of the target team",
                )

    def issue_key(
        self,
        name: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        role_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> APIKeyIssueResponse:
        if user_id is None and team_id is None:
            user_id = self.requester.id
        self._assert_can_act_on(user_id, team_id)
        raw = secrets.token_urlsafe(32)
        key_hash = _hash_key(raw)
        record = self.DB.create(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.model_registry.apply(self.Model),
            name=name,
            key_hash=key_hash,
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
            expires_at=expires_at,
            is_revoked=False,
        )
        return APIKeyIssueResponse(
            id=record.id, name=name, key=raw, expires_at=expires_at
        )

    @custom_route(
        method="POST",
        path="/issue",
        input_model=APIKeyIssueRequest,
        output_model=APIKeyIssueResponse,
        authentication_type="jwt",
        openapi_tags=("API Keys",),
        summary="Issue a new API key (raw value returned exactly once)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def issue_route(self, body: APIKeyIssueRequest) -> APIKeyIssueResponse:
        return self.issue_key(
            name=body.name,
            user_id=body.user_id,
            team_id=body.team_id,
            role_id=body.role_id,
            expires_at=body.expires_at,
        )

    @custom_route(
        method="POST",
        path="/validate",
        input_model=APIKeyValidateRequest,
        output_model=APIKeyValidateResponse,
        authentication_type="none",
        openapi_tags=("API Keys",),
        summary="Validate an API key",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def validate_route(
        self, body: APIKeyValidateRequest
    ) -> APIKeyValidateResponse:
        record = self.validate_key(body.api_key)
        if record is None:
            return APIKeyValidateResponse(valid=False)
        return APIKeyValidateResponse(
            valid=True,
            user_id=record.user_id,
            team_id=record.team_id,
            role_id=record.role_id,
        )

    @custom_route(
        method="POST",
        path="/rotate",
        input_model=APIKeyRotateRequest,
        output_model=APIKeyIssueResponse,
        authentication_type="jwt",
        openapi_tags=("API Keys",),
        summary="Rotate an existing API key (issues a replacement, revokes the old)",
    )
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    async def rotate_route(self, body: APIKeyRotateRequest) -> APIKeyIssueResponse:
        return self.rotate_key(body.key_id)

    def validate_key(self, raw: str) -> Optional[APIKeyModel]:
        """Resolve ``raw`` to an active API key record, or return None.

        Constant-time comparison after a hash-indexed lookup defeats
        timing attacks even though the hash is the canonical lookup key.
        """
        if not raw:
            return None
        candidate_hash = _hash_key(raw)
        KeyDB = APIKeyModel.DB(self.model_registry.DB.manager.Base)
        rows = (
            KeyDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    KeyDB.key_hash == candidate_hash,
                    KeyDB.is_revoked == False,  # noqa: E712
                ],
                return_type="dto",
                override_dto=APIKeyModel,
            )
            or []
        )
        now = datetime.now(timezone.utc)
        for record in rows:
            if record.expires_at is not None:
                expires_at = ensure_utc(record.expires_at)
                if expires_at < now:
                    continue
            if hmac.compare_digest(record.key_hash, candidate_hash):
                KeyDB.update(
                    requester_id=env("ROOT_ID"),
                    model_registry=self.model_registry,
                    id=record.id,
                    new_properties={"last_used_at": now},
                )
                return record  # type: ignore[no-any-return]
        return None

    def delete(self, id: str):
        """DELETE on this resource is soft-revoke, not hard-delete: API
        keys are bearer credentials that downstream services may have
        already cached, and the audit row must persist for forensic
        reasons. Authorization rides through ``revoke_key``."""
        self.revoke_key(id)

    def revoke_key(self, key_id: str) -> APIKeyModel:
        """Soft-revoke a key. Authorization: the requester must own the
        key (user_id) or be ROOT_ID. Team-scoped keys check membership."""
        KeyDB = APIKeyModel.DB(self.model_registry.DB.manager.Base)
        existing = KeyDB.get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=key_id,
            return_type="dto",
            override_dto=APIKeyModel,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="API key not found")
        self._assert_can_act_on(existing.user_id, existing.team_id)
        return KeyDB.update(  # type: ignore[no-any-return]
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=key_id,
            new_properties={"is_revoked": True},
            return_type="dto",
            override_dto=APIKeyModel,
        )

    def rotate_key(self, key_id: str) -> APIKeyIssueResponse:
        """Issue a replacement key, revoke the old one. Atomic-from-the-
        client's-perspective: caller persists the new ``key`` value before
        the old one is no longer accepted (revoke runs after create)."""
        existing = APIKeyModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=key_id,
            return_type="dto",
            override_dto=APIKeyModel,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="API key not found")
        self._assert_can_act_on(existing.user_id, existing.team_id)
        new = self.issue_key(
            name=existing.name,
            user_id=existing.user_id,
            team_id=existing.team_id,
            role_id=existing.role_id,
            expires_at=existing.expires_at,
        )
        self.revoke_key(key_id)
        return new


APIKeyModel.Manager = APIKeyManager


# ---------------------------------------------------------------------------
# Merge participation
# ---------------------------------------------------------------------------


def _merge_handler(ctx) -> None:
    """Revoke (don't migrate) the target user's API keys.

    API keys are bearer credentials that may have been distributed to
    external systems. Migrating them onto the surviving account would
    silently extend access; revocation forces the operator to mint
    fresh keys and re-distribute, which is the correct security posture
    for a merge.
    """
    from datetime import datetime, timezone

    from zephyrex.lib.Environment import env as _env

    KeyDB = APIKeyModel.DB(ctx.model_registry.DB.manager.Base)
    target_keys = (
        KeyDB.list(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            filters=[
                KeyDB.user_id == ctx.target_user_id,
                KeyDB.is_revoked == False,  # noqa: E712
            ],
            return_type="dto",
            override_dto=APIKeyModel,
        )
        or []
    )
    now = datetime.now(timezone.utc)
    for key in target_keys:
        KeyDB.update(
            requester_id=_env("ROOT_ID"),
            model_registry=ctx.model_registry,
            id=key.id,
            new_properties={"is_revoked": True, "last_used_at": now},
        )


def register_merge_participation() -> None:
    try:
        from zephyrex.extensions.auth_merge.BLL_Auth_Merge import (
            register_merge_handler,
        )
    except ImportError:
        return
    register_merge_handler("auth_api_keys", _merge_handler)
