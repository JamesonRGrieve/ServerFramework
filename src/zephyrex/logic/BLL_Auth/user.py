import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Type, cast

import bcrypt
from fastapi import HTTPException, Header, Request, status

from pydantic import Field, ValidationError, model_validator

from sqlalchemy import or_
from zephyrex.lib.Dependencies import jwt
from zephyrex.lib.Environment import env, extract_base_domain
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    LockoutPolicy,
    LockoutTracker,
    rate_limit,
)
from zephyrex.lib.Logging import logger
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
    _invitation_hooks,
    _lockout_hooks,
    _metadata_hooks,
    _session_hooks,
)


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

    @classmethod
    def user_has_read_access(
        cls, user_id, record, model_registry, minimum_role=None, referred=False
    ):
        """
        Check if a user has read access to a user record.

        IMPORTANT: User records have special access rules:
        1. Users can always see themselves
        2. Users can see other users in teams they have access to
        3. ROOT_ID and SYSTEM_ID can see all users
        4. Records created by ROOT_ID can only be accessed by ROOT_ID

        This behavior differs from other entities where explicit permissions
        are required to see records created by other users.

        Args:
            user_id: The ID of the user requesting access
            record: The User record to check
            model_registry: ModelRegistry instance for database access
            minimum_role: Minimum role required (if applicable)
            referred: Whether this check is part of a referred access check

        Returns:
            bool: True if access is granted, False otherwise
        """
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_user_id,
        )

        # Get the record if an ID was passed
        if isinstance(record, str):
            db = model_registry.DB.session()
            record_obj = (
                db.query(cls.DB(model_registry.DB.manager.Base))
                .filter(cls.DB(model_registry.DB.manager.Base).id == record)
                .first()
            )
            if record_obj is None:
                return False
        else:
            record_obj = record
            db = model_registry.DB.session()

        # ROOT_ID can access everything
        if is_root_id(user_id):
            return True

        # Check for deleted records - only ROOT_ID can see them
        if hasattr(record_obj, "deleted_at") and record_obj.deleted_at is not None:
            return False

        # Users can see their own records
        if user_id == record_obj.id:
            return True

        # Check for records created by SYSTEM_ID
        if hasattr(
            record_obj, "created_by_user_id"
        ) and record_obj.created_by_user_id == env("SYSTEM_ID"):
            # For view operations, regular users can view
            if minimum_role is None or minimum_role == "user":
                return True
            # For admin operations, only ROOT_ID and SYSTEM_ID
            return is_root_id(user_id) or is_system_user_id(user_id)

        # Check for records created by TEMPLATE_ID
        if hasattr(
            record_obj, "created_by_user_id"
        ) and record_obj.created_by_user_id == env("TEMPLATE_ID"):
            # For view/copy/execute/share operations, all users can access
            if minimum_role is None or minimum_role == "user":
                return True
            # For edit/delete, only ROOT_ID and SYSTEM_ID can modify
            return is_root_id(user_id) or is_system_user_id(user_id)

        # For direct record-level access checks, use standard permission system
        if not referred:
            # Check if created by this user
            if (
                hasattr(record_obj, "created_by_user_id")
                and record_obj.created_by_user_id == user_id
            ):
                return True

            # Use standard permission system
            result, _ = check_permission(
                user_id,
                cls.DB,
                record_obj.id,
                db,
                PermissionType.VIEW if minimum_role is None else None,
                minimum_role=minimum_role,
            )
            return result == PermissionResult.GRANTED

        return False

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


class UserManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
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
        # {
        #     "path": "",
        #     "method": "post",
        #     "function": "register",
        #     "auth_type": AuthType.NONE,
        #     "summary": "Register a new user",
        #     "description": "Registers a new user.",
        #     "response_model": "UserModel.ResponseSingle",
        #     "status_code": 201,
        # },
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
            "summary": "list invitations for user",
            "description": "list invitations for user",
            "response_model": "Dict[str, str]",
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
            # child_network_model_cls will be inferred from the manager
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
        expiration_hours: int = 24,
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
        return jwt.encode(payload, env("JWT_SECRET"), algorithm=env("JWT_ALGORITHM"))  # type: ignore[no-any-return]

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
        decode_kwargs = dict(
            algorithms=[env("JWT_ALGORITHM")],
            audience=env("JWT_AUDIENCE"),
            issuer=env("JWT_ISSUER"),
            leeway=30,
            options={"require": ["exp", "nbf", "iat", "jti", "aud", "iss"]},
        )
        try:
            return cast(
                Dict[str, Any], jwt.decode(token, env("JWT_SECRET"), **decode_kwargs)
            )
        except jwt.InvalidSignatureError:
            previous = env("JWT_SECRET_PREVIOUS")
            if previous:
                return cast(
                    Dict[str, Any], jwt.decode(token, previous, **decode_kwargs)
                )
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

            user = UserModel.DB(model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                id=payload["sub"],
                return_type="dto",
                override_dto=UserModel,
            )

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
    def auth(
        model_registry,
        authorization: str = Header(None),
        request: Dict | None = None,
    ) -> UserModel:
        """Authenticate a user from Authorization header"""
        if isinstance(request, dict):
            request = RequestInfo(request)
        # bypass auth for user registration
        if (
            request
            and str(request.url).endswith("/v1/user")  # type: ignore[attr-defined]
            and request.method == "POST"  # type: ignore[attr-defined]
        ):
            return None  # type: ignore[return-value]

        if not authorization:
            raise HTTPException(
                status_code=401, detail="Authorization header is missing!"
            )

        ip = None
        server = None
        if request:
            # H-7 — never trust X-Forwarded-For unless the immediate peer is
            # a configured trusted proxy. Centralized in `resolve_client_ip`
            # so spoofing one transport doesn't bypass another.
            from zephyrex.lib.InboundSecurity import resolve_client_ip

            peer_host: Optional[str] | None = None
            client_obj = getattr(request, "client", None)
            if client_obj is not None:
                if hasattr(client_obj, "host"):
                    peer_host = client_obj.host
                elif isinstance(client_obj, (tuple, list)) and client_obj:
                    peer_host = client_obj[0]
                elif isinstance(client_obj, dict) and "host" in client_obj:
                    peer_host = client_obj["host"]
            ip = resolve_client_ip(request, peer_host=peer_host)
            host = request.headers.get("Host")  # type: ignore[attr-defined]
            scheme = request.headers.get("X-Forwarded-Proto", "http")  # type: ignore[attr-defined]
            if host:
                server = f"{scheme}://{host}"
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

                api_key_header = (
                    request.headers.get("X-API-Key") if request else None  # type: ignore[attr-defined]
                )
                principal = resolve_principal_from_api_key(api_key_header)
                if not principal and token:
                    principal = resolve_principal_from_api_key(token)
                if principal:
                    return (  # type: ignore[no-any-return]
                        db.query(UserModel.DB(db_manager.Base))
                        .filter(UserModel.DB(db_manager.Base).id == principal)
                        .first()
                    )

                try:
                    payload = UserManager._decode_jwt(token)

                    # If the token carries a `jti`, the bound session must
                    # still be active. A revoked session invalidates every
                    # bearer token issued for it.
                    UserManager._enforce_session_not_revoked(
                        payload, model_registry, db=db
                    )

                    user = (
                        db.query(UserModel.DB(db_manager.Base))
                        .filter(UserModel.DB(db_manager.Base).id == payload["sub"])
                        .first()
                    )
                    if not user:
                        raise HTTPException(status_code=404, detail="User not found")

                    if not user.active:
                        raise HTTPException(
                            status_code=403, detail="User account is disabled"
                        )

                    return user  # type: ignore[no-any-return]
                except jwt.ExpiredSignatureError:
                    raise HTTPException(status_code=401, detail="Token has expired")
                except jwt.InvalidTokenError:
                    raise HTTPException(status_code=401, detail="Invalid token")

            elif authorization.startswith("Basic"):
                # Basic auth with username/email and password
                try:
                    import base64

                    auth_encoded = authorization.replace("Basic ", "").strip()
                    auth_decoded = base64.b64decode(auth_encoded).decode("utf-8")

                    if ":" not in auth_decoded:
                        raise HTTPException(
                            status_code=401, detail="Invalid authentication format"
                        )

                    identifier, password = auth_decoded.split(":", 1)

                    user = UserModel.DB(db_manager.Base).get(
                        requester_id=env("ROOT_ID"),
                        model_registry=model_registry if model_registry else None,
                        db=db if not model_registry else None,
                        filters=[
                            or_(
                                UserModel.DB(db_manager.Base).email == identifier,
                                UserModel.DB(db_manager.Base).username == identifier,
                            )
                        ],
                        return_type="dto",
                        override_dto=UserModel,
                    )

                    if not user:
                        raise HTTPException(
                            status_code=401, detail="Invalid credentials"
                        )

                    if not user.active:
                        raise HTTPException(
                            status_code=403, detail="User account is disabled"
                        )

                    # Get current credential (password_changed_at is NULL for current password)
                    credentials = UserCredentialModel.DB(db_manager.Base).get(
                        requester_id=env("ROOT_ID"),
                        model_registry=model_registry if model_registry else None,
                        db=db if not model_registry else None,
                        filters=[
                            UserCredentialModel.DB(db_manager.Base).user_id == user.id,
                            UserCredentialModel.DB(db_manager.Base).password_changed_at
                            == None,
                        ],
                    )

                    if not credentials:
                        raise HTTPException(
                            status_code=401, detail="No valid credentials found"
                        )

                    # Check password
                    if not bcrypt.checkpw(
                        password.encode(), credentials.password_hash.encode()
                    ):
                        # Check if there is an older password that matches
                        old_credentials = UserCredentialModel.list(
                            filters=[
                                UserCredentialModel.DB(db_manager.Base).user_id
                                == user.id,
                                UserCredentialModel.DB(
                                    db_manager.Base
                                ).password_changed_at
                                != None,
                            ],
                            order_by=[
                                UserCredentialModel.DB(
                                    db_manager.Base
                                ).password_changed_at.desc()
                            ],
                        )[0]

                        if old_credentials and bcrypt.checkpw(
                            password.encode(),
                            old_credentials.password_hash.encode(),
                        ):
                            logger.info(
                                "Login attempt used a previously valid password "
                                "(changed %s)",
                                old_credentials.password_changed_at.strftime("%Y-%m"),
                            )
                        raise HTTPException(
                            status_code=401, detail="Invalid credentials"
                        )

                    return user  # type: ignore[no-any-return]
                except Exception as e:
                    if isinstance(e, HTTPException):
                        raise e
                    raise HTTPException(status_code=401, detail="Authentication failed")
            else:
                raise HTTPException(
                    status_code=401, detail="Unsupported authorization method"
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

        if not credentials or not credentials[0].password_hash:
            return False

        try:
            return bcrypt.checkpw(
                password.encode(), credentials[0].password_hash.encode()
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
    @rate_limit(DEFAULT_AUTH_RATE_LIMIT, scope="ip")
    def login(
        login_data: Dict[str, Any] | None = None,
        ip_address: str | None = None,
        req_uri: Optional[str] | None = None,
        authorization: Optional[str] | None = None,
        model_registry=None,
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

        db = model_registry.DB.session()
        close_session = True

        try:

            root_id = env("ROOT_ID")

            # Extract credentials from Basic Auth header if provided
            if authorization and authorization.startswith("Basic "):
                try:
                    import base64

                    auth_encoded = authorization.replace("Basic ", "").strip()
                    auth_decoded = base64.b64decode(auth_encoded).decode("utf-8")

                    if ":" not in auth_decoded:
                        raise HTTPException(
                            status_code=401,
                            detail="Invalid Authorization header, bad format for mode 'Basic'.",
                        )

                    identifier, password = auth_decoded.split(":", 1)
                    login_data = {"email": identifier, "password": password}
                except Exception:
                    raise HTTPException(
                        status_code=401, detail="Authentication failed."
                    )

            if not login_data:
                raise HTTPException(
                    status_code=400, detail="Invalid Authorization header."
                )

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

            import unicodedata

            login_model = UserManager.UserLoginModel(**login_data)
            normalized_identifier = (
                unicodedata.normalize("NFKC", login_model.email).lower().strip()
            )

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
                UserManager._lockout_tracker.record_failure(
                    lockout_key, "password_login"
                )
                # Burn the same bcrypt time as a real password check to
                # prevent timing-based username enumeration.
                bcrypt.checkpw(
                    (login_model.password or "x").encode(), _DUMMY_BCRYPT_HASH
                )
                raise HTTPException(status_code=401, detail="Invalid credentials")

            user = user[0]

            # Per-user threshold gate (auth_lockout extension when loaded).
            # The IP-keyed in-memory lockout above is the always-on defense.
            if _lockout_hooks["assert_within_threshold"] is not None:
                _lockout_hooks["assert_within_threshold"](user["id"], model_registry)

            # Check if user account is active
            if not user["active"]:
                if _lockout_hooks["record_failure"] is not None:
                    _lockout_hooks["record_failure"](
                        user["id"], ip_address, model_registry
                    )
                raise HTTPException(status_code=401, detail="Invalid credentials")

            # Check if user account was deleted
            if user["deleted_at"]:
                if _lockout_hooks["record_failure"] is not None:
                    _lockout_hooks["record_failure"](
                        user["id"], ip_address, model_registry
                    )
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
                    old_credentials = (
                        model_registry.DB.session()
                        .query(UserCredentialModel.DB(model_registry.DB.manager.Base))
                        .filter(
                            UserCredentialModel.DB(
                                model_registry.DB.manager.Base
                            ).user_id
                            == user["id"],
                            UserCredentialModel.DB(
                                model_registry.DB.manager.Base
                            ).password_changed_at
                            != None,
                        )
                        .order_by(
                            UserCredentialModel.DB(
                                model_registry.DB.manager.Base
                            ).password_changed_at.desc()
                        )
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
                        raise HTTPException(
                            status_code=401, detail="Invalid credentials"
                        )

            else:
                raise HTTPException(
                    status_code=400, detail="Either password or token is required"
                )

            # H-8 — successful auth clears the IP-keyed counter so a user
            # who misremembered their password once does not carry the
            # failure into the next legitimate attempt.
            UserManager._lockout_tracker.clear(lockout_key, "password_login")

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

            teams_with_roles = []
            for user_team in user_teams:
                team = TeamModel.DB(model_registry.DB.manager.Base).get(
                    requester_id=root_id,
                    model_registry=model_registry,
                    id=user_team["team_id"],
                )

                role = RoleModel.DB(model_registry.DB.manager.Base).get(
                    requester_id=root_id,
                    model_registry=model_registry,
                    id=user_team["role_id"],
                )

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

            model_registry.DB.session().commit()
            return result
        finally:
            # Close session if we created it
            if close_session:
                model_registry.DB.session().close()

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
        cannot meaningfully run without ``auth_session``.
        """
        user_id = user.id if hasattr(user, "id") else user["id"]
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

    def get(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a user with optional included relationships."""
        if "team_id" in kwargs:
            if not self.DB.user_has_read_access(
                self.requester.id, kwargs.get("team_id"), self.db
            ):
                raise HTTPException(status_code=403, detail="get - not permissable")

        return super().get(include=include, fields=fields, **kwargs)

    def get_current_user(self, fields: Optional[List[str]] | None = None):
        """Get the current user's profile."""
        user = self.get(id=self.requester.id, fields=fields)
        if hasattr(user, "model_dump"):
            return user.model_dump()
        return user

    def update_current_user(self, body: Dict[str, Any]):
        """Update the current user's profile."""
        user_data = body.get("user", {})
        updated_user = self.update(id=self.requester.id, **user_data)
        if hasattr(updated_user, "model_dump"):
            return updated_user.model_dump()
        return updated_user

    def delete(self, id: str | None = None):
        """Override delete to handle special self-deletion logic."""
        target_id = id or self.requester.id

        if target_id == self.requester.id:
            current_model = self.Model.DB(self.model_registry.DB.manager.Base)

            self.update(id=self.requester.id, active=True)
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
        """Change the current user's password"""
        current_password = body.get("current_password")
        new_password = body.get("new_password")
        return self.credentials.change_password(
            user_id=self.requester.id,
            current_password=current_password,
            new_password=new_password,
        )

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
            try:
                import base64

                auth_encoded = authorization.replace("Basic ", "").strip()
                auth_decoded = base64.b64decode(auth_encoded).decode("utf-8")

                if ":" not in auth_decoded:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid Authorization header, bad format for mode 'Basic'.",
                    )

                email_from_header, password_from_header = auth_decoded.split(":", 1)
            except Exception:
                raise HTTPException(status_code=401, detail="Authentication failed.")

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

        import unicodedata

        if email:
            email = unicodedata.normalize("NFKC", email).lower().strip()
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

        # Get the model fields for comparison
        model_fields_set = set(UserModel.__annotations__.keys())
        # Add fields from mixins that might not be in annotations
        model_fields_set.add("image_url")

        for key, value in registration_data.items():
            # Include invitation_code and invitation_id as special fields that shouldn't go to metadata
            if key in model_fields_set or key in [
                "password",
                "invitation_code",
                "invitation_id",
                "external_payment_id",
            ]:
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

    def list_invitations_for_user(self):
        """List all invitations for the requesting user. Routed through the
        auth_invitations extension hook (Scope #4); returns an empty list
        when the extension is not loaded."""
        factory = _invitation_hooks["invitation_manager_factory"]
        if factory is None:
            return {"invitations": []}

        invitation_manager = factory(
            requester_id=env("ROOT_ID"),
            target_team_id=None,
            model_registry=self.model_registry,
        )

        invitations = invitation_manager.list(include=["invitation"])
        invitations_dict = []
        user_id = self.requester.id
        user = self.get(id=user_id)

        from zephyrex.pydantic2.registry import obj_to_dict
        from zephyrex.logic.BLL_Auth.team import TeamManager
        from zephyrex.logic.BLL_Auth.role import RoleManager

        for invitation in invitations:
            if invitation.team_id:
                team_manager = TeamManager(
                    requester_id=env("ROOT_ID"), model_registry=self.model_registry
                )
                invitation.team = team_manager.get(id=invitation.team_id)
            if invitation.role_id:
                role_manager = RoleManager(
                    requester_id=env("ROOT_ID"), model_registry=self.model_registry
                )
                invitation.role = role_manager.get(id=invitation.role_id)

            invitation_dict = obj_to_dict(invitation)
            invitees_dict = []
            if invitation.user_id is None:
                invitees = invitation_manager.Invitee_manager.list(
                    invitation_id=invitation.id
                )
                for invitee in invitees:
                    if invitee.user_id != user_id:
                        continue
                    invitee_dict = obj_to_dict(invitee)
                    invitee_dict["status"] = (
                        "declined"
                        if invitee.declined_at
                        else "accepted" if invitee.accepted_at else "pending"
                    )
                    invitees_dict.append(invitee_dict)
                if invitees_dict:
                    invitation_dict["invitees"] = invitees_dict
            elif invitation.user_id == user_id:
                invitation_dict["user"] = user

            if invitation.user_id == user_id or invitees_dict:
                invitations_dict.append(invitation_dict)
        return {"invitations": invitations_dict}


class UserCredentialModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference,  # type: ignore[name-defined]
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

    class Create(BaseModel, UserModel.Reference.ID):  # type: ignore[name-defined]
        password_hash: Optional[str]

    class CreateRaw(BaseModel, UserModel.Reference.ID):  # type: ignore[name-defined]
        password: str = Field(None, description="New password (will be hashed)")  # type: ignore[assignment]

    class Update(BaseModel):
        # This model and entity should not be manually updatable, only via the User password change function.
        # However, we need to allow updating the password_changed_at field for tests
        password_changed_at: Optional[datetime] | None = None

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):  # type: ignore[name-defined]
        password_changed_at: Optional[DateSearchModel] | None = None


class UserCredentialManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = UserCredentialModel

    def create(self, **kwargs):
        """Create new user credentials (password)"""
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

    # Minimum password requirements. A password must contain at least
    # MIN_LENGTH characters and at least one digit + one letter. The aim
    # is to refuse trivially-weak passwords (e.g. "a" or "12345"), not to
    # police every dictionary password — defence in depth on top of
    # bcrypt + MFA.
    PASSWORD_MIN_LENGTH = 8

    @staticmethod
    def _validate_password_policy(password: Optional[str]) -> None:
        """Reject passwords that don't meet the minimum policy."""
        if not isinstance(password, str) or not password.strip():
            raise HTTPException(
                status_code=422,
                detail="Password must be a non-empty string",
            )
        if len(password) < UserCredentialManager.PASSWORD_MIN_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Password must be at least "
                    f"{UserCredentialManager.PASSWORD_MIN_LENGTH} characters"
                ),
            )
        has_alpha = any(c.isalpha() for c in password)
        has_digit = any(c.isdigit() for c in password)
        if not (has_alpha and has_digit):
            raise HTTPException(
                status_code=422,
                detail="Password must contain at least one letter and one digit",
            )

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
        self._validate_password_policy(new_password)

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
