"""SessionModel + SessionManager and the hook implementations they back.

Extracted from ``logic/BLL_Auth.py``. Core JWT issuance no longer references
the session row directly; instead it dispatches through the
``_session_hooks`` registry in ``BLL_Auth``. When this extension is loaded,
``EXT_Session.AuthSessionExtension.on_load`` registers the implementations
in this module.

Without this extension, core JWTs are stateless — they verify on
signature/exp/aud/iss/jti/nbf but no row tracks them, so revocation, sign-
out-all-devices, and device-pairing pending-state are all unavailable.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Dict, List, Literal, Optional, Type

from fastapi import HTTPException
from pydantic import Field

from serverframework.lib.Environment import env
from serverframework.lib.Pydantic import BaseModel
from serverframework.lib.Pydantic2FastAPI import (
    AuthType,
    RouteType,
    RouterMixin,
)
from serverframework.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ModelMeta,
    NumericalSearchModel,
    StringSearchModel,
    UpdateMixinModel,
)
from serverframework.logic.BLL_Auth import (
    PendingSessionError,
    UserModel,
)


class SessionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    """Persisted authentication-session row.

    Bound to a JWT via ``session_key`` (the JWT's ``jti`` claim). Verifying
    a token against this row is what makes JWTs revocable; without the
    extension the row simply does not exist and the verify path is
    stateless.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}
    Manager: ClassVar[Type["SessionManager"]] = None  # type: ignore[assignment]
    session_key: str = Field(
        ..., description="Unique session identifier used in JWT jti claim"
    )
    jwt_issued_at: datetime = Field(..., description="When the JWT was issued")
    refresh_token_hash: Optional[str] = Field(
        None, description="Hash of refresh token if refresh mechanism is enabled"
    )
    device_type: Optional[str] = Field(
        None,
        description="Type of device used for authentication (mobile, desktop, etc.)",
    )
    device_name: Optional[str] = Field(
        None, description="Name of the device if provided"
    )
    browser: Optional[str] = Field(
        None, description="Browser information from user agent"
    )
    is_active: bool = Field(
        True, description="Whether this session is currently active"
    )
    last_activity: datetime = Field(
        ..., description="Timestamp of last activity in this session"
    )
    expires_at: datetime = Field(..., description="When this session expires")
    revoked: bool = Field(
        False, description="Whether this session has been explicitly revoked"
    )
    trust_score: int = Field(50, description="Trust level of this session (0-100)")
    requires_verification: bool = Field(
        False, description="Whether additional verification is required"
    )
    grant_type: Optional[str] = Field(
        None,
        description="Identifier of the grant kind that issued this session (observability only)",
    )
    pending_state: Optional[Literal["awaiting_approval", "approved", "denied"]] = Field(
        None,
        description="Pending approval state for cross-device passwordless flows",
    )

    table_comment: ClassVar[str] = (
        "Active user authentication sessions and related metadata"
    )

    @classmethod
    def user_has_all_access(
        cls, user_id, id, db, db_manager=None, model_registry=None
    ):
        """Allow users to delete their own sessions even if a different
        actor created them (e.g. an admin minted the row on a remote device).
        """
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")
        from serverframework.database.StaticPermissions import (
            is_root_id,
            is_system_user_id,
        )

        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        SQLAlchemy_model = cls.DB(Base)
        session_record = (
            db.query(SQLAlchemy_model)
            .filter(SQLAlchemy_model.id == id)
            .first()
        )
        if session_record is None:
            return False
        if (
            hasattr(session_record, "user_id")
            and session_record.user_id == user_id
        ):
            return True

        return super().user_has_all_access(user_id, id, db, db_manager)

    # M-5 — the Create schema is the public input contract. Even though
    # ``routes_to_register`` currently omits POST, schema fields are
    # reachable from any future router-config that re-enables it (and
    # from internal callers). Server-controlled state (refresh-token
    # hash, trust score, pending-state machine) is populated via
    # ``**kwargs`` from the session-management code paths and MUST NOT
    # be acceptable client input.
    class Create(BaseModel, UserModel.Reference.ID):
        session_key: str = Field(
            ..., description="Unique session identifier used in JWT jti claim"
        )
        jwt_issued_at: datetime = Field(..., description="When the JWT was issued")
        device_type: Optional[str] = Field(
            None,
            description="Type of device used for authentication (mobile, desktop, etc.)",
        )
        device_name: Optional[str] = Field(
            None, description="Name of the device if provided"
        )
        browser: Optional[str] = Field(
            None, description="Browser information from user agent"
        )
        is_active: Optional[bool] = Field(
            True, description="Whether this session is currently active"
        )
        last_activity: datetime = Field(
            ..., description="Timestamp of last activity in this session"
        )
        expires_at: datetime = Field(..., description="When this session expires")
        revoked: Optional[bool] = Field(
            False, description="Whether this session has been explicitly revoked"
        )
        requires_verification: Optional[bool] = Field(
            False, description="Whether additional verification is required"
        )
        grant_type: Optional[str] = Field(
            None,
            description="Identifier of the grant kind that issued this session",
        )

    # Defense in depth: even though the manager's ``routes_to_register``
    # restricts the public HTTP surface to LIST + GET, we keep
    # ``refresh_token_hash`` and ``trust_score`` out of the Update
    # schema. Both would be catastrophic if a future router config
    # accidentally re-exposed UPDATE — overwriting the hash with a
    # known plaintext is account takeover. The fields kept here are
    # the lifecycle bits the framework legitimately mutates from
    # session-management code paths.
    class Update(BaseModel):
        is_active: Optional[bool] = Field(None)
        last_activity: Optional[datetime] = Field(None)
        expires_at: Optional[datetime] = Field(None)
        revoked: Optional[bool] = Field(None)
        requires_verification: Optional[bool] = Field(None)
        pending_state: Optional[
            Literal["awaiting_approval", "approved", "denied"]
        ] = Field(None)

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        session_key: Optional[StringSearchModel] = None
        is_active: Optional[bool] = None
        revoked: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None
        device_type: Optional[StringSearchModel] = None
        browser: Optional[StringSearchModel] = None
        requires_verification: Optional[bool] = None
        trust_score: Optional[NumericalSearchModel] = None
        grant_type: Optional[StringSearchModel] = None
        pending_state: Optional[StringSearchModel] = None


class SessionManager(AbstractBLLManager, RouterMixin):
    """CRUD + admin operations on the persisted session row.

    Endpoints under ``/v1/session``:
      - ``LIST`` / ``GET`` — owner reads their own sessions.
      - ``DELETE /{id}`` — revoke a single session (``revoke_session``).
      - bulk revoke and listing helpers used by the parent ``UserManager``.
    """

    _model = SessionModel

    prefix: ClassVar[Optional[str]] = "/v1/session"
    tags: ClassVar[Optional[List[str]]] = ["User Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.LIST,
        RouteType.GET,
    ]
    auth_dependency: ClassVar[Optional[str]] = "get_user_session_manager"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}",
            "method": "delete",
            "function": "revoke_session",
            "summary": "Revoke session",
            "description": "Revokes a user session.",
            "status_code": 204,
        }
    ]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def create_validation(self, entity) -> None:
        """Refuse a duplicate ``session_key``."""
        if self.Model.DB(self.model_registry.DB.manager.Base).exists(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            session_key=entity.session_key,
        ):
            raise HTTPException(
                status_code=400, detail="Session key already exists"
            )

    def revoke_session(self, id: str) -> Dict[str, str]:
        # Sessions are minted by the framework as ROOT (the user is not
        # yet known when the row is materialized in the login pipeline),
        # so the row's ``created_by_user_id`` is ROOT_ID. The DB layer's
        # "system entity" rule then refuses regular-user mutations.
        # Resolve the session as ROOT, verify the caller owns it, then
        # apply the revocation as ROOT — the caller's authority is the
        # session.user_id match, not the row provenance.
        from serverframework.database.StaticPermissions import is_root_id

        SessionDB = self.Model.DB(self.model_registry.DB.manager.Base)
        session = SessionDB.get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=id,
            return_type="dto",
            override_dto=self.Model,
        )
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if (
            session.user_id != self.requester.id
            and not is_root_id(self.requester.id)
        ):
            raise HTTPException(
                status_code=403,
                detail="Cannot revoke another user's session",
            )
        SessionDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=id,
            new_properties={"revoked": True, "is_active": False},
        )
        return {"message": "Session revoked successfully"}

    def revoke_all_user_sessions(self, user_id: str) -> Dict[str, Any]:
        # Same root-write pattern as ``revoke_session``: rows are
        # ROOT-owned, so we authenticate the caller (must be the user
        # themselves or ROOT) and then mutate as ROOT.
        from serverframework.database.StaticPermissions import is_root_id

        if user_id != self.requester.id and not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail="Cannot revoke another user's sessions",
            )
        SessionDB = self.Model.DB(self.model_registry.DB.manager.Base)
        sessions = SessionDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=user_id,
            is_active=True,
            revoked=False,
        )
        revoked_count = 0
        for session in sessions:
            session_id = session["id"] if isinstance(session, dict) else session.id
            SessionDB.update(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=session_id,
                new_properties={"is_active": False, "revoked": True},
            )
            revoked_count += 1
        return {
            "message": f"Revoked {revoked_count} sessions successfully",
            "revoked_count": revoked_count,
        }

    def update_activity(self, session_key: str) -> Dict[str, str]:
        sessions = self.Model.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            session_key=session_key,
        )
        if not sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = sessions[0]
        session_id = session["id"] if isinstance(session, dict) else session.id
        self.Model.DB(self.model_registry.DB.manager.Base).update(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=session_id,
            new_properties={"last_activity": datetime.now(timezone.utc)},
        )
        return {"message": "Session activity updated successfully"}

    def validate_session(self, session_key: str) -> bool:
        sessions = self.Model.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            session_key=session_key,
            is_active=True,
            revoked=False,
        )
        if not sessions:
            return False
        session = sessions[0]
        expires_at = (
            session["expires_at"]
            if isinstance(session, dict)
            else session.expires_at
        )
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    def cleanup_expired_sessions(self) -> Dict[str, Any]:
        current_time = datetime.now(timezone.utc)
        expired_sessions = self.Model.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            filters=[
                self.Model.DB(self.model_registry.DB.manager.Base).expires_at
                < current_time,
                self.Model.DB(self.model_registry.DB.manager.Base).is_active == True,
            ],
        )
        cleaned_count = 0
        for session in expired_sessions:
            session_id = session["id"] if isinstance(session, dict) else session.id
            self.Model.DB(self.model_registry.DB.manager.Base).update(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=session_id,
                new_properties={"is_active": False},
            )
            cleaned_count += 1
        return {
            "message": f"Cleaned up {cleaned_count} expired sessions",
            "cleaned_count": cleaned_count,
        }

    def get_user_sessions(
        self, user_id: str, active_only: bool = True
    ) -> List[SessionModel]:
        filters: Dict[str, Any] = {"user_id": user_id}
        if active_only:
            filters.update({"is_active": True, "revoked": False})
        return self.Model.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=SessionModel,
            **filters,
        )

    def revoke_sessions(self, user_id: str) -> int:
        return self.revoke_all_user_sessions(user_id).get("revoked_count", 0)


SessionModel.Manager = SessionManager


# ---------------------------------------------------------------------------
# Hook implementations registered with core BLL_Auth
# ---------------------------------------------------------------------------


def issue_session(
    *,
    user_id: str,
    model_registry: Any,
    session_key: Optional[str] = None,
    expiration_hours: int = 24,
    device_type: str = "web",
    grant_type: Optional[str] = None,
    pending_state: Optional[str] = None,
) -> str:
    """Persist a fresh ``SessionModel`` row and return its ``session_key``.

    Called by core ``UserManager.generate_jwt_token`` via the
    ``issue_session`` hook. The returned key becomes the JWT's ``jti`` claim
    so the verify path can later resolve the row and check revocation.

    A failure to persist (stale registry, ad-hoc tooling without a bound
    DB, etc.) still returns the generated key — the verify path will reject
    the token at first use because no matching row exists, which is the
    correct fail-closed behavior. The exception is logged via the framework
    logger before being swallowed.
    """
    if session_key is None:
        session_key = secrets.token_hex(16)
    if model_registry is None:
        return session_key
    now = datetime.now(timezone.utc)
    try:
        SessionModel.DB(model_registry.DB.manager.Base).create(
            requester_id=user_id,
            model_registry=model_registry,
            user_id=user_id,
            session_key=session_key,
            jwt_issued_at=now,
            device_type=device_type,
            browser="unknown",
            is_active=True,
            last_activity=now,
            expires_at=now + timedelta(hours=expiration_hours),
            revoked=False,
            trust_score=50,
            grant_type=grant_type,
            pending_state=pending_state,
        )
    except Exception:
        from serverframework.lib.Logging import logger

        logger.exception(
            "auth_session.issue_session: failed to persist session row; "
            "token will be rejected on first verify"
        )
    return session_key


def enforce_not_revoked(
    payload: Dict[str, Any],
    model_registry: Any,
    db: Any = None,
) -> None:
    """Raise 401 if the JWT's bound session is missing/inactive/revoked.

    Called by core ``UserManager.verify_token`` via the
    ``enforce_not_revoked`` hook. Without this hook (extension not
    loaded), JWT verification is purely stateless — the framework still
    requires ``jti`` so that the moment ``auth_session`` does load, every
    token already in the wild becomes revocable.
    """
    session_key = payload.get("jti") if isinstance(payload, dict) else None
    if not session_key:
        raise HTTPException(
            status_code=401,
            detail="Token missing required `jti`; reauthenticate.",
        )
    if model_registry is None and db is None:
        # No DB context to consult; allow the token. Callers running
        # outside a request context (test scaffolding, CLI tooling) hit
        # this path. Production verify paths always carry a registry.
        return
    db_manager = (
        model_registry.DB.manager if model_registry is not None else None
    )
    try:
        session_dto = SessionModel.DB(db_manager.Base).get(  # type: ignore[union-attr]
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            db=db,
            filters=[
                SessionModel.DB(db_manager.Base).session_key == session_key  # type: ignore[union-attr]
            ],
        )
    except Exception:
        session_dto = None
    if session_dto is None:
        raise HTTPException(
            status_code=401, detail="Session has been revoked"
        )
    is_active = (
        session_dto["is_active"]
        if isinstance(session_dto, dict)
        else getattr(session_dto, "is_active", False)
    )
    revoked = (
        session_dto["revoked"]
        if isinstance(session_dto, dict)
        else getattr(session_dto, "revoked", False)
    )
    if not is_active or revoked:
        raise HTTPException(
            status_code=401, detail="Session has been revoked"
        )
    pending_state = (
        session_dto["pending_state"]
        if isinstance(session_dto, dict)
        else getattr(session_dto, "pending_state", None)
    )
    if pending_state == "awaiting_approval":
        raise PendingSessionError()


def session_manager_factory(
    *,
    requester_id: str,
    target_id: Optional[str] = None,
    target_team_id: Optional[str] = None,
    model_registry: Optional[Any] = None,
) -> SessionManager:
    """Return a ``SessionManager`` bound to the given identities.

    Called by core ``UserManager.sessions`` via the
    ``manager_factory`` hook so the parent manager can expose
    ``user.sessions`` without importing ``SessionManager`` at the core
    layer.
    """
    return SessionManager(
        requester_id=requester_id,
        target_id=target_id,
        target_team_id=target_team_id,
        model_registry=model_registry,
    )


def revoke_user_sessions(
    *, user_id: str, requester_id: str, model_registry: Any
) -> int:
    """Revoke every active/non-revoked session for ``user_id``. Returns
    the count for observability."""
    if model_registry is None:
        return 0
    mgr = SessionManager(requester_id=requester_id, model_registry=model_registry)
    return mgr.revoke_sessions(user_id)


__all__ = [
    "SessionModel",
    "SessionManager",
    "issue_session",
    "enforce_not_revoked",
    "session_manager_factory",
    "revoke_user_sessions",
]


# Register the hooks at module-import time. The framework's loader resolves
# this module either directly (when ``auth_session`` is on ``APP_EXTENSIONS``)
# or lazily through the PEP 562 ``__getattr__`` shim in ``BLL_Auth`` when a
# caller imports a session symbol. Either path is sufficient — by the time
# any session-aware path runs, the hooks are wired. The ImportError guard
# is for the framework-bootstrap window where ``BLL_Auth`` hasn't finished
# importing yet.
try:
    from serverframework.logic.BLL_Auth import register_session_hooks

    register_session_hooks(
        issue_session=issue_session,
        enforce_not_revoked=enforce_not_revoked,
        manager_factory=session_manager_factory,
        revoke_user_sessions=revoke_user_sessions,
    )
except ImportError:
    pass
