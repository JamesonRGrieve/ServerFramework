# SPDX-License-Identifier: AGPL-3.0-or-later
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

import bcrypt
from fastapi import HTTPException, Header, Request, Response, status

from pydantic import Field, ValidationError, model_validator

from sqlalchemy import or_
from zephyrex.lib.CustomRoute import ExposeIn, custom_route
import jwt
from zephyrex.lib.Environment import env, extract_base_domain
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    LockoutPolicy,
    LockoutTracker,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.Preconditions import check_route_record, expect_route_record
from zephyrex.pydantic2.sqlalchemy.indexes import TableIndex
from zephyrex.pydantic2.fastapi import (
    AuthType,
    RequestInfo,
    RouteType,
    RouterMixin,
    static_route,
)
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ImageMixinModel,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
)
from zephyrex.logic.BLL_Auth._shared import (
    BaseModel,
    InvalidGrantError,
    PasswordlessGrantRegistry,
    _BCRYPT_ROUNDS,
    _DUMMY_BCRYPT_HASH,
    _api_key_hooks,
    _invitation_hooks,
    _lockout_hooks,
    _metadata_hooks,
    _session_hooks,
    mfa_login_methods,
    refuse_internal_account,
    run_login_checks,
    verify_mfa_login_code,
)
from zephyrex.lib.SessionCookies import clear_session_cookies, set_session_cookies
from zephyrex.logic.BLL_Auth.password_policy import (
    PASSWORD_POLICY,
    PasswordPolicy,
    enforce_password_policy,
)
from zephyrex.lib.SingleUseToken import (
    issue_single_use_token,
    read_single_use_token,
    redeem_single_use_token,
)

# A compact JWT is three base64url segments joined by two ``.`` separators.
_JWT_SEPARATOR_COUNT = 2

# Login tokens, and the session cookie that carries them, live this long.
JWT_LIFETIME_HOURS = 24
_SECONDS_PER_HOUR = 3600
# Clock skew tolerated between token issuer and verifier.
JWT_LEEWAY_SECONDS = 30


def issue_browser_session(response: Response, token: str) -> None:
    """Set the session cookies for a login token (see lib.SessionCookies)."""
    set_session_cookies(response, token, JWT_LIFETIME_HOURS * _SECONDS_PER_HOUR)


# Password login for a user with a second factor yields this challenge,
# redeemed with a code at POST /v1/user/authorize/mfa.
MFA_CHALLENGE_AUDIENCE = "zephyrex:auth:mfa_challenge"
MFA_CHALLENGE_TTL_SECONDS = 300

# User fields only root may change (see UserManager.update).
ACCOUNT_STATE_FIELDS = frozenset({"active", "mfa_count"})

# The database's guarantee of one live account per email.
USERS_EMAIL_UNIQUE_INDEX = "uq_users_email_live"


class UserModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ImageMixinModel.Optional,
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore", "populate_by_name": True}
    Manager: ClassVar[Type["UserManager"]] = None  # type: ignore[assignment]
    email: Optional[str] = Field(description="User's email address")
    username: Optional[str] = Field(description="User's username")
    display_name: Optional[str] = Field(description="User's display name")
    first_name: Optional[str] = Field(description="User's first name")
    last_name: Optional[str] = Field(description="User's last name")
    mfa_count: Optional[int] = Field(description="Number of MFA verifications required")
    active: Optional[bool] = Field(
        default=True, description="Whether the user is active"
    )
    timezone: Optional[str] = Field(description="User's timezone")
    language: Optional[str] = Field(description="User's language")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Core user accounts for authentication and identity management"
    )
    # One live account per email. The stored email is the normalized one
    # (UserManager._normalize_identifier), so the column itself is indexed;
    # migration users_email_unique_live creates it.
    table_indexes: ClassVar[Tuple[TableIndex, ...]] = (
        TableIndex(
            name=USERS_EMAIL_UNIQUE_INDEX,
            columns=("email",),
            unique=True,
            where="deleted_at IS NULL",
        ),
    )
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": env("ROOT_ID"),
            "email": f"root@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
        {
            "id": env("SYSTEM_ID"),
            "email": f"system@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
        {
            "id": env("TEMPLATE_ID"),
            "email": f"template@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
    ]

    # Who may read a user record is decided by the users rule in
    # StaticPermissions.generate_permission_filter: themselves, users they
    # share a live team with, ROOT and SYSTEM everyone.

    @classmethod
    def user_has_admin_access(
        cls, user_id, id, db, db_manager=None, model_registry=None
    ):
        """
        Check if user has admin access to a specific record.
        Admin access requires EDIT permission.

        Args:
            user_id: The ID of the user requesting access
            id: The ID of the record to check
            db: Database session
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if admin access is granted, False otherwise
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_user_id,
        )

        # Root has admin access to everything
        if is_root_id(user_id):
            return True

        # Get the record to check creator and deletion rules
        record = None
        if isinstance(id, str):
            record = db.query(cls.DB(Base)).filter(cls.DB(Base).id == id).first()
            if record is None:
                return False

        # Check if the record was created by ROOT_ID - only ROOT_ID can access
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "ROOT_ID"
        ):
            return is_root_id(user_id)  # Only ROOT_ID can access

        # Check if the record was created by TEMPLATE_ID - only system users can modify
        if hasattr(record, "created_by_user_id") and record.created_by_user_id == env(
            "TEMPLATE_ID"
        ):
            return is_root_id(user_id) or is_system_user_id(user_id)

        # For User model, only allow admin access to your own record
        if id == user_id:
            return True

        # Otherwise use permission system
        result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.EDIT)
        return result == PermissionResult.GRANTED

    @classmethod
    def user_has_all_access(cls, user_id, id, db, db_manager=None, model_registry=None):
        """
        Override user_has_all_access for User model to enforce specific rules for
        DELETE and SHARE permissions.

        Args:
            user_id: ID of the requesting user
            id: ID of the User record
            db: Database session
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if user has all access, False otherwise
        """
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
        )

        # ROOT_ID has all access
        if is_root_id(user_id):
            return True

        # Get the record
        user_record = None
        if isinstance(id, str):
            user_record = (
                db.query(cls.DB(db_manager.Base))
                .filter(cls.DB(db_manager.Base).id == id)
                .first()
            )
            if user_record is None:
                return False

        # Special checks for ROOT_ID created records
        if hasattr(
            user_record, "created_by_user_id"
        ) and user_record.created_by_user_id == env("ROOT_ID"):
            return is_root_id(user_id)

        # Check explicit permissions
        result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.SHARE)
        return result == PermissionResult.GRANTED

    # Add a get method to support dictionary-like access for tests
    def get(self, field_name, default=None):
        """Dictionary-like accessor for attributes"""
        return getattr(self, field_name, default)

    class Create(BaseModel, ImageMixinModel.Optional):
        email: str = Field(..., description="User's email address")
        username: Optional[str] = Field(None, description="User's username")
        display_name: Optional[str] = Field(None, description="User's display name")
        first_name: Optional[str] = Field(None, description="User's first name")
        last_name: Optional[str] = Field(None, description="User's last name")
        password: Optional[str] = Field(None, description="User's password")
        timezone: Optional[str] = Field(None, description="User's timezone")
        language: Optional[str] = Field(None, description="User's language")
        invitation_code: Optional[str] = Field(None, description="invitation code")

        @model_validator(mode="after")
        def validate_email(self):
            try:
                from email_validator import EmailNotValidError
                from email_validator import validate_email as _validate_email

                _validate_email(self.email, check_deliverability=False)
            except EmailNotValidError as exc:
                raise ValueError(f"Invalid email format: {exc}") from exc
            return self

        invitation_id: Optional[str] = Field(
            None,
            description="Invitation ID for direct email invite acceptance during registration (scenario 3)",
        )

    class Update(BaseModel, ImageMixinModel.Optional):
        email: Optional[str] = Field(None, description="User's email address")
        username: Optional[str] = Field(None, description="User's username")
        display_name: Optional[str] = Field(None, description="User's display name")
        first_name: Optional[str] = Field(None, description="User's first name")
        last_name: Optional[str] = Field(None, description="User's last name")
        mfa_count: Optional[int] = Field(
            None, description="Number of MFA verifications required"
        )
        active: Optional[bool] = Field(None, description="Whether the user is active")
        timezone: Optional[str] = Field(None, description="User's timezone")
        language: Optional[str] = Field(None, description="User's language")

        @model_validator(mode="after")
        def validate_email(self):
            if self.email is not None:
                try:
                    from email_validator import EmailNotValidError
                    from email_validator import validate_email as _validate_email

                    _validate_email(self.email, check_deliverability=False)
                except EmailNotValidError as exc:
                    raise ValueError(f"Invalid email format: {exc}") from exc
            return self

    class Search(ApplicationModel.Search, ImageMixinModel.Search):
        email: Optional[StringSearchModel] | None = None
        username: Optional[StringSearchModel] | None = None
        display_name: Optional[StringSearchModel] | None = None
        first_name: Optional[StringSearchModel] | None = None
        last_name: Optional[StringSearchModel] | None = None
        active: Optional[bool] | None = None
        timezone: Optional[str] | None = None
        language: Optional[str] | None = None


# The fields a registrant may send: UserModel.Create's, taken before any
# extension extends the model. Every other users column is the server's to
# write, and registration refuses it: account state only root may change
# (ACCOUNT_STATE_FIELDS) and the columns extensions add (the payment link).
REGISTRATION_FIELDS = frozenset(UserModel.Create.model_fields)


class UserManager(AbstractBLLManager, RouterMixin):
    _model = UserModel
    _entity_label: ClassVar[Optional[str]] = "User"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/user"
    tags: ClassVar[Optional[List[str]]] = ["User Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []
    route_auth_overrides: ClassVar[Dict[RouteType, AuthType]] = {}
    factory_params: ClassVar[List[str]] = ["target_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/authorize",
            "method": "post",
            "function": "login",
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "Login with credentials",
            "description": """
            Authenticates a user using their credentials and returns a JWT token.
            
            The endpoint accepts credentials via the Authorization header using Basic auth
            format (base64 encoded email:password) or through the request body.
            
            If successful, returns user information including teams and a JWT token
            for authentication in subsequent requests.
            """,
            "response_model": "Dict[str, Any]",
            "status_code": 200,
            "responses": {
                200: {
                    "description": "Authentication successful",
                    "content": {
                        "application/json": {
                            "example": {
                                "id": "u1s2e3r4-5678-90ab-cdef-123456789012",
                                "email": "user@example.com",
                                "first_name": "John",
                                "last_name": "Doe",
                                "display_name": "John Doe",
                                "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                                "teams": [
                                    {
                                        "id": "t1e2a3m4-5678-90ab-cdef-123456789012",
                                        "name": "Marketing Team",
                                        "description": "Team responsible for marketing activities",
                                        "role_id": "r1o2l3e4-5678-90ab-cdef-123456789012",
                                        "role_name": "admin",
                                    }
                                ],
                                "detail": "https://example.com?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                            }
                        }
                    },
                },
                401: {"description": "Invalid credentials"},
                429: {"description": "Too many failed login attempts"},
            },
        },
        {
            "path": "/authorize/mfa",
            "method": "post",
            "function": "login_mfa",
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "Complete an MFA login",
            "description": """
            For a user with a verified second factor, POST /authorize returns
            {mfa_required: true, challenge_token, methods} and no session.
            Posting {challenge_token, code} here, with a current TOTP or
            recovery code, returns the normal login response. The challenge
            expires after five minutes and is spent by a correct code.
            """,
            "response_model": "Dict[str, Any]",
            "status_code": 200,
            "responses": {
                401: {"description": "Invalid code or challenge"},
                429: {"description": "Too many verification attempts"},
            },
        },
        {
            "path": "/logout",
            "method": "post",
            "function": "logout",
            # The method authenticates the presented token itself: the
            # token, not a resolved requester, is what logout ends.
            "auth_type": AuthType.NONE,
            "is_static": True,
            "summary": "End the current session",
            "description": "Revokes the session the presented JWT belongs to.",
            "status_code": 204,
        },
        {
            "path": "",
            "method": "get",
            "function": "get_current_user",
            "summary": "Get current user",
            "description": "Retrieves the current user's profile based on JWT token.",
            "response_model": "UserModel.ResponseSingle",
            "status_code": 200,
        },
        {
            "path": "",
            "method": "put",
            "function": "update_current_user",
            "summary": "Update current user",
            "description": "Updates the current user's profile.",
            "response_model": "UserModel.ResponseSingle",
            "status_code": 200,
        },
        {
            "path": "",
            "method": "delete",
            "function": "delete",
            "summary": "Delete current user",
            "description": "Marks the current user based on JWT token as deleted. AKA self-deletion.",
            "status_code": 204,
        },
        {
            "path": "",
            "method": "patch",
            "function": "change_password",
            "summary": "Change user password",
            "description": "Changes the password for the current user account.",
            "response_model": "Dict[str, str]",
            "status_code": 200,
            "responses": {
                200: {
                    "description": "Password changed successfully",
                    "content": {
                        "application/json": {
                            "example": {"message": "Password changed successfully"}
                        }
                    },
                },
                401: {"description": "Current password is incorrect"},
            },
        },
        {
            "path": "/invitation",
            "method": "get",
            "function": "list_invitations_for_user",
            "summary": "Invitations awaiting the caller's answer",
            "description": "Pending, unexpired, unrevoked invitations addressed "
            "to the caller directly or by email, with team and role.",
            "response_model": "Dict[str, Any]",
            "status_code": 200,
        },
    ]
    nested_resources: ClassVar[Dict[str, Any]] = {
        "user_team": {
            "child_resource_name": "user_team",
            "manager_property": "user_teams",
            "child_manager_class": lambda: getattr(
                __import__("zephyrex.logic.BLL_Auth", fromlist=["UserTeamManager"]),
                "UserTeamManager",
            ),
            # Read-only: a membership is granted, changed and removed through
            # the team (invitations, PATCH/DELETE /v1/team/{t}/user/{u}),
            # where TeamAuthority decides; a member never edits their own.
            "routes_to_register": ["get", "list"],
        },
        "metadata": {
            "child_resource_name": "metadata",
            "manager_property": "metadata",
            "child_manager_class": lambda: getattr(
                __import__(
                    "zephyrex.extensions.metadata.BLL_Metadata",
                    fromlist=["UserMetadataManager"],
                ),
                "UserMetadataManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "session": {
            "child_resource_name": "session",
            "manager_property": "sessions",
            # Session manager is provided by the ``auth_session`` extension.
            # The lambda imports lazily through the PEP 562 ``__getattr__``
            # shim below so the framework never resolves this attribute at
            # import time — when the extension is not loaded the shim
            # raises a typed migration error pointing the operator at the
            # extension to enable.
            "child_manager_class": lambda: getattr(
                __import__("zephyrex.logic.BLL_Auth", fromlist=["SessionManager"]),
                "SessionManager",
            ),
            "routes_to_register": ["list", "get"],
            "custom_routes": [
                {
                    "path": "",
                    "method": "delete",
                    "function": "revoke_all_user_sessions",
                    "summary": "Revoke all user sessions",
                    "description": "Revokes all sessions for a user.",
                    "status_code": 204,
                }
            ],
        },
    }

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry=None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._credentials = None
        self._metadata = None
        self._mfa_methods = None
        self._failed_logins = None
        self._user_teams = None
        self._sessions = None

    def _register_search_transformers(self):
        self.register_search_transformer("name", self._transform_name_search)

    def _transform_name_search(self, value):
        if not value:
            return []

        if isinstance(value, dict):
            return None

        escaped = (
            str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        search_value = f"%{escaped}%"
        db_model = self.DB
        return [
            or_(
                db_model.first_name.ilike(search_value, escape="\\"),
                db_model.last_name.ilike(search_value, escape="\\"),
                db_model.display_name.ilike(search_value, escape="\\"),
                db_model.username.ilike(search_value, escape="\\"),
            )
        ]

    @property
    def credentials(self):
        if self._credentials is None:
            self._credentials = UserCredentialManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._credentials

    @property
    def metadata(self):
        if self._metadata is None:
            factory = _metadata_hooks["user_manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not loaded; user metadata unavailable",
                )
            self._metadata = factory(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
            # Factory returns None when MetadataModel isn't bound to *this*
            # registry (e.g. an extension fixture that loaded auth_mfa only).
            if self._metadata is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not bound to this registry",
                )
        return self._metadata

    @property
    def failed_logins(self):
        """Return the failed-login manager from the ``auth_lockout``
        extension. Without it, the property is genuinely unavailable —
        core only knows that login failures should be tracked, not how
        to durably record them."""
        if self._failed_logins is None:
            factory = _lockout_hooks["manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "auth_lockout extension not loaded; "
                        "failed-login records unavailable"
                    ),
                )
            self._failed_logins = factory(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._failed_logins

    @property
    def user_teams(self):
        if self._user_teams is None:
            from zephyrex.logic.BLL_Auth.user_team import UserTeamManager

            self._user_teams = UserTeamManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._user_teams

    @property
    def sessions(self):
        """Return the session manager from the ``auth_session`` extension.

        Without ``auth_session`` loaded, ``user.sessions`` is unavailable —
        a 503 surfaces so callers know to enable the extension. Core JWT
        issuance/verification still works (stateless) without the
        extension; only the per-user session surface requires it.
        """
        if self._sessions is None:
            factory = _session_hooks["manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "auth_session extension not loaded; "
                        "per-user session surface unavailable"
                    ),
                )
            self._sessions = factory(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._sessions

    def create(self, **kwargs):
        raise NotImplementedError(
            "Intentionally not implemented. Use the `register` method instead."
        )

    def update(self, id: str, **kwargs):
        """Update a user with optional metadata"""
        from zephyrex.database.StaticPermissions import is_root_id

        # Account state is administrative: a user who could set their own
        # ``active`` or ``mfa_count`` could re-enable a disabled account or
        # switch off their MFA requirement.
        administrative = sorted(ACCOUNT_STATE_FIELDS & kwargs.keys())
        if administrative and not is_root_id(self.requester.id):
            raise HTTPException(
                status_code=403,
                detail=f"Only root may set {', '.join(administrative)}",
            )

        # The stored email is the normalized one: login and registration
        # match it so, and the database holds it unique among live users.
        if kwargs.get("email"):
            kwargs["email"] = UserManager._normalize_identifier(kwargs["email"])
            UserDB = self.DB
            if UserDB.exists(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                filters=[
                    UserDB.email == kwargs["email"],
                    UserDB.id != id,
                    UserDB.deleted_at.is_(None),
                ],
            ):
                raise HTTPException(status_code=409, detail="Email already in use")

        # Extract metadata fields (non-model fields)
        metadata_fields = {}
        model_fields = {}

        # Get the model fields for comparison - use ModelRegistry if available to get extended model
        if self.model_registry and hasattr(self.model_registry, "get_extended_model"):
            extended_model = self.model_registry.get_extended_model(self.Model)
            if extended_model and hasattr(extended_model, "Update"):
                model_fields_set = set(extended_model.Update.__annotations__.keys())
            else:
                model_fields_set = set(self.Model.Update.__annotations__.keys())
        else:
            model_fields_set = set(self.Model.Update.__annotations__.keys())

        for key, value in kwargs.items():
            if key in model_fields_set:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # Update the user
        user = super().update(id, **model_fields)

        # Update metadata if provided
        if metadata_fields and user:
            existing_metadata = self.metadata.list(user_id=id)
            existing_metadata_dict = {item.key: item for item in existing_metadata}

            for key, value in metadata_fields.items():
                if key in existing_metadata_dict:
                    # Update existing metadata
                    self.metadata.update(
                        id=existing_metadata_dict[key].id,
                        value=str(value),
                    )
                else:
                    # Create new metadata
                    self.metadata.create(
                        user_id=id,
                        key=key,
                        value=str(value),
                    )

        return user

    @staticmethod
    def generate_jwt_token(
        user_id: str,
        email: str,
        timezone_str: str = "UTC",
        expiration_hours: int = JWT_LIFETIME_HOURS,
        session_key: Optional[str] | None = None,
        model_registry=None,
    ) -> str:
        """Generate a JWT token for authentication.

        Always carries ``jti`` (M-1) so token verification can enforce
        session revocation when the ``auth_session`` extension is loaded.

        When ``session_key`` is None and the ``auth_session`` extension is
        loaded, the registered ``issue_session`` hook persists a fresh
        ``SessionModel`` row tagged with the generated ``jti`` so the
        verify path can later check revocation/pending state. Without the
        extension the call falls back to a freshly-generated key but does
        not attempt persistence — JWT verification will still succeed on
        signature/exp/nbf/aud/iss/jti, but the token is not server-side
        revocable until ``auth_session`` is enabled.
        """
        now = datetime.now(timezone.utc)
        expiration = now + timedelta(hours=expiration_hours)
        # M-4 — emit ``nbf`` so the verify path can require it. Setting the
        # claim to ``now`` (with a small leeway on verify) prevents tokens
        # minted with a future ``nbf`` from sliding past the gate.
        not_before = now

        if session_key is None:
            issue_hook = _session_hooks["issue_session"]
            if issue_hook is not None:
                session_key = issue_hook(
                    user_id=user_id,
                    model_registry=model_registry,
                    expiration_hours=expiration_hours,
                    device_type="api",
                )
            else:
                session_key = secrets.token_hex(16)

        # M-1 — `aud` and `iss` so tokens minted by another deployment
        # sharing the same JWT_SECRET (dev↔staging accident) do not
        # cross-validate. Mandatory `jti` so revocation always engages.
        # M-4/M-5 — emit ``iat`` and ``nbf`` so the verify path can
        # require both; verify uses a small leeway to absorb skew.
        payload = {
            "sub": user_id,
            "email": email,
            "timezone": timezone_str,
            "exp": expiration,
            "iat": now,
            "nbf": not_before,
            "aud": env("JWT_AUDIENCE"),
            "iss": env("JWT_ISSUER"),
            "jti": session_key,
        }
        return jwt.encode(payload, env("JWT_SECRET"), algorithm=env("JWT_ALGORITHM"))

    @staticmethod
    def _enforce_session_not_revoked(
        payload: Dict[str, Any], model_registry, db=None
    ) -> None:
        """Always require a ``jti`` claim (M-1). When the ``auth_session``
        extension is loaded, dispatch to its ``enforce_not_revoked`` hook
        which checks the bound row's ``is_active``, ``revoked``, and
        ``pending_state``. Without the extension, a present-and-non-empty
        ``jti`` is sufficient — the framework still requires it so that
        adding ``auth_session`` later is fully effective for tokens
        already in the wild.
        """
        session_key = payload.get("jti") if isinstance(payload, dict) else None
        if not session_key:
            raise HTTPException(
                status_code=401,
                detail="Token missing required `jti`; reauthenticate.",
            )
        enforce_hook = _session_hooks["enforce_not_revoked"]
        if enforce_hook is None:
            return

        from zephyrex.logic.AbstractLogicManager import (
            _cache_sync_run,
            get_entity_cache,
        )

        cache = get_entity_cache()
        if cache is not None:
            try:
                cached = _cache_sync_run(
                    cache.get_by_field("session", "session_key", session_key)
                )
                if cached is not None:
                    return
            except Exception:
                pass

        enforce_hook(payload, model_registry, db)

        if cache is not None:
            try:
                _cache_sync_run(
                    cache.put(
                        "session",
                        session_key,
                        {"session_key": session_key, "valid": True},
                        {"session_key": session_key},
                    )
                )
            except Exception:
                pass

    @staticmethod
    def _decode_jwt(token: str) -> Dict[str, Any]:
        """Decode a JWT trying current secret, then previous for rotation."""

        def decode(secret: str) -> Dict[str, Any]:
            return jwt.decode(
                token,
                secret,
                algorithms=[env("JWT_ALGORITHM")],
                audience=env("JWT_AUDIENCE"),
                issuer=env("JWT_ISSUER"),
                leeway=JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "nbf", "iat", "jti", "aud", "iss"]},
            )

        try:
            return decode(env("JWT_SECRET"))
        except jwt.InvalidSignatureError:
            previous = env("JWT_SECRET_PREVIOUS")
            if previous:
                return decode(previous)
            raise

    @staticmethod
    def verify_token(
        token: str,
        model_registry=None,
    ) -> Dict[str, Any]:
        """Verify a JWT token and return user information"""
        if model_registry is None:
            raise ValueError("model_registry is required for verify_token")

        try:
            payload = UserManager._decode_jwt(token)

            UserManager._enforce_session_not_revoked(payload, model_registry)

            from zephyrex.logic.AbstractLogicManager import (
                _cache_sync_run,
                get_entity_cache,
            )

            cache = get_entity_cache()
            if cache is not None:
                try:
                    cached_user = _cache_sync_run(
                        cache.get_by_id("user", payload["sub"])
                    )
                    if cached_user is not None:
                        user = UserModel.model_validate(cached_user)
                        if not user.active:
                            raise HTTPException(status_code=401, detail="Inactive user")
                        return {"id": user.id, "email": user.email}
                except HTTPException:
                    raise
                except Exception:
                    pass

            # ROOT's reads include deleted rows; a deleted user's token is
            # void. (A soft delete through the manager evicts the cache.)
            UserDB = UserModel.DB(model_registry.DB.manager.Base)
            live: List[UserModel] = UserDB.list(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                filters=[UserDB.id == payload["sub"], UserDB.deleted_at.is_(None)],
                return_type="dto",
                override_dto=UserModel,
            )
            if len(live) != 1:
                raise HTTPException(status_code=401, detail="Invalid token")
            user = live[0]
            if not user.active:
                raise HTTPException(status_code=401, detail="Inactive user")

            if cache is not None:
                try:
                    dto_dict = (
                        user.model_dump(mode="json")
                        if hasattr(user, "model_dump")
                        else {"id": user.id, "email": user.email, "active": user.active}
                    )
                    _cache_sync_run(
                        cache.put(
                            "user",
                            payload["sub"],
                            dto_dict,
                            (
                                {"email": user.email}
                                if hasattr(user, "email") and user.email
                                else None
                            ),
                        )
                    )
                except Exception:
                    pass

            return {"id": user.id, "email": user.email}
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Token verification failed: %s", e, exc_info=True)
            raise HTTPException(status_code=401, detail="Token verification failed")

    @staticmethod
    def _decode_basic_auth(authorization: str) -> tuple[str, str]:
        """Decode a Basic ``Authorization`` header into ``(identifier, password)``.

        Strips the ``Basic `` scheme prefix, base64-decodes the credential
        payload, and splits it on the first ``:``. Raises a 401 with a single
        converged detail on any malformed input — bad base64, non-UTF-8 bytes,
        or a missing ``:`` separator. Decode only: it neither looks up the user
        nor verifies the password.
        """
        import base64

        encoded = authorization.replace("Basic ", "").strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except ValueError as exc:
            raise HTTPException(
                status_code=401, detail="Invalid Basic authentication header"
            ) from exc
        if ":" not in decoded:
            raise HTTPException(
                status_code=401, detail="Invalid Basic authentication header"
            )
        identifier, password = decoded.split(":", 1)
        return identifier, password

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """Normalize an email/username identifier for consistent matching.

        Applies NFKC Unicode normalization, lowercases, and strips surrounding
        whitespace. Used at both registration and login so a given user
        normalizes to the exact same stored/looked-up value on both paths.
        """
        import unicodedata

        return unicodedata.normalize("NFKC", identifier).lower().strip()

    @staticmethod
    def user_id_for_verified_email(
        email: Optional[str], model_registry: Any
    ) -> Optional[str]:
        """The one live account an external identity's vouched-for email
        names, for linking that identity on its first sign-in. The email is
        matched as registration stores it (``_normalize_identifier``).

        None without an email or without such an account; 403 when it is an
        internal account's (``refuse_internal_account``). The filter is
        uq_users_email_live's own predicate, so it matches at most one row.
        """
        if not email:
            return None
        UserDB = UserModel.DB(model_registry.DB.manager.Base)
        users: List[Dict[str, Any]] = UserDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[
                UserDB.email == UserManager._normalize_identifier(email),
                UserDB.deleted_at.is_(None),
            ],
        )
        if not users:
            return None
        refuse_internal_account(users[0]["id"])
        return str(users[0]["id"])

    @staticmethod
    def _resolve_issued_api_key(
        model_registry: Any, candidates: tuple[Optional[str], ...]
    ) -> Optional[str]:
        """Resolve the first issued API key among ``candidates`` to its user id.

        Dispatches through ``_api_key_hooks["resolve_principal"]`` (registered
        by the auth_api_keys extension); returns ``None`` when the extension is
        not loaded or no candidate is a live user-bound key. JWT-shaped values
        are skipped: an issued key is opaque (no ``.`` separators), so a JWT
        can never match and hashing it would cost a lookup on every request.
        """
        resolve_principal = _api_key_hooks["resolve_principal"]
        if resolve_principal is None:
            return None
        seen: set[str] = set()
        for candidate in candidates:
            if (
                not candidate
                or candidate in seen
                or candidate.count(".") >= _JWT_SEPARATOR_COUNT
            ):
                continue
            seen.add(candidate)
            user_id = resolve_principal(candidate, model_registry)
            if user_id is not None:
                return user_id
        return None

    @staticmethod
    def auth(
        model_registry,
        authorization: Optional[str] = Header(None),
        request: Request | RequestInfo | Dict[str, Any] | None = None,
    ) -> UserModel:
        """Authenticate a user from Authorization header"""
        if isinstance(request, dict):
            request = RequestInfo(request)
        # bypass auth for user registration
        if (
            request
            and str(request.url).endswith("/v1/user")
            and request.method == "POST"
        ):
            return None  # type: ignore[return-value]

        if not authorization:
            raise HTTPException(
                status_code=401, detail="Authorization header is missing!"
            )

        db_manager = model_registry.DB
        if db_manager is None:
            raise ValueError("db_manager is required for auth")

        db = db_manager.get_session()

        try:

            if authorization.startswith("Bearer"):
                # JWT Token authentication
                token = (
                    authorization.replace("Bearer ", "").replace("bearer ", "").strip()
                )

                # H-6 — API-key path uses the canonical resolver. X-API-Key
                # takes precedence; if the Bearer token itself is one of the
                # configured keys (legacy clients) honour that too. The
                # mapping is consistent with the REST factory and GraphQL
                # context — three transports, one decision.
                from zephyrex.lib.InboundSecurity import (
                    resolve_principal_from_api_key,
                )

                api_key_header = request.headers.get("X-API-Key") if request else None
                principal = resolve_principal_from_api_key(api_key_header)
                if not principal and token:
                    principal = resolve_principal_from_api_key(token)
                if principal:
                    return (  # type: ignore[no-any-return]
                        db.query(UserModel.DB(db_manager.Base))
                        .filter(UserModel.DB(db_manager.Base).id == principal)
                        .first()
                    )

                # Keys issued by the auth_api_keys extension, consulted only
                # after the env-configured keys. A matching issued key binds
                # the request to the key's owning user; an unknown, revoked,
                # expired, or team-only key falls through to the JWT path,
                # which rejects it as an invalid token.
                issued_key_user_id = UserManager._resolve_issued_api_key(
                    model_registry, (api_key_header, token)
                )
                if issued_key_user_id is not None:
                    KeyUserDB = UserModel.DB(db_manager.Base)
                    key_user: Optional[UserModel] = (
                        db.query(KeyUserDB)
                        .filter(
                            KeyUserDB.id == issued_key_user_id,
                            KeyUserDB.deleted_at.is_(None),
                        )
                        .first()
                    )
                    if key_user is None:
                        raise HTTPException(status_code=401, detail="Invalid API key")
                    if not key_user.active:
                        raise HTTPException(
                            status_code=403, detail="User account is disabled"
                        )
                    return key_user

                try:
                    payload = UserManager._decode_jwt(token)

                    # If the token carries a `jti`, the bound session must
                    # still be active. A revoked session invalidates every
                    # bearer token issued for it.
                    UserManager._enforce_session_not_revoked(
                        payload, model_registry, db=db
                    )

                    UserDB = UserModel.DB(db_manager.Base)
                    # A deleted account's still-unexpired tokens are void:
                    # the token is as invalid as one for no user at all.
                    user = (
                        db.query(UserDB)
                        .filter(
                            UserDB.id == payload["sub"], UserDB.deleted_at.is_(None)
                        )
                        .first()
                    )
                    if not user:
                        raise HTTPException(status_code=401, detail="Invalid token")

                    if not user.active:
                        raise HTTPException(
                            status_code=403, detail="User account is disabled"
                        )

                    return user  # type: ignore[no-any-return]
                except jwt.ExpiredSignatureError:
                    raise HTTPException(status_code=401, detail="Token has expired")
                except jwt.InvalidTokenError:
                    raise HTTPException(status_code=401, detail="Invalid token")

            else:
                # Per-request authentication accepts Bearer/JWT (and API keys)
                # only. Basic credentials are exchanged for a token at the login
                # endpoint (POST /v1/user/authorize -> UserManager.login); they
                # are not a valid per-request scheme on protected routes, so any
                # non-Bearer header is rejected here rather than re-implementing
                # a second password-verification path.
                raise HTTPException(
                    status_code=401,
                    detail="Unsupported authorization method; use a Bearer token",
                )
        finally:
            db.close()

    def verify_password(self, user_id: str, password: str) -> bool:
        """Verify a user's password"""
        credentials = UserCredentialModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester_id,
            model_registry=self.model_registry,
            user_id=user_id,
            filters=[
                UserCredentialModel.DB(
                    self.model_registry.DB.manager.Base
                ).password_changed_at
                == None
            ],
        )

        if not credentials or not credentials[0]["password_hash"]:
            return False

        try:
            return bcrypt.checkpw(
                password.encode(), credentials[0]["password_hash"].encode()
            )
        except Exception:
            return False

    def get_metadata(self) -> Dict[str, str]:
        """Get all metadata for the target user (via metadata extension)."""
        list_hook = _metadata_hooks["list_user_metadata"]
        if list_hook is None:
            return {}
        metadata_items = list_hook(self.target_user_id, self.model_registry)
        return {item.key: item.value for item in metadata_items}

    def revoke_all_user_sessions(self, user_id: str):
        """Revoke all sessions for a user (nested custom route method)"""
        return self.sessions.revoke_all_user_sessions(user_id=user_id)

    # H-8 — IP-keyed brute-force lockout. Per-user counting (the existing
    # FailedLoginAttempt-driven gate) is necessary but insufficient: an
    # attacker rotating across hundreds of usernames never trips it. This
    # tracker is process-local; multi-worker deployments wire a shared
    # backend via `LockoutTracker.set_backend(...)` (same swap as the
    # rate-limit counter).
    _lockout_tracker: ClassVar[LockoutTracker] = LockoutTracker(
        LockoutPolicy(failures_per_window=10, window_seconds=900, lockout_seconds=1800)
    )

    # Login-specific models (not part of the main entity model system)
    class UserLoginModel(BaseModel):
        email: str = Field(..., description="User's email or username")
        password: str = Field(..., description="User's password")

    @staticmethod
    def _complete_login(
        user: Dict[str, Any], model_registry: Any, response: Response
    ) -> Dict[str, Any]:
        """Issue the session and build the login response for a user whose
        credentials (and second factor, when they have one) are proven: the
        token in the body for API clients, and in the session cookies for
        browsers. Shared by password login, the MFA challenge step and every
        sign-in extension; an internal account is refused here (403), and so
        is anyone a loaded extension's login check refuses
        (``register_login_check``), before any session exists."""
        refuse_internal_account(user["id"])
        run_login_checks(str(user["id"]), model_registry)
        root_id = env("ROOT_ID")

        # Login successful — issue the session row first (when
        # ``auth_session`` is loaded) so its key becomes the JWT's
        # ``jti``. Revoking the session row then invalidates every
        # bearer token bound to it (see ``_enforce_session_not_revoked``).
        user_timezone = (
            user.get("timezone", "UTC")
            if isinstance(user, dict)
            else getattr(user, "timezone", "UTC")
        )
        issue_hook = _session_hooks["issue_session"]
        if issue_hook is not None:
            session_key = issue_hook(
                user_id=user["id"],
                model_registry=model_registry,
                # Login defaults to a 30-day session; 24h JWT exp is
                # carried by ``generate_jwt_token`` independently.
                expiration_hours=24 * 30,
                device_type="web",
            )
        else:
            session_key = secrets.token_hex(16)
        token = UserManager.generate_jwt_token(
            user_id=str(user["id"]),
            email=user["email"],
            timezone_str=user_timezone,
            session_key=session_key,
        )

        # Get user preferences via metadata extension hook (Scope #3).
        preferences: Dict[str, str] = {}
        list_prefs = _metadata_hooks["list_preferences"]
        if list_prefs is not None:
            try:
                preferences = list_prefs(user["id"], model_registry) or {}
            except Exception:
                pass

        # Get user teams with roles
        from zephyrex.logic.BLL_Auth.user_team import UserTeamModel
        from zephyrex.logic.BLL_Auth.team import TeamModel
        from zephyrex.logic.BLL_Auth.role import RoleModel

        user_teams = UserTeamModel.DB(model_registry.DB.manager.Base).list(
            requester_id=root_id,
            model_registry=model_registry,
            user_id=user["id"],
            enabled=True,
        )

        # E1 (#230) — batch-load the referenced teams and roles with a
        # single ``id.in_(...)`` query each and build id->row maps, rather
        # than firing TeamModel.get + RoleModel.get per membership (the old
        # 2N-query pattern on every successful login). Mirrors the batched
        # lookup already used by ``UserTeamManager.search``. Uses
        # ``requester_id=root_id`` for both queries, exactly as the
        # per-item gets did, so soft-delete/permission handling is
        # identical.
        TeamDB = TeamModel.DB(model_registry.DB.manager.Base)
        RoleDB = RoleModel.DB(model_registry.DB.manager.Base)
        team_ids = {ut["team_id"] for ut in user_teams if ut["team_id"]}
        role_ids = {ut["role_id"] for ut in user_teams if ut["role_id"]}

        team_map: Dict[str, Any] = {}
        role_map: Dict[str, Any] = {}
        if team_ids:
            team_map = {
                team["id"]: team
                for team in TeamDB.list(
                    requester_id=root_id,
                    model_registry=model_registry,
                    filters=[TeamDB.id.in_(team_ids)],
                )
            }
        if role_ids:
            role_map = {
                role["id"]: role
                for role in RoleDB.list(
                    requester_id=root_id,
                    model_registry=model_registry,
                    filters=[RoleDB.id.in_(role_ids)],
                )
            }

        teams_with_roles = []
        for user_team in user_teams:
            team = team_map[user_team["team_id"]]
            role = role_map[user_team["role_id"]]

            # Ensure the key is serializable
            if isinstance(user_team["expires_at"], datetime):
                user_team["expires_at"] = user_team["expires_at"].isoformat()
            if isinstance(user_team["created_at"], datetime):
                user_team["created_at"] = user_team["created_at"].isoformat()
            if isinstance(user_team["updated_at"], datetime):
                user_team["updated_at"] = user_team["updated_at"].isoformat()

            teams_with_roles.append(
                {
                    "team_id": user_team["team_id"],
                    "user_team_id": user_team["id"],
                    "team_name": team["name"],
                    "role_id": user_team["role_id"],
                    "role_name": role["name"],
                    "user_team": user_team,
                    "role": role,
                    "team": team,
                }
            )

        result = {
            "user": user,
            "token": token,
            "preferences": preferences,
            "teams": teams_with_roles,
            "session_key": session_key,
        }

        issue_browser_session(response, token)
        return result

    @staticmethod
    def logout(
        authorization: Optional[str], model_registry: Any, response: Response
    ) -> None:
        """Revoke the session the presented JWT belongs to (from the header
        or, for a browser, the session cookie) and clear the session cookies.

        The token is authenticated first, so a missing, invalid, expired or
        already-revoked token is a 401. Without the auth_session extension
        tokens are stateless and expire on their own; there is nothing
        server-side to revoke."""
        UserManager.auth(model_registry=model_registry, authorization=authorization)
        token = (authorization or "").removeprefix("Bearer ").strip()
        try:
            session_key = UserManager._decode_jwt(token)["jti"]
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=400, detail="Logout ends a JWT session, not an API key"
            )
        revoke = _session_hooks["revoke_session_key"]
        if revoke is not None:
            revoke(session_key=session_key, model_registry=model_registry)
        clear_session_cookies(response)

    @staticmethod
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def login(
        login_data: Dict[str, Any] | None = None,
        ip_address: str | None = None,
        req_uri: Optional[str] | None = None,
        authorization: Optional[str] | None = None,
        model_registry=None,
        *,
        response: Response,
    ) -> Dict[str, Any]:
        """Process user login from various input methods.

        Decorated with ``@rate_limit("10/min", scope="ip")`` (Item 71b) so
        login is throttled per source IP. The decorator stamps metadata
        consumed by the FastAPI middleware that turns burst floods into
        429 responses.

        Brute-force lockout (Item 71c) is enforced inline below via
        ``UserManager._lockout_tracker``: 5 failures within a 15-minute
        sliding window lock the actor for 30 minutes. Lockout state is
        persisted in the ``auth_lockout`` table so it survives restarts.
        """
        if model_registry is None:
            raise ValueError("model_registry is required for login")

        # Extract credentials from Basic Auth header if provided
        if authorization and authorization.startswith("Basic "):
            identifier, password = UserManager._decode_basic_auth(authorization)
            login_data = {"email": identifier, "password": password}

        if not login_data:
            raise HTTPException(status_code=400, detail="Invalid Authorization header.")

        # H-8 — IP-keyed lockout check before any DB work. An attacker
        # rotating usernames against a single IP trips this even if no
        # individual user account is locked.
        lockout_key = ip_address or "unknown"
        if UserManager._lockout_tracker.is_locked(lockout_key, "password_login"):
            remaining = UserManager._lockout_tracker.remaining_lockout_seconds(
                lockout_key, "password_login"
            )
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Try again later.",
                headers={"Retry-After": str(int(remaining or 60))},
            )

        login_model = UserManager.UserLoginModel(**login_data)
        normalized_identifier = UserManager._normalize_identifier(login_model.email)

        # Try to find user by email or username
        user = UserModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[
                or_(
                    UserModel.DB(model_registry.DB.manager.Base).email
                    == normalized_identifier,
                    UserModel.DB(model_registry.DB.manager.Base).username
                    == normalized_identifier,
                )
            ],
        )
        if len(user) != 1:
            logger.warning("This should never have multiple users!")
            UserManager._lockout_tracker.record_failure(lockout_key, "password_login")
            # Burn the same bcrypt time as a real password check to
            # prevent timing-based username enumeration.
            bcrypt.checkpw((login_model.password or "x").encode(), _DUMMY_BCRYPT_HASH)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user = user[0]

        # Per-user threshold gate (auth_lockout extension when loaded).
        # The IP-keyed in-memory lockout above is the always-on defense.
        if _lockout_hooks["assert_within_threshold"] is not None:
            _lockout_hooks["assert_within_threshold"](user["id"], model_registry)

        # Check if user account is active
        if not user["active"]:
            if _lockout_hooks["record_failure"] is not None:
                _lockout_hooks["record_failure"](user["id"], ip_address, model_registry)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Check if user account was deleted
        if user["deleted_at"]:
            if _lockout_hooks["record_failure"] is not None:
                _lockout_hooks["record_failure"](user["id"], ip_address, model_registry)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Handle password-based login
        if login_model.password:
            credential = UserCredentialModel.DB(model_registry.DB.manager.Base).get(
                requester_id=user["id"],
                model_registry=model_registry,
                user_id=user["id"],
                filters=[
                    UserCredentialModel.DB(
                        model_registry.DB.manager.Base
                    ).password_changed_at
                    == None,
                ],
            )

            if not bcrypt.checkpw(
                login_model.password.encode(), credential["password_hash"].encode()
            ):
                # Check if there is an older password that matches
                Credential = UserCredentialModel.DB(model_registry.DB.manager.Base)
                with model_registry.DB.manager._get_db_session(
                    auto_commit=False
                ) as session:
                    old_credentials = (
                        session.query(Credential)
                        .filter(
                            Credential.user_id == user["id"],
                            Credential.password_changed_at != None,
                        )
                        .order_by(Credential.password_changed_at.desc())
                        .first()
                    )

                if old_credentials and bcrypt.checkpw(
                    login_model.password.encode(),
                    old_credentials.password_hash.encode(),
                ):
                    logger.info(
                        "Login attempt used a previously valid password "
                        "(changed %s)",
                        old_credentials.password_changed_at.strftime("%Y-%m"),
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid credentials",
                    )
                else:
                    if _lockout_hooks["record_failure"] is not None:
                        _lockout_hooks["record_failure"](
                            user["id"], ip_address, model_registry
                        )
                    # H-8 — record IP-keyed failure too.
                    UserManager._lockout_tracker.record_failure(
                        lockout_key, "password_login"
                    )
                    raise HTTPException(status_code=401, detail="Invalid credentials")

        else:
            raise HTTPException(
                status_code=400, detail="Either password or token is required"
            )

        # H-8 — successful auth clears the IP-keyed counter so a user
        # who misremembered their password once does not carry the
        # failure into the next legitimate attempt.
        UserManager._lockout_tracker.clear(lockout_key, "password_login")

        challenge = UserManager.mfa_challenge(str(user["id"]), model_registry)
        if challenge is not None:
            return challenge
        return UserManager._complete_login(user, model_registry, response)

    @staticmethod
    def mfa_challenge(user_id: str, model_registry: Any) -> Optional[Dict[str, Any]]:
        """For a user with a verified second factor, the challenge any first
        factor (password, magic link) yields instead of a session:
        ``{mfa_required, challenge_token, methods}``, redeemed at POST
        /v1/user/authorize/mfa (or at the route of the extension whose
        method the user picks, such as /v1/webauthn/mfa). ``methods`` is
        every registered source's (see ``register_mfa_source``). None for a
        user without one."""
        methods = mfa_login_methods(user_id, model_registry)
        if not methods:
            return None
        return {
            "mfa_required": True,
            "challenge_token": issue_single_use_token(
                audience=MFA_CHALLENGE_AUDIENCE,
                subject=user_id,
                ttl_seconds=MFA_CHALLENGE_TTL_SECONDS,
            ),
            "methods": methods,
        }

    @staticmethod
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def login_mfa(
        body: Dict[str, Any], model_registry: Any, response: Response
    ) -> Dict[str, Any]:
        """Second step of an MFA login: exchange the challenge issued by
        POST /authorize and a current TOTP or recovery code for the normal
        login response. A wrong code leaves the challenge usable until it
        expires (attempts are bounded by the MFA verification lockout); a
        right one spends it. The code may be any code-based source's."""
        try:
            claims = read_single_use_token(
                str(body.get("challenge_token") or ""),
                audience=MFA_CHALLENGE_AUDIENCE,
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=401, detail="Invalid or expired MFA challenge"
            )
        user_id = str(claims["sub"])
        code = str(body.get("code") or "")
        if not code or not verify_mfa_login_code(user_id, code, model_registry):
            raise HTTPException(status_code=401, detail="Invalid MFA code")
        if not redeem_single_use_token(claims):
            raise HTTPException(status_code=401, detail="MFA challenge already used")
        users = UserModel.DB(model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=user_id,
        )
        # The account may have been disabled since the password step.
        if len(users) != 1 or not users[0]["active"] or users[0]["deleted_at"]:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return UserManager._complete_login(users[0], model_registry, response)

    @staticmethod
    def _issue_session(
        user: Any,
        model_registry,
        grant_type: Optional[str] | None = None,
        pending_state: Optional[str] | None = None,
    ) -> Any:
        """Persist a fresh session row (when ``auth_session`` is loaded)
        and return a ``SessionModel`` describing it.

        Shared by passwordless grant flows (``login_via_grant``). When
        the extension is not loaded, a typed ``HTTPException(503)``
        surfaces — passwordless grant validators are extension-side and
        cannot meaningfully run without ``auth_session``. An internal
        account is refused (403), whatever the grant says, and so is anyone
        a loaded extension's login check refuses.
        """
        user_id = user.id if hasattr(user, "id") else user["id"]
        refuse_internal_account(user_id)
        run_login_checks(str(user_id), model_registry)
        issue_hook = _session_hooks["issue_session"]
        if issue_hook is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "auth_session extension not loaded; passwordless grant "
                    "flows require persisted sessions"
                ),
            )
        session_key = issue_hook(
            user_id=user_id,
            model_registry=model_registry,
            expiration_hours=24 * 30,
            device_type="web",
            grant_type=grant_type,
            pending_state=pending_state,
        )
        # Resolve the SessionModel class lazily through the PEP 562 shim so
        # this static method does not import the extension at module load.
        from zephyrex.logic import BLL_Auth as _self_mod

        SessionModel = _self_mod.SessionModel
        now = datetime.now(timezone.utc)
        return SessionModel(
            id=None,
            user_id=user_id,
            session_key=session_key,
            jwt_issued_at=now,
            is_active=True,
            last_activity=now,
            expires_at=now + timedelta(days=30),
            revoked=False,
            trust_score=50,
            requires_verification=False,
            grant_type=grant_type,
            pending_state=pending_state,
        )

    @staticmethod
    def session_token(session: Any, model_registry: Any) -> str:
        """The JWT a client presents for an issued session: ``jti`` is the
        session's key, so revoking the session revokes the token. Grant
        flows (magic link, device pairing) hand this to the client."""
        user = UserModel.DB(model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=session.user_id,
            return_type="dto",
            override_dto=UserModel,
        )
        return UserManager.generate_jwt_token(
            user_id=user.id,
            email=user.email,
            timezone_str=user.timezone or "UTC",
            session_key=session.session_key,
        )

    @staticmethod
    def login_via_grant(
        grant_type: str, grant_payload: BaseModel, model_registry=None
    ) -> Any:
        """Dispatch a passwordless grant to its registered validator and
        issue a fresh session bound to the resolved user.

        Extensions register validators via
        ``PasswordlessGrantRegistry.register``. Validator raises any
        domain-specific failure; we wrap the missing-grant case as
        ``InvalidGrantError`` for a typed 401.
        """
        if model_registry is None:
            raise ValueError("model_registry is required for login_via_grant")
        try:
            validator = PasswordlessGrantRegistry.get(grant_type)
        except KeyError as exc:
            raise InvalidGrantError(detail=str(exc))
        user = validator(grant_payload)
        if user is None:
            raise InvalidGrantError(detail="Grant validator returned no user")
        return UserManager._issue_session(
            user=user, model_registry=model_registry, grant_type=grant_type
        )

    def list(self, *args: Any, team_id: Optional[str] = None, **kwargs: Any) -> Any:
        """List users; with ``team_id``, the live members of that team.

        Users have no team column. A team's members are the users with an
        enabled, unexpired, undeleted membership in it, and only for a
        requester who could see them through it: one with a live membership
        in that team or in one of its sub-teams (as the user visibility rule
        reaches up), or ROOT and SYSTEM. Anyone else gets no one, so the
        filter never says who belongs to a team the requester is not in.
        """
        if team_id is not None:
            from zephyrex.database.StaticPermissions import live_team_members_filter

            kwargs["filters"] = [
                *(kwargs.get("filters") or []),
                live_team_members_filter(
                    self.requester.id,
                    str(team_id),
                    self.DB,
                    self.model_registry.DB.manager.Base,
                ),
            ]
        return super().list(*args, **kwargs)

    def get_current_user(self, fields: Optional[List[str]] | None = None):
        """Get the current user's profile."""
        user = self.get(id=self.requester.id, fields=fields)
        if hasattr(user, "model_dump"):
            return user.model_dump()
        return user

    def update_current_user(self, body: Dict[str, Any]):
        """Update the current user's profile. The path names no record, so
        the request's If-Match is bound to the requester's own row here."""
        user_data = dict(body.get("user", {}))
        # The caller is the target; identity and audit fields in the body are
        # never theirs to set (and ``id`` would collide with the target id).
        self._strip_server_controlled_fields(user_data)
        with expect_route_record(self, self.requester.id):
            updated_user = self.update(id=self.requester.id, **user_data)
        if hasattr(updated_user, "model_dump"):
            return updated_user.model_dump()
        return updated_user

    def delete(self, id: str | None = None):
        """Override delete to handle special self-deletion logic."""
        target_id = id or self.requester.id

        if target_id == self.requester.id:
            current_model = self.Model.DB(self.model_registry.DB.manager.Base)
            deleted_user = current_model.delete(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                filters=[
                    current_model.id == self.requester.id,
                    current_model.deleted_at == None,
                ],
            )
            return deleted_user
        else:
            raise NotImplementedError(
                "Intentionally not implemented. User cannot delete other users."
            )

    def change_password(self, body: Dict[str, Any]):
        """Change the current user's password. The change is the account's
        but writes its credentials, so the request's If-Match is checked
        against the requester's own users row before anything is written."""
        current_password = body.get("current_password")
        new_password = body.get("new_password")
        check_route_record(self, self.requester.id)
        return self.credentials.change_password(
            user_id=self.requester.id,
            current_password=current_password,
            new_password=new_password,
        )

    @custom_route(
        method="GET",
        path="/password-policy",
        output_model=PasswordPolicy,
        authentication_type="none",
        # Read before a session exists, over REST; GraphQL keeps password
        # types out of its schema.
        expose_in=(ExposeIn.REST,),
        summary="The password rule register and change-password enforce",
    )
    def password_policy_route(self) -> PasswordPolicy:
        """Public, so a registration form can check before submitting."""
        return PASSWORD_POLICY

    @staticmethod
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    @static_route("", method="POST", auth_type=AuthType.NONE, status_code=201)
    def register(
        registration_data: dict,
        model_registry,
        request: Request | None = None,
        authorization: Optional[str] | None = None,
    ) -> dict:
        """
        Register a new user with the provided data.
        Handles validation, creation, metadata, credentials, and invitation acceptance.
        Accepts either email+password in body OR Basic Auth header (mutually exclusive).
        """
        if model_registry is None:
            raise ValueError("model_registry is required for register")

        # Strip server-controlled audit/identity fields via the base SSOT so a
        # registering client cannot spoof their `id`, `created_by_user_id`, or
        # audit timestamps — the same set create()/update() enforce. Adding a
        # field to AbstractBLLManager._SERVER_CONTROLLED_AUDIT_FIELDS now covers
        # register too, instead of leaving this stale copy behind.
        if isinstance(registration_data, dict):
            UserManager._strip_server_controlled_fields(registration_data)

        # Check registration mode
        from zephyrex.lib.Environment import settings

        if settings.REGISTRATION_MODE == "closed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User registration is currently closed",
            )
        elif settings.REGISTRATION_MODE == "invite":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User registration requires an invitation",
            )

        # Enhanced JSON validation
        # Check if registration_data is None (happens when JSON parsing fails completely)
        if registration_data is None:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON syntax in request body - no data received",
            )

        # Check if registration_data is not a dict (malformed JSON might parse to other types)
        if not isinstance(registration_data, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON format - expected object, got {type(registration_data).__name__}",
            )

        # Check for completely empty dict - this often indicates JSON parsing failure
        # FastAPI converts malformed JSON to empty dict in many cases
        if len(registration_data) == 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON syntax in request body - empty object received",
            )

        # Check if we have any data that looks like it came from malformed JSON
        suspicious_patterns = [
            # Look for keys that don't look like normal field names
            any(not isinstance(key, str) for key in registration_data.keys()),
            # Look for values that might indicate parsing errors
            any(
                isinstance(value, str) and len(value) > 1000
                for value in registration_data.values()
            ),
            # Look for completely non-sensical data
            any(key.startswith("__") for key in registration_data.keys()),
        ]

        if any(suspicious_patterns):
            raise HTTPException(
                status_code=400,
                detail="Invalid JSON syntax in request body - malformed data detected",
            )

        root_id = env("ROOT_ID")

        # Handle Basic Auth header if provided
        email_from_header = None
        password_from_header = None
        if authorization and authorization.startswith("Basic "):
            email_from_header, password_from_header = UserManager._decode_basic_auth(
                authorization
            )

        # Extract fields from body
        email_from_body = registration_data.get("email")
        password_from_body = registration_data.get("password")

        # Validate mutual exclusivity
        if (email_from_body or password_from_body) and (
            email_from_header or password_from_header
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot provide credentials in both body and Authorization header. Use one method only.",
            )

        # Use credentials from appropriate source
        if email_from_header and password_from_header:
            email = email_from_header
            password = password_from_header
            # Remove email/password from registration_data if they exist
            registration_data.pop("email", None)
            registration_data.pop("password", None)
            # Add email to registration_data for user creation
            registration_data["email"] = email
        else:
            email = email_from_body  # type: ignore[assignment]
            password = password_from_body  # type: ignore[assignment]

        if email:
            email = UserManager._normalize_identifier(email)
            registration_data["email"] = email

        # Extract invitation fields
        invitation_code = registration_data.pop("invitation_code", None)
        invitation_id = registration_data.pop("invitation_id", None)
        invitation_details = None

        # Create a temporary entity for validation
        temp_entity_data = {
            k: v for k, v in registration_data.items() if k != "password"
        }
        try:
            temp_entity = UserModel.Create(**temp_entity_data)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail={"message": "Validation error", "details": e.errors()},
            )

        # Validation - check if email already exists
        if UserModel.DB(model_registry.DB.manager.Base).exists(
            requester_id=root_id,
            model_registry=model_registry,
            email=temp_entity.email,
        ):
            raise HTTPException(status_code=409, detail="Email already in use")

        if not email or not password:
            raise HTTPException(
                status_code=422, detail="Email and password are required."
            )
        # Before the user row exists: a refused password must not leave an
        # account with no credential behind.
        enforce_password_policy(password)

        # Validation - check if username already exists (if provided)
        if temp_entity.username and UserModel.DB(model_registry.DB.manager.Base).exists(
            requester_id=root_id,
            model_registry=model_registry,
            username=temp_entity.username,
        ):
            raise HTTPException(status_code=409, detail="Username already in use")

        # Handle invitation acceptance scenarios via the auth_invitations
        # extension hooks. When the extension is not loaded, both branches
        # silently fall through with `invitation_details = None`.
        if invitation_id:
            lookup = _invitation_hooks["lookup_by_id"]
            if lookup is None:
                logger.debug(
                    "Invitation ID supplied but auth_invitations extension is not "
                    "loaded; skipping invitation handling."
                )
                invitation_details = None
            else:
                try:
                    invitation_details = lookup(invitation_id, model_registry)
                    if invitation_details is None:
                        logger.warning(
                            f"Invalid or expired invitation ID during user "
                            f"registration: {invitation_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"Error validating invitation ID during user registration: {str(e)}"
                    )
                    invitation_details = None
        elif invitation_code:
            lookup = _invitation_hooks["lookup_by_code"]
            if lookup is None:
                logger.debug(
                    "Invitation code supplied but auth_invitations extension is "
                    "not loaded; skipping invitation handling."
                )
                invitation_details = None
            else:
                try:
                    invitation_details = lookup(invitation_code, model_registry)
                    if invitation_details is None:
                        logger.warning(
                            f"Invalid or expired invitation code during user "
                            f"registration: {invitation_code}"
                        )
                except Exception as e:
                    logger.error(
                        f"Error validating invitation code during user registration: {str(e)}"
                    )
                    invitation_details = None

        # Separate model fields from metadata fields
        metadata_fields = {}
        model_fields = {}

        # A users column outside REGISTRATION_FIELDS (active, mfa_count, the
        # payment customer link) is the server's to write, never the
        # registrant's: refused here, so it reaches neither the row nor, as a
        # look-alike, the metadata.
        user_columns = UserModel.DB(model_registry.DB.manager.Base).__table__.columns
        server_set = sorted(
            key
            for key in registration_data
            if key in user_columns and key not in REGISTRATION_FIELDS
        )
        if server_set:
            raise HTTPException(
                status_code=422,
                detail=f"Not settable at registration: {server_set}",
            )

        for key, value in registration_data.items():
            if key in REGISTRATION_FIELDS:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # M-4: Reject unknown fields unless the deployment explicitly opts in
        # to storing arbitrary registration metadata via
        # ACCEPT_REGISTRATION_METADATA=true. Fields starting with '_' are
        # internal markers (e.g. _test_password) and are silently dropped.
        metadata_fields = {
            k: v for k, v in metadata_fields.items() if not k.startswith("_")
        }
        if metadata_fields and env("ACCEPT_REGISTRATION_METADATA").lower() != "true":
            raise HTTPException(
                status_code=422,
                detail=f"Unknown fields: {list(metadata_fields.keys())}",
            )

        # Remove processed fields from model_fields
        model_fields.pop("password", None)
        model_fields.pop("invitation_code", None)
        model_fields.pop("invitation_id", None)

        # Debug logging
        logger.debug(f"UserManager.register: invitation_details = {invitation_details}")
        if invitation_details:
            logger.debug(
                f"UserManager.register: Processing invitation with team_id={invitation_details.get('team_id')}, role_id={invitation_details.get('role_id')}"
            )

        # Create the user
        user = UserModel.DB(model_registry.DB.manager.Base).create(
            requester_id=root_id,
            model_registry=model_registry,
            override_dto=model_registry.apply(UserModel),
            return_type="dto",
            **model_fields,
        )

        # Create metadata if provided (via metadata extension hook).
        if metadata_fields and user:
            create_meta = _metadata_hooks["create_user_metadata"]
            if create_meta is None:
                logger.warning(
                    "Registration metadata fields provided but `metadata` "
                    "extension is not loaded; skipping persistence of: %s",
                    list(metadata_fields.keys()),
                )
            else:
                for key, value in metadata_fields.items():
                    create_meta(
                        user.id,
                        key,
                        value,
                        model_registry,
                        requester_id=root_id,
                    )

        # Create credentials for the user. The user owns and self-creates their
        # own credential row so that the strict permission filter (which hides
        # ROOT-created records from non-ROOT viewers) does not block the user
        # from listing/editing their own credential during password change.
        credentials_manager = UserCredentialManager(
            requester_id=user.id,
            target_id=user.id,
            model_registry=model_registry,
        )
        credentials_manager.create(user_id=user.id, password=password)

        # Handle invitation acceptance via the auth_invitations extension
        # (Scope #4). The `apply_to_user` hook runs the entire invitee +
        # user-team sync — core no longer reaches into Invitation/Invitee.
        if invitation_details:
            apply_invitation = _invitation_hooks["apply_to_user"]
            if apply_invitation is None:
                logger.debug(
                    "invitation_details present but auth_invitations extension is "
                    "not loaded; skipping team-membership reconciliation."
                )
            else:
                try:
                    apply_invitation(invitation_details, user.id, model_registry)
                except Exception as e:
                    logger.error(
                        f"auth_invitations.apply_to_user failed for user "
                        f"{user.id}: {e}",
                        exc_info=True,
                    )

                # Mirror invitation provenance into user metadata when both
                # extensions are loaded.
                create_meta = _metadata_hooks["create_user_metadata"]
                if create_meta is not None:
                    create_meta(
                        user.id,
                        "invitation_accepted",
                        "true",
                        model_registry,
                        requester_id=root_id,
                    )
                    if invitation_details.get("code"):
                        create_meta(
                            user.id,
                            "invitation_code",
                            invitation_details["code"],
                            model_registry,
                            requester_id=root_id,
                        )
                    if invitation_details.get("team_id"):
                        create_meta(
                            user.id,
                            "invitation_team_id",
                            str(invitation_details["team_id"]),
                            model_registry,
                            requester_id=root_id,
                        )

                logger.debug(
                    f"User {user.id} successfully accepted invitation "
                    f"{invitation_details.get('code')} during registration"
                )

        return user  # type: ignore[no-any-return]

    def list_invitations_for_user(self) -> Dict[str, List[Dict[str, Any]]]:
        """Invitations awaiting the caller's answer (see the auth_invitations
        ``pending_invitations_for_user`` hook); none without the extension.
        Direct invitations also carry the caller as ``user``."""
        pending_for = _invitation_hooks["pending_invitations_for_user"]
        if pending_for is None:
            return {"invitations": []}
        user = self.get(id=self.requester.id)
        invitations = pending_for(user.id, user.email, self.model_registry)
        for invitation in invitations:
            if invitation["user_id"] == user.id:
                invitation["user"] = user
        return {"invitations": invitations}


class UserCredentialModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserCredentialManager"]] = None  # type: ignore[assignment]
    password_hash: Optional[str] = Field(None, description="Hashed password")
    password_salt: Optional[str] = Field(
        None, description="Salt used for hashing the password"
    )
    password_changed_at: Optional[datetime] = Field(
        None, description="When password was changed; null indicates current password"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Stores user password hashes and tracks password change history"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        password_hash: Optional[str]

    class CreateRaw(BaseModel, UserModel.Reference.ID):
        password: str = Field(None, description="New password (will be hashed)")  # type: ignore[assignment]

    class Update(BaseModel):
        # This model and entity should not be manually updatable, only via the User password change function.
        # However, we need to allow updating the password_changed_at field for tests
        password_changed_at: Optional[datetime] | None = None

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        password_changed_at: Optional[DateSearchModel] | None = None


class UserCredentialManager(AbstractBLLManager, RouterMixin):
    _model = UserCredentialModel

    def create(self, **kwargs):
        """Create new user credentials (password)"""
        enforce_password_policy(kwargs.get("password"))
        UserCredentialModel.DB(self.model_registry.DB.manager.Base).update(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            filters=[
                UserCredentialModel.DB(self.model_registry.DB.manager.Base).user_id
                == kwargs.get("user_id"),
                UserCredentialModel.DB(
                    self.model_registry.DB.manager.Base
                ).password_changed_at
                == None,
                UserCredentialModel.DB(self.model_registry.DB.manager.Base).deleted_at
                == None,
                UserCredentialModel.DB(
                    self.model_registry.DB.manager.Base
                ).created_by_user_id
                == kwargs.get("user_id"),
            ],
            new_properties={"password_changed_at": datetime.now(timezone.utc)},
            allow_nonexistent=True,  # Skip if no previous password exists
        )
        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        password_hash = bcrypt.hashpw(kwargs.pop("password").encode(), salt).decode()

        return super().create(
            password_hash=password_hash, password_salt=salt.decode(), **kwargs
        )

    def update(self, id: str, **kwargs):
        """Update user credentials (password)"""
        if "password" in kwargs:
            enforce_password_policy(kwargs["password"])
            # Get the credential we're updating
            credential = UserCredentialModel.DB(
                self.model_registry.DB.manager.Base
            ).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=id,
            )

            # If this is the current password (password_changed_at is None)
            if credential.password_changed_at is None:
                # Create a new credential record instead of updating
                return self.create(
                    user_id=credential.user_id, password=kwargs.pop("password")
                )
            else:
                # Otherwise, just update this old password record
                password = kwargs.pop("password")
                salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
                kwargs["password_hash"] = bcrypt.hashpw(
                    password.encode(), salt
                ).decode()
                kwargs["password_salt"] = salt.decode()

        return super().update(id, **kwargs)

    def change_password(
        self,
        user_id: str,
        current_password: Optional[str],
        new_password: Optional[str],
    ) -> Dict[str, str]:
        """Change a user's password with verification"""
        # Validate inputs up-front so a missing or weak password fails
        # cleanly with 422/401 rather than crashing inside bcrypt.
        if current_password is None or not isinstance(current_password, str):
            raise HTTPException(status_code=422, detail="current_password is required")
        enforce_password_policy(new_password)

        # Find current active credential
        credentials = UserCredentialModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=user_id or env("ROOT_ID"),
            model_registry=self.model_registry,
            user_id=user_id,
            filters=[
                UserCredentialModel.DB(
                    self.model_registry.DB.manager.Base
                ).password_changed_at
                == None,
                UserCredentialModel.DB(self.model_registry.DB.manager.Base).deleted_at
                == None,
            ],
        )

        if not credentials:
            raise HTTPException(status_code=404, detail="User credentials not found")

        credential = credentials[0]

        # Handle both dictionary and object return types
        password_hash = (
            credential["password_hash"]
            if isinstance(credential, dict)
            else credential.password_hash
        )

        # Verify current password
        if not bcrypt.checkpw(current_password.encode(), password_hash.encode()):
            raise HTTPException(status_code=401, detail="Current password is incorrect")

        # Mark the current password as changed
        credential_id = (
            credential["id"] if isinstance(credential, dict) else credential.id
        )

        # Create a temporary manager with ROOT credentials for the update operation
        # since the credential might have been created by ROOT
        with UserCredentialManager(
            requester_id=env("ROOT_ID"), model_registry=self.model_registry
        ) as root_manager:
            # Update existing credential
            root_manager.update(
                id=credential_id, password_changed_at=datetime.now(timezone.utc)
            )

        # Determine who should be the requester for the new credential
        # If the requester is the same as the user whose password is being changed,
        # then the user is changing their own password
        # Otherwise, the requester (e.g., root) is changing someone else's password
        if self.requester.id == user_id:
            # User is changing their own password
            with UserCredentialManager(
                requester_id=user_id, model_registry=self.model_registry
            ) as user_manager:
                user_manager.create(user_id=user_id, password=new_password)
        else:
            # Someone else (like root) is changing the user's password
            self.create(user_id=user_id, password=new_password)

        return {"message": "Password changed successfully"}


UserModel.Manager = UserManager
UserCredentialModel.Manager = UserCredentialManager
