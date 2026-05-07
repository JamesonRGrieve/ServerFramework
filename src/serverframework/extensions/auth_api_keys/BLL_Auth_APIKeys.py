"""API-key authentication BLL.

Owns ``APIKeyModel`` plus the issue/validate/revoke/rotate flow. Keys are
generated from ``secrets.token_urlsafe(32)``, returned to the user once at
issuance, and persisted as a SHA-256 hash for indexed lookup. Constant-time
comparison guards validation.

Pattern reference: ``auth_session/BLL_Session.py`` (canonical model
shape) and ``auth_magic_link/BLL_Auth_MagicLink.py`` (token issuance).
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import ClassVar, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, Field

from serverframework.lib.Environment import env
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
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

    Manager: ClassVar[Type["APIKeyManager"]] = None
    name: str = Field(..., description="Human-readable label for this key")
    key_hash: str = Field(
        ..., description="SHA-256 hex digest of the issued key"
    )
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
        name: str
        key_hash: str
        expires_at: Optional[datetime] = None
        is_revoked: bool = False
        last_used_at: Optional[datetime] = None

    class Update(BaseModel):
        name: Optional[str] = None
        is_revoked: Optional[bool] = None
        expires_at: Optional[datetime] = None
        last_used_at: Optional[datetime] = None

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
    key: str = Field(..., description="Raw API key — store securely; never returned again")
    expires_at: Optional[datetime] = None


class APIKeyManager(AbstractBLLManager, RouterMixin):
    _model = APIKeyModel
    prefix: ClassVar[Optional[str]] = "/v1/auth/api-keys"
    tags: ClassVar[Optional[List[str]]] = ["API Keys"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def issue_key(
        self,
        name: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
        role_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> APIKeyIssueResponse:
        if user_id is None and team_id is None:
            raise HTTPException(
                status_code=400,
                detail="API key must be scoped to a user_id or team_id",
            )
        raw = secrets.token_urlsafe(32)
        key_hash = _hash_key(raw)
        record = self.create(
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
                expires_at = record.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < now:
                    continue
            if hmac.compare_digest(record.key_hash, candidate_hash):
                self.update(id=record.id, last_used_at=now)
                return record
        return None

    def revoke_key(self, key_id: str) -> APIKeyModel:
        return self.update(id=key_id, is_revoked=True)

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
