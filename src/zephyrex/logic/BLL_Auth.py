import base64 as _b64
import hmac
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Type,
)

if TYPE_CHECKING:
    from zephyrex.logic.BLL_Auth import (
        UserManager,
        UserCredentialManager,
        TeamManager,
        RoleManager,
        UserTeamManager,
        RateLimitPolicyManager,
    )

    # Session symbols are owned by the ``auth_session`` extension and
    # are resolved through the PEP 562 ``__getattr__`` shim below at
    # runtime. Forward-references in core annotations use string
    # literals so this module never imports the extension — even at
    # type-check time.

import bcrypt
from fastapi import Header, HTTPException, Request, status

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_

from zephyrex.database.StaticPermissions import can_manage_permissions
from zephyrex.lib.Dependencies import jwt
from zephyrex.lib.Environment import env, extract_base_domain
from zephyrex.lib.InboundSecurity import (
    DEFAULT_AUTH_RATE_LIMIT,
    LockoutPolicy,
    LockoutTracker,
    rate_limit,
)
from zephyrex.lib.Logging import logger
from zephyrex.lib.Pydantic import BaseModel  # type: ignore[no-redef]
from zephyrex.lib.Pydantic2FastAPI import (
    AuthType,
    RequestInfo,
    RouterMixin,
    RouteType,
    static_route,
)
from zephyrex.logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    ImageMixinModel,
    ModelMeta,
    NameMixinModel,
    NumericalSearchModel,
    ParentMixinModel,
    StringSearchModel,
    UpdateMixinModel,
)

# Resolve _BCRYPT_ROUNDS from env now that the import is available.
# Using gensalt() without a rounds argument leaves the cost at whatever the
# installed bcrypt's default is, which has shifted across library versions
# and is invisible to operators. Configurable via BCRYPT_ROUNDS (default 12).
_BCRYPT_ROUNDS = int(env("BCRYPT_ROUNDS"))
_DUMMY_BCRYPT_HASH = bcrypt.hashpw(b"timing-equalization-dummy", bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))


class InvalidGrantError(HTTPException):
    """Raised when a passwordless grant cannot be validated or no validator
    is registered for the requested grant_type."""

    def __init__(self, detail: str = "Invalid grant") -> None:
        super().__init__(status_code=401, detail=detail)


class PendingSessionError(HTTPException):
    """Raised when an authenticated request hits a session that is still in
    the ``awaiting_approval`` pending state (Item 59 cross-device pairing)."""

    def __init__(
        self, detail: str = "Session is awaiting approval and cannot be used"
    ) -> None:
        super().__init__(status_code=401, detail=detail)


class OneTimeTokenMixin(BaseModel):
    """Reusable primitive for short-lived, single-use tokens hashed at rest.

    Captures the recovery-code shape (see ``MultifactorRecoveryCodeModel`` —
    next consumer) so passwordless extensions (magic-link Item 58, device
    pairing Item 59) can share one verified implementation.
    """

    code_hash: str = Field(..., description="bcrypt hash of the raw code")
    code_salt: str = Field(..., description="Salt used when hashing the code")
    # H-5 — indexable HMAC fingerprint of the raw code so verify-time
    # lookup is a single indexed row read instead of a bcrypt-against-
    # every-unused-token loop. The bcrypt comparison still runs to
    # constant-time-confirm the match; the fingerprint just narrows the
    # candidate set to exactly one row (or zero).
    code_fingerprint: Optional[str] = Field(
        None,
        description=(
            "HMAC-SHA256(FRAMEWORK_FERNET_KEY or JWT_SECRET, raw_code) hex. "
            "Indexed; replaces the unbounded bcrypt-loop verify."
        ),
    )
    expires_at: datetime = Field(..., description="UTC expiry timestamp")
    is_used: bool = Field(False, description="Whether this token has been redeemed")
    used_at: Optional[datetime] = Field(
        None, description="When this token was redeemed, if any"
    )
    created_ip: Optional[str] = Field(
        None, description="IP address that generated the token"
    )

    @staticmethod
    def fingerprint(raw_code: str) -> str:
        """HMAC fingerprint used for indexed token lookup (H-5).

        Keyed on FRAMEWORK_FERNET_KEY when set, falling back to
        JWT_SECRET. Both rotate independently of token issuance, so we
        accept that fingerprints become invalid on key rotation — that is
        the expected blast radius (re-issue outstanding tokens) rather
        than the alternative of a static unkeyed hash that lets anyone
        with read access to the table validate tokens offline.
        """
        import hashlib

        key_material = env("FRAMEWORK_FERNET_KEY") or env("JWT_SECRET") or ""
        if not key_material:
            raise ValueError(
                "Cannot compute HMAC fingerprint: neither "
                "FRAMEWORK_FERNET_KEY nor JWT_SECRET is set"
            )
        key_material = key_material.encode("utf-8")
        return hmac.new(
            key_material, raw_code.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def verify(self, submitted_code: str) -> bool:
        """Constant-time verification of a submitted raw code."""
        try:
            computed = bcrypt.hashpw(
                submitted_code.encode(), self.code_salt.encode()
            ).decode()
        except Exception:
            return False
        return hmac.compare_digest(computed, self.code_hash)

    def mark_used(self) -> None:
        self.is_used = True
        self.used_at = datetime.now(timezone.utc)

    @classmethod
    def generate(cls, ttl_minutes: int) -> Tuple[str, "OneTimeTokenMixin"]:
        """Generate 256 bits of entropy, return ``(raw_code, instance)``.

        The raw code is base64url-encoded for URL safety; only the bcrypt
        hash, salt, and fingerprint are persisted. The instance can be
        merged into a concrete subclass via ``.model_dump()``.
        """
        raw_bytes = secrets.token_bytes(32)
        raw_code = _b64.urlsafe_b64encode(raw_bytes).rstrip(b"=").decode("ascii")
        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        code_hash = bcrypt.hashpw(raw_code.encode(), salt).decode()
        instance = cls(
            code_hash=code_hash,
            code_salt=salt.decode(),
            code_fingerprint=cls.fingerprint(raw_code),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
            is_used=False,
            used_at=None,
            created_ip=None,
        )
        return raw_code, instance


class PasswordlessGrantRegistry:
    """Process-global registry of passwordless grant validators.

    Extensions (e.g. ``EXT_Auth_MagicLink``, ``EXT_Auth_DevicePairing``)
    populate this at load time with a callable that maps a grant payload to
    the resolved ``UserModel``. ``UserManager.login_via_grant`` dispatches
    here without knowing the grant kinds.
    """

    _validators: ClassVar[Dict[str, Callable[[BaseModel], "UserModel"]]] = {}

    @classmethod
    def register(
        cls, grant_type: str, validator: Callable[[BaseModel], "UserModel"]
    ) -> None:
        cls._validators[grant_type] = validator

    @classmethod
    def get(cls, grant_type: str) -> Callable[[BaseModel], "UserModel"]:
        if grant_type not in cls._validators:
            raise KeyError(
                f"No passwordless grant validator registered for grant_type={grant_type!r}"
            )
        return cls._validators[grant_type]

    @classmethod
    def list_grant_types(cls) -> List[str]:
        return list(cls._validators.keys())


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
            is_template_id,
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
            if self.email is not None and "@" not in self.email:
                raise ValueError("Invalid email format")
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

        escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
            return jwt.decode(token, env("JWT_SECRET"), **decode_kwargs)
        except jwt.InvalidSignatureError:
            previous = env("JWT_SECRET_PREVIOUS")
            if previous:
                return jwt.decode(token, previous, **decode_kwargs)
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
            normalized_identifier = unicodedata.normalize(
                "NFKC", login_model.email
            ).lower().strip()

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

        # Strip server-controlled audit/identity fields from the inbound
        # body so a registering client cannot spoof their `id`,
        # `created_by_user_id`, or audit timestamps.
        if isinstance(registration_data, dict):
            for banned in (
                "id",
                "created_at",
                "updated_at",
                "deleted_at",
                "created_by_user_id",
                "updated_by_user_id",
                "deleted_by_user_id",
            ):
                registration_data.pop(banned, None)

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

        from zephyrex.lib.Pydantic import obj_to_dict

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


# UserRecoveryQuestion model+manager moved to extension `auth_recovery_questions`
# (Scope #1). Apps that want question-based recovery enable that extension via
# APP_EXTENSIONS. Re-export the names so existing imports of
# `BLL_Auth.UserRecoveryQuestionModel` continue to resolve when the extension
# is loaded; otherwise raise a clear error pointing to the migration path.
def _moved_to_extension(name: str, extension: str):
    raise ImportError(
        f"{name} was extracted to extension {extension!r}. Add {extension!r} "
        f"to APP_EXTENSIONS or import from "
        f"zephyrex.extensions.{extension}.BLL_Recovery_Questions."
    )


_EXTRACTED = {
    "UserRecoveryQuestionModel": (
        "auth_recovery_questions",
        "BLL_Recovery_Questions",
    ),
    "UserRecoveryQuestionManager": (
        "auth_recovery_questions",
        "BLL_Recovery_Questions",
    ),
    "FailedLoginAttemptModel": ("auth_lockout", "BLL_Lockout"),
    "FailedLoginAttemptManager": ("auth_lockout", "BLL_Lockout"),
    "SessionModel": ("auth_session", "BLL_Session"),
    "SessionManager": ("auth_session", "BLL_Session"),
    "MetadataModel": ("metadata", "BLL_Metadata"),
    "UserMetadataManager": ("metadata", "BLL_Metadata"),
    "TeamMetadataManager": ("metadata", "BLL_Metadata"),
    "PermissionModel": ("acl_rbac", "BLL_ACL"),
    "PermissionManager": ("acl_rbac", "BLL_ACL"),
    "InvitationModel": ("auth_invitations", "BLL_Invitations"),
    "InvitationManager": ("auth_invitations", "BLL_Invitations"),
    "InviteeModel": ("auth_invitations", "BLL_Invitations"),
    "InviteeManager": ("auth_invitations", "BLL_Invitations"),
}


# Hook callables — populated by extensions at registration time. None when
# the extension isn't loaded; core code falls back to a safe default.
_lockout_hooks: dict = {
    "assert_within_threshold": None,  # (user_id, model_registry) -> None | raises
    "record_failure": None,  # (user_id, ip, model_registry) -> None
    "manager_factory": None,  # (*, requester_id, target_id, model_registry) -> manager
}


def register_lockout_hooks(
    *,
    assert_within_threshold=None,
    record_failure=None,
    manager_factory=None,
) -> None:
    """Called by `auth_lockout.EXT_Lockout.AuthLockoutExtension.on_load`."""
    if assert_within_threshold is not None:
        _lockout_hooks["assert_within_threshold"] = assert_within_threshold
    if record_failure is not None:
        _lockout_hooks["record_failure"] = record_failure
    if manager_factory is not None:
        _lockout_hooks["manager_factory"] = manager_factory


# auth_session extension hooks — populated by ``auth_session.on_load``. When
# the extension is not loaded, ``issue_session`` returns a fresh key without
# persistence (token is stateless), ``enforce_not_revoked`` is a no-op once
# ``jti`` is present (see ``_enforce_session_not_revoked``), and
# ``manager_factory``/``revoke_user_sessions`` raise 503 because the
# per-user session surface is genuinely unavailable.
_session_hooks: dict = {
    "issue_session": None,  # (*, user_id, model_registry, expiration_hours, device_type, grant_type, pending_state) -> session_key
    "enforce_not_revoked": None,  # (payload, model_registry, db) -> None | raises
    "manager_factory": None,  # (*, requester_id, target_id, target_team_id, model_registry) -> manager
    "revoke_user_sessions": None,  # (*, user_id, requester_id, model_registry) -> int
}


def register_session_hooks(
    *,
    issue_session=None,
    enforce_not_revoked=None,
    manager_factory=None,
    revoke_user_sessions=None,
) -> None:
    """Called by ``auth_session.EXT_Session.AuthSessionExtension.on_load``.

    Each argument is optional — operators can swap individual hook
    implementations (e.g. for testing) without re-registering the others.
    """
    for name, fn in (
        ("issue_session", issue_session),
        ("enforce_not_revoked", enforce_not_revoked),
        ("manager_factory", manager_factory),
        ("revoke_user_sessions", revoke_user_sessions),
    ):
        if fn is not None:
            _session_hooks[name] = fn


def reset_session_hooks() -> None:
    """Test helper — clear every registered ``_session_hooks`` entry.

    Production code should never call this; it exists so test fixtures
    that load and unload the extension can drop stale state between
    tests without leaking hooks across module reloads.
    """
    for name in list(_session_hooks.keys()):
        _session_hooks[name] = None


# Metadata extension hooks — populated by `metadata.on_load` (Scope #3).
# Core code that historically called MetadataModel/UserMetadataManager/
# TeamMetadataManager directly now goes through these hooks; when the
# extension is not loaded, calls degrade to no-ops or empty results.
_metadata_hooks: dict = {
    "list_preferences": None,  # (user_id, model_registry) -> Dict[str,str]
    "list_user_metadata": None,  # (user_id, model_registry) -> List[Any]
    "user_manager_factory": None,  # (requester_id, target_id, model_registry, **kw) -> manager
    "team_manager_factory": None,  # (requester_id, target_team_id, model_registry, **kw) -> manager
    "create_user_metadata": None,  # (user_id, key, value, model_registry, *, requester_id) -> None
    "update_user_metadata": None,  # (id, value, model_registry, *, requester_id) -> None
}


def register_metadata_hooks(
    *,
    list_preferences=None,
    list_user_metadata=None,
    user_manager_factory=None,
    team_manager_factory=None,
    create_user_metadata=None,
    update_user_metadata=None,
) -> None:
    """Called by `metadata.EXT_Metadata.MetadataExtension.on_load`."""
    for name, fn in (
        ("list_preferences", list_preferences),
        ("list_user_metadata", list_user_metadata),
        ("user_manager_factory", user_manager_factory),
        ("team_manager_factory", team_manager_factory),
        ("create_user_metadata", create_user_metadata),
        ("update_user_metadata", update_user_metadata),
    ):
        if fn is not None:
            _metadata_hooks[name] = fn


# auth_invitations extension hooks — populated by `auth_invitations.on_load`
# (Scope #4). Encapsulate every place core BLL_Auth used to reach into
# InvitationModel / InviteeModel / InvitationManager / InviteeManager.
_invitation_hooks: dict = {
    "lookup_by_id": None,  # (invitation_id, model_registry) -> dict | None
    "lookup_by_code": None,  # (code, model_registry) -> dict | None
    "apply_to_user": None,  # (invitation_dict, user_id, model_registry) -> None
    "invitation_manager_factory": None,  # (requester_id, target_team_id, model_registry, **kw) -> manager
    "invitee_manager_factory": None,  # (requester_id, target_id, model_registry, **kw) -> manager
    "list_invitees_for_user": None,  # (user_id, email, model_registry) -> List[dict]
    "invitation_db_class": None,  # (declarative_base) -> SA model
    "invitee_db_class": None,  # (declarative_base) -> SA model
}


def register_invitation_hooks(
    *,
    lookup_by_id=None,
    lookup_by_code=None,
    apply_to_user=None,
    invitation_manager_factory=None,
    invitee_manager_factory=None,
    list_invitees_for_user=None,
    invitation_db_class=None,
    invitee_db_class=None,
) -> None:
    for name, fn in (
        ("lookup_by_id", lookup_by_id),
        ("lookup_by_code", lookup_by_code),
        ("apply_to_user", apply_to_user),
        ("invitation_manager_factory", invitation_manager_factory),
        ("invitee_manager_factory", invitee_manager_factory),
        ("list_invitees_for_user", list_invitees_for_user),
        ("invitation_db_class", invitation_db_class),
        ("invitee_db_class", invitee_db_class),
    ):
        if fn is not None:
            _invitation_hooks[name] = fn


# acl_rbac extension hooks — populated by `acl_rbac.on_load` (Scope #5).
_acl_hooks: dict = {
    "permission_db_class": None,  # (declarative_base) -> SA model
    "create_permission": None,  # (resource_type, resource_id, user_id, can_*, model_registry, **kw) -> Any
}


def register_acl_hooks(
    *,
    permission_db_class=None,
    create_permission=None,
) -> None:
    for name, fn in (
        ("permission_db_class", permission_db_class),
        ("create_permission", create_permission),
    ):
        if fn is not None:
            _acl_hooks[name] = fn


# privacy extension hooks — populated by `privacy.on_load`. Core's
# logging redaction (``lib/Logging``, ``lib/Credentials``) consults the
# registered ``log_filter`` callable on every record. Without the
# extension, only the registered-secret scrubbing in core fires.
_pii_hooks: dict = {
    "log_filter": None,  # (logging.LogRecord) -> bool
}


def register_pii_hooks(*, log_filter=None) -> None:
    if log_filter is not None:
        _pii_hooks["log_filter"] = log_filter


# federation extension hooks — populated by `federation.on_load`. Core
# ``ModelRegistry.commit`` calls ``bootstrap`` after Phase 1 model binding
# so the federation extension can lift external types into the registry.
# Without it, the framework runs as a single-app server (no upstream
# federation) and the hook is a no-op.
_registry_hooks: dict = {
    "bootstrap_federation": None,  # (model_registry) -> federation_report | None
}


def register_registry_hooks(*, bootstrap_federation=None) -> None:
    if bootstrap_federation is not None:
        _registry_hooks["bootstrap_federation"] = bootstrap_federation


def __getattr__(name: str):  # PEP 562 — lazy module-level attribute access
    if name in _EXTRACTED:
        ext, module = _EXTRACTED[name]
        try:
            mod = __import__(
                f"zephyrex.extensions.{ext}.{module}",
                fromlist=[name],
            )
            return getattr(mod, name)
        except (ImportError, AttributeError):
            _moved_to_extension(name, ext)
    raise AttributeError(name)


# FailedLoginAttempt extracted to extension `auth_lockout` (Scope #2). Core
# `UserManager.login` consults it via the optional callable hooks below — when
# the extension isn't loaded the gate is the IP-keyed `LockoutTracker` only.


class TeamModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ParentMixinModel.Optional,
    NameMixinModel.Optional,
    ImageMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TeamManager"]] = None  # type: ignore[assignment]
    description: Optional[str] = Field(None, description="Team description")
    encryption_salt: Optional[str] = Field(
        ..., description="Per-team salt for row-level encryption of team data"
    )
    # TODO remove these two fields
    token: Optional[str] = Field(None, description="Team token")
    training_data: Optional[str] = Field(None, description="Training data for team")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Teams to which users can belong"
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": "FFFFFFFF-FFFF-FFFF-0000-FFFFFFFFFFFF",
            "name": "System",
            "parent_id": None,
            "encryption_salt": "",
        }
    ]

    @classmethod
    def user_has_read_access(
        cls, user_id, id, db, referred=False, db_manager=None, model_registry=None
    ):
        """
        Check if user has read access to a team.
        Read access requires VIEW permission.

        Args:
            user_id: The ID of the user to check
            id: The team ID to check
            db: Database session
            referred: Whether this is a referred check
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if read access is granted, False otherwise
        """
        from zephyrex.database.StaticPermissions import (
            PermissionResult,
            PermissionType,
            check_permission,
            is_root_id,
            is_system_user_id,
        )

        # ROOT_ID has read access to everything
        if is_root_id(user_id):
            return True

        # SYSTEM_ID has read access to all teams
        if is_system_user_id(user_id):
            return True

        # Get the team to check creator and deletion rules
        team = None
        if isinstance(id, str):
            team = (
                db.query(cls.DB(db_manager.Base))
                .filter(cls.DB(db_manager.Base).id == id)
                .first()
            )
            if not team:
                return False

            # Check if record is deleted - only ROOT_ID can see
            if hasattr(team, "deleted_at") and team.deleted_at is not None:
                return False

            # Teams created by ROOT_ID can only be viewed by ROOT_ID
            if team.created_by_user_id == env("ROOT_ID"):
                return False

            # Teams created by TEMPLATE_ID can be viewed by everyone
            if team.created_by_user_id == env("TEMPLATE_ID"):
                return True

        # For non-referred checks, check read permissions
        if not referred:
            result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.VIEW)
            return result == PermissionResult.GRANTED

        return False

    class Create(
        BaseModel, NameMixinModel, ParentMixinModel.Optional, ImageMixinModel.Optional
    ):
        description: Optional[str] = Field(None, description="Team description")
        encryption_salt: Optional[str] = Field(
            None, description="Per-team salt for row-level encryption of team data"
        )

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            if v is not None:
                v = v.strip()
                if not v:
                    raise ValueError("Team name cannot be empty")
            return v

    class Update(
        BaseModel,
        NameMixinModel.Optional,
        ParentMixinModel.Optional,
        ImageMixinModel.Optional,
    ):
        description: Optional[str] = Field(None, description="Team description")
        token: Optional[str] = Field(None, description="Team token")
        training_data: Optional[str] = Field(None, description="Training data for team")

        @field_validator("name")
        @classmethod
        def validate_name(cls, v):
            if v is not None:
                v = v.strip()
                if not v:
                    raise ValueError("Team name cannot be empty")
            return v

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        ImageMixinModel.Search,
    ):
        description: Optional[StringSearchModel] | None = None


class TeamManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = TeamModel
    _entity_label: ClassVar[Optional[str]] = "Team"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/team"
    tags: ClassVar[Optional[List[str]]] = ["Team Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    factory_params: ClassVar[List[str]] = ["target_team_id"]
    auth_dependency: ClassVar[Optional[str]] = "get_auth_user"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}/user",
            "method": "get",
            "function": "get_team_users",
            "summary": "Get team users",
            "description": "Gets users belonging to a team.",
            "response_model": "UserTeamModel.Network.ResponsePlural",
            "status_code": 200,
        },
        {
            "path": "/{team_id}/user/{user_id}",
            "method": "patch",
            "function": "patch_role",
            "summary": "Update user role",
            "description": "Updates a user's role within a team.",
            "response_model": "Dict[str, str]",
            "status_code": 200,
        },
    ]
    nested_resources: ClassVar[Dict[str, Any]] = {
        "invitation": {
            "child_resource_name": "invitation",
            "manager_property": "invitations",
            "child_manager_class": lambda: getattr(
                __import__(
                    "zephyrex.extensions.auth_invitations.BLL_Invitations",
                    fromlist=["InvitationManager"],
                ),
                "InvitationManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": [
                "get",
                "list",
                "create",
                "search",
                "update",
                "batch_update",
            ],
            "custom_routes": [
                {
                    "path": "",
                    "method": "delete",
                    "function": "revoke_all_invitations",
                    "summary": "Revoke all invitations",
                    "description": "Revokes ALL open invitations for a team.",
                    "status_code": 204,
                },
                {
                    "path": "",
                    "method": "get",
                    "function": "list_invitations_for_team",
                    "summary": "List invitations for team",
                    "description": "Lists all invitations for a team.",
                    "status_code": 200,
                },
            ],
        },
        "metadata": {
            "child_resource_name": "metadata",
            "manager_property": "team_metadata",
            "child_manager_class": lambda: getattr(
                __import__(
                    "zephyrex.extensions.metadata.BLL_Metadata",
                    fromlist=["TeamMetadataManager"],
                ),
                "TeamMetadataManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "role": {
            "child_resource_name": "role",
            "manager_property": "roles",
            "child_manager_class": lambda: getattr(
                __import__("zephyrex.logic.BLL_Auth", fromlist=["RoleManager"]),
                "RoleManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": [
                "create",
                "list",
                "search",
                "get",
                "update",
                "delete",
                "batch_update",
                "batch_delete",
            ],
        },
    }

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] | None = None,
        target_team_id: Optional[str] | None = None,
        model_registry: Optional[Any] | None = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._team_metadata = None
        self._user_teams = None
        self._roles = None
        self._invitations = None

    @property
    def team_metadata(self):
        if self._team_metadata is None:
            factory = _metadata_hooks["team_manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not loaded; team metadata unavailable",
                )
            self._team_metadata = factory(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
                parent=self,
            )
            if self._team_metadata is None:
                raise HTTPException(
                    status_code=503,
                    detail="metadata extension not bound to this registry",
                )
        return self._team_metadata

    @property
    def user_teams(self):
        if self._user_teams is None:
            self._user_teams = UserTeamManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._user_teams

    @property
    def roles(self):
        if self._roles is None:
            self._roles = RoleManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._roles

    @property
    def invitations(self):
        if self._invitations is None:
            factory = _invitation_hooks["invitation_manager_factory"]
            if factory is None:
                raise HTTPException(
                    status_code=503,
                    detail="auth_invitations extension not loaded; "
                    "team invitations unavailable",
                )
            self._invitations = factory(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._invitations

    def create(self, **kwargs):
        """Create a team with metadata"""
        # Extract metadata fields (non-model fields)
        metadata_fields = {}
        model_fields = {}

        # Get the model fields for comparison
        model_fields_set = set(self.Model.Create.__annotations__.keys())
        # TODO #51 Add fields from mixins dynamically that might not be in annotations
        model_fields_set.add("name")
        model_fields_set.add("parent_id")
        model_fields_set.add("description")
        model_fields_set.add("image_url")

        for key, value in kwargs.items():
            if key in model_fields_set:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # Mint a per-team salt if the caller did not supply one.
        if "encryption_salt" not in model_fields:
            model_fields["encryption_salt"] = secrets.token_hex(32)

        # Create the team first
        team = super().create(**model_fields)

        # Only proceed with metadata and associations if team creation succeeded
        if team:
            # Create metadata if provided
            if metadata_fields:
                for key, value in metadata_fields.items():
                    self.team_metadata.create(
                        team_id=team.id,
                        key=key,
                        value=str(value),
                    )

            # Add the creator as an admin of the team
            UserTeamManager(
                requester_id=self.requester.id, model_registry=self.model_registry
            ).create(  # Must create with Root ID or can't see Team (yet).
                team_id=team.id, user_id=self.requester.id, role_id=env("ADMIN_ROLE_ID")
            )

        return team

    def update(self, id: str, **kwargs):
        """Update a team with metadata"""
        from zephyrex.database.StaticPermissions import is_root_id, is_system_id

        if not (is_root_id(self.requester.id) or is_system_id(self.requester.id)):
            db_session = self.model_registry.DB.session()
            try:
                team_db = TeamModel.DB(self.model_registry.DB.manager.Base)
                team = db_session.query(team_db).filter(team_db.id == id).first()
                if team is None:
                    raise HTTPException(status_code=404, detail="Team not found")
                is_creator = team.created_by_user_id == self.requester.id
                ut_db = UserTeamModel.DB(self.model_registry.DB.manager.Base)
                is_member = (
                    db_session.query(ut_db)
                    .filter(ut_db.team_id == id, ut_db.user_id == self.requester.id)
                    .first()
                    is not None
                )
                if not is_creator and not is_member:
                    raise HTTPException(
                        status_code=403, detail="Not a member of this team"
                    )
            finally:
                db_session.close()

        # Extract metadata fields (non-model fields)
        metadata_fields = {}
        model_fields = {}

        # Get the model fields for comparison
        model_fields_set = set(self.Model.Update.__annotations__.keys())
        # TODO #51 Add fields from mixins dynamically that might not be in annotations
        model_fields_set.add("name")
        model_fields_set.add("parent_id")
        model_fields_set.add("description")
        model_fields_set.add("image_url")
        for key, value in kwargs.items():
            if key in model_fields_set:
                model_fields[key] = value
            else:
                metadata_fields[key] = value

        # Normalize and validate the team name before delegating to the base update logic.
        # This ensures that business rule violations raise ValueError instead of being
        # converted into HTTP exceptions by the abstract manager layer, allowing the
        # calling code and tests to handle them consistently.
        if "name" in model_fields:
            name = model_fields["name"]
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ValueError("Team name cannot be empty")
                model_fields["name"] = normalized_name

        # Update the team
        team = super().update(id, **model_fields)

        # Update metadata if provided
        if metadata_fields and team:
            existing_metadata = self.team_metadata.list(team_id=id)
            existing_metadata_dict = {item.key: item for item in existing_metadata}

            for key, value in metadata_fields.items():
                if key in existing_metadata_dict:
                    # Update existing metadata
                    self.team_metadata.update(
                        id=existing_metadata_dict[key].id,
                        value=str(value),
                    )
                else:
                    # Create new metadata
                    self.team_metadata.create(
                        team_id=id,
                        key=key,
                        value=str(value),
                    )

        return team

    def get_metadata(self) -> Dict[str, str]:
        """Get all metadata for the target team (via metadata extension)."""
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        # Reuse the team manager factory so we get the per-team filtered view.
        factory = _metadata_hooks["team_manager_factory"]
        if factory is None:
            return {}
        with factory(
            requester_id=self.requester.id,
            target_team_id=self.target_team_id,
            model_registry=self.model_registry,
        ) as mgr:
            results = mgr.search({"team_id": {"value": self.target_team_id}}) or []
            return {row.key: row.value for row in results}

    def get_team_users(self, id: str):
        """Get users belonging to a team (custom route method)"""
        result = self.user_teams.list(team_id=id, include=["users"])
        user_manager = UserManager(
            self.requester.id, model_registry=self.model_registry
        )

        for record in result:
            if record.user is None:
                user = user_manager.get(id=record.user_id)
                record.user = user

        return {"user_teams": result}

    def patch_role(self, team_id: str, user_id: str, body: Dict[str, Any]):
        """Update a user's role within a team (custom route method)"""
        return self.user_teams.patch_role(user_id=user_id, team_id=team_id, body=body)

    def revoke_all_invitations(self, team_id: str):
        """Revoke all invitations for a team (nested custom route method)"""
        # Get all invitations for the team
        invitations = self.invitations.list(team_id=team_id)
        invitation_ids = [
            inv.id if hasattr(inv, "id") else inv["id"] for inv in invitations
        ]

        if invitation_ids:
            self.invitations.batch_delete(ids=invitation_ids)

        return {
            "message": f"Revoked {len(invitation_ids)} invitations for team {team_id}"
        }

    def list_invitations_for_team(self, team_id: str):
        """List all invitations for a team (nested custom route method)"""
        invitations = self.invitations.list(team_id=team_id, include=["invitation"])
        invitations_dict = []

        from zephyrex.lib.Pydantic import obj_to_dict

        for invitation in invitations:
            invitation_dict = obj_to_dict(invitation)

            invitees = self.invitations.Invitee_manager.list(
                invitation_id=invitation.id
            )
            invitees_dict = []
            for invitee in invitees:
                invitee_dict = obj_to_dict(invitee)
                invitee_dict["status"] = (
                    "declined"
                    if invitee.declined_at
                    else "accepted" if invitee.accepted_at else "pending"
                )
                invitees_dict.append(invitee_dict)
            if invitees_dict:
                invitation_dict["invitees"] = invitees_dict

            invitations_dict.append(invitation_dict)
        return {"invitations": invitations_dict}


# Unified Metadata Model
# MetadataModel/Manager moved to extension `metadata` (Scope #3).
# Canonical home: zephyrex.extensions.metadata.BLL_Metadata
# Core access goes through `_metadata_hooks` registered via `register_metadata_hooks`.


class RoleModel(
    ApplicationModel,
    ParentMixinModel,
    NameMixinModel,
    UpdateMixinModel,
    TeamModel.Reference.Optional,  # type: ignore[name-defined]
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RoleManager"]] = None  # type: ignore[assignment]
    friendly_name: Optional[str] = Field(None, description="Human-readable role name")
    mfa_count: int = Field(1, description="Number of MFA verifications required")
    password_change_frequency_days: int = Field(
        365, description="How often password must be changed"
    )
    expires_at: Optional[datetime] = Field(None, description="Role expiration date")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Permission roles that define what actions users can perform"
    )

    seed_creator_id: ClassVar[str] = env("TEMPLATE_ID")
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": env("USER_ROLE_ID"),
            "name": "user",
            "friendly_name": "User",
            "parent_id": None,
        },
        {
            "id": env("ADMIN_ROLE_ID"),
            "name": "admin",
            "friendly_name": "Admin",
            "parent_id": env("USER_ROLE_ID"),
        },
        {
            "id": env("SUPERADMIN_ROLE_ID"),
            "name": "superadmin",
            "friendly_name": "Superadmin",
            "parent_id": env("ADMIN_ROLE_ID"),
        },
    ]

    class Create(
        BaseModel,
        NameMixinModel,  # Name is required for creation
        ParentMixinModel.Optional,
        TeamModel.Reference.ID.Optional,  # type: ignore[name-defined]
    ):
        friendly_name: Optional[str] = Field(
            None, description="Human-readable role name"
        )
        mfa_count: Optional[int] = Field(
            1, description="Number of MFA verifications required"
        )
        password_change_frequency_days: Optional[int] = Field(
            365, description="How often password must be changed"
        )

    class Update(BaseModel):  # Removed mixins to make all fields truly optional
        name: Optional[str] = Field(None, description="Role name")
        friendly_name: Optional[str] = Field(
            None, description="Human-readable role name"
        )
        mfa_count: Optional[int] = Field(
            None, description="Number of MFA verifications required"
        )
        password_change_frequency_days: Optional[int] = Field(
            None, description="How often password must be changed"
        )
        parent_id: Optional[str] = Field(None, description="Parent role ID")

    class Search(
        ApplicationModel.Search,
        NameMixinModel.Search,
        ParentMixinModel.Search,
        TeamModel.Reference.ID.Search,  # type: ignore[name-defined]
    ):
        friendly_name: Optional[StringSearchModel] | None = None
        mfa_count: Optional[NumericalSearchModel] | None = None

    create_permission_reference: ClassVar[str] = "resource"

    @classmethod
    def user_can_create(cls, user_id, db, **kwargs):
        """
        Check if a user can create a permission record.
        Users need SHARE permission on the resource they're creating a permission for.
        """
        from zephyrex.database.StaticPermissions import (
            can_manage_permissions,
            is_root_id,
            is_system_user_id,
        )

        # Root and system users can create permissions
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        # Check if user can manage permissions for this resource
        resource_type = kwargs.get("resource_type")
        resource_id = kwargs.get("resource_id")

        if not resource_type or not resource_id:
            return False

        # Check if the user has permission to manage permissions on this resource
        can_manage, _ = can_manage_permissions(user_id, resource_type, resource_id, db)
        return can_manage

    @classmethod
    def user_has_admin_access(
        cls, user_id, id, db, db_manager=None, model_registry=None
    ):
        """
        Overrides the default admin access check for Permission records.
        Allow users with explicit permission to edit this record or with SHARE access to the target resource.
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

        # Root and system users always have admin access
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        # First check standard permission on this record
        result, _ = check_permission(user_id, cls.DB, id, db, PermissionType.EDIT)
        if result == PermissionResult.GRANTED:
            return True

        # If that fails, check if the user can manage permissions for the target resource
        permission = db.query(cls.DB(Base)).filter(cls.DB(Base).id == id).first()
        if permission:
            can_manage, _ = can_manage_permissions(
                user_id, permission.resource_type, permission.resource_id, db
            )
            return can_manage

        return False


class RoleManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = RoleModel
    _entity_label: ClassVar[Optional[str]] = "Role"

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/role"
    tags: ClassVar[Optional[List[str]]] = ["Role Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    auth_dependency: ClassVar[Optional[str]] = "get_role_manager"

    def delete(self, id: str | None = None, **kwargs: Any):
        """Delete a role and reparent its UserTeam assignments.

        Without this override, deleting a role would orphan every
        ``UserTeam`` row referencing it. Behavior:
          - Roles with no active ``UserTeam`` references are deleted as
            usual — there is nothing to orphan.
          - Roles with active references are reparented: each affected
            ``UserTeam`` is updated to the role's ``parent_id`` so
            members fall back to the inherited role.
          - If the role both has active references *and* has no parent
            (a system root role), the delete is refused with 409 —
            silently dropping the assignments would let previously-
            authorized users keep their tokens but lose all permissions.
        """
        target_id = id
        if not target_id:
            return super().delete(id=target_id, **kwargs)

        role = self.DB.get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=target_id,
            return_type="dto",
            override_dto=RoleModel,
        )
        if role is None:
            return super().delete(id=target_id, **kwargs)

        ut_db = UserTeamModel.DB(self.model_registry.DB.manager.Base)
        affected = ut_db.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            role_id=target_id,
            return_type="dto",
            override_dto=UserTeamModel,
        )
        if affected:
            if role.parent_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Refusing to delete a root role that still has "
                        f"{len(affected)} UserTeam assignment(s): every "
                        "such row would be orphaned. Move members off "
                        "this role first, or delete a child role with a "
                        "defined parent."
                    ),
                )
            ut_manager = UserTeamManager(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
            )
            for ut in affected:
                ut_manager.update(id=ut.id, role_id=role.parent_id)

        return super().delete(id=target_id, **kwargs)

    def _register_search_transformers(self):
        self.register_search_transformer("is_system", self._transform_is_system_search)

    def _transform_is_system_search(self, value):
        """Transform is_system search to filter system roles (team_id is NULL)"""
        if value:
            return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id == None]
        return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id != None]

    def get(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a role with optional included relationships. Returns 404 if not found."""
        role = super().get(include=include, fields=fields, **kwargs)

        # Handle both dict and object access patterns (dict when fields is specified)
        created_by_user_id = (
            role.get("created_by_user_id")
            if isinstance(role, dict)
            else role.created_by_user_id
        )
        team_id = role.get("team_id") if isinstance(role, dict) else role.team_id

        if created_by_user_id != self.requester.id:
            # Business logic validation: if accessing a team-specific role, validate team membership
            if team_id:
                self.validate_user_team(self.requester.id, team_id)

        return role

    def validate_user_team(self, user_id: str, team_id: str):
        """
        Validate that the user has exactly one UserTeam relationship with the specified team.
        This is business logic validation, not permission validation.
        """
        # Use the UserTeamManager class defined later in this file instead of importing it
        user_team_manager = UserTeamManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )

        # Check that user has exactly one UserTeam relationship with this team
        # Use the database class directly to avoid parameter conflicts
        user_teams = UserTeamModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=user_id,
            team_id=team_id,
        )

        if len(user_teams) == 0:
            raise HTTPException(
                status_code=403,
                detail="Access denied",
            )
        elif len(user_teams) > 1:
            raise HTTPException(
                status_code=409,
                detail="Request uncovered multiple UserTeam when only one was expected.",
            )

    def create_validation(self, entity):
        """Validate role creation."""
        # First, validate that team_id is provided (required for user-created roles)
        # System roles with team_id=None can only be created through seeding, not the API
        if entity.team_id is None:
            raise HTTPException(
                status_code=422,
                detail="team_id is required for role creation",
            )

        # Second, check if team exists (use ROOT_ID to bypass permission checks)
        # This ensures we return 404 only for genuinely non-existent teams,
        # not for teams the user can't access (which should return 403 later)
        if entity.team_id:
            team = TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                id=entity.team_id,
            )
            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

        # Third, check if parent role exists and is accessible
        if entity.parent_id:
            try:
                parent_role = self.DB.get(
                    requester_id=self.requester.id,
                    model_registry=self.model_registry,
                    id=entity.parent_id,
                )
                if not parent_role:
                    raise HTTPException(status_code=404, detail="Parent role not found")
            except HTTPException:
                raise HTTPException(status_code=404, detail="Parent role not found")

        # Finally, validate user-team relationship (business logic, not permissions)
        # Only validate if team_id is provided and not null
        if entity.team_id:
            self.validate_user_team(self.requester.id, entity.team_id)

    def search_validation(self, params):
        """Validate search parameters for business logic rules"""
        if "team_id" in params:
            if params["team_id"] in [None, "", "None"]:
                raise HTTPException(status_code=400, detail="Team ID cannot be None")


class UserTeamModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,  # type: ignore[name-defined]
    TeamModel.Reference,  # type: ignore[name-defined]
    RoleModel.Reference,  # type: ignore[name-defined]
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserTeamManager"]] = None  # type: ignore[assignment]
    enabled: bool = Field(True, description="Whether this membership is enabled")
    expires_at: Optional[datetime] = Field(
        None, description="When this membership expires"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Junction table linking users to teams with assigned roles"
    )

    class Create(
        BaseModel,
        UserModel.Reference.ID,  # type: ignore[name-defined]
        TeamModel.Reference.ID,  # type: ignore[name-defined]
        RoleModel.Reference.ID,  # type: ignore[name-defined]
    ):
        enabled: Optional[bool] = Field(
            True, description="Whether this membership is enabled"
        )

    class Update(BaseModel):
        role_id: Optional[str] = Field(
            None, description="Role ID assigned to the user in this team"
        )
        enabled: Optional[bool] = Field(
            None, description="Whether this membership is enabled"
        )

    class Patch(BaseModel):
        role_id: str = Field(  # type: ignore[assignment]
            None, description="Role ID to be assigned to the user in this team"
        )

    class Search(
        ApplicationModel.Search,
        UserModel.Reference.ID.Search,  # type: ignore[name-defined]
        TeamModel.Reference.ID.Search,  # type: ignore[name-defined]
        RoleModel.Reference.ID.Search,  # type: ignore[name-defined]
    ):
        enabled: Optional[bool] | None = None

    @classmethod
    def user_has_read_access(
        cls,
        user_id,
        team_id,
        db,
        minimum_role=None,
        referred=False,
        db_manager=None,
        model_registry=None,
    ):
        """
        Custom read access logic for user team records:
        Users can see user team record if they belong to the team.

        Args:
            user_id: The ID of the user requesting access
            team_id: The ID of the team that the user should belong to
            db: Database session
            minimum_role: Minimum role required (if applicable)
            referred: Whether this check is part of a referred access check
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if access is granted, False otherwise
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            # For backward compatibility, if neither is provided, we'll need it later
            Base = None
        from zephyrex.database.StaticPermissions import is_root_id, is_system_user_id

        # ROOT_ID can access everything
        if is_root_id(user_id):
            return True

        # SYSTEM_ID can access most things
        if is_system_user_id(user_id):
            return True

        if Base is None and db_manager:
            Base = db_manager.Base

        record = (
            db.query(cls.DB(Base))
            .filter(
                cls.DB(Base).user_id == user_id,
                cls.DB(Base).team_id == team_id,
            )
            .first()
        )
        if record is None:
            return False

        if hasattr(record, "deleted_at") and record.deleted_at is not None:
            return is_root_id(user_id)

        return True

    @classmethod
    def user_has_admin_access(
        cls, user_id, team_id, db, db_manager=None, model_registry=None
    ):
        """
        Overrides the default admin access check for UserTeam records with better error handling.
        Checks if the user is an admin in the team.

        Args:
            user_id: The ID of the user requesting access
            team_id: The ID of the team that the user should belong to
            db: Database session
            db_manager: Database manager instance (deprecated)
            model_registry: Model registry instance (preferred)

        Returns:
            bool: True if access is granted, False otherwise

        Raises:
            ValueError: If neither model_registry nor db_manager is provided
            Exception: If there are database access issues
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")

        from zephyrex.database.StaticPermissions import is_root_id, is_system_user_id
        from zephyrex.lib.Logging import logger

        # Root and system users always have admin access
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        try:
            # Query for the specific user-team relationship
            user_team = (
                db.query(cls.DB(Base))
                .filter(
                    cls.DB(Base).user_id == user_id,
                    cls.DB(Base).team_id == team_id,
                )
                .first()
            )

            if user_team is None:
                logger.warning(
                    f"No UserTeam relationship found for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership is deleted
            if hasattr(user_team, "deleted_at") and user_team.deleted_at is not None:
                logger.warning(
                    f"UserTeam relationship is deleted for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership is enabled
            if hasattr(user_team, "enabled") and not user_team.enabled:
                logger.warning(
                    f"UserTeam relationship is disabled for user_id={user_id}, team_id={team_id}"
                )
                return False

            # Check if membership has expired
            if hasattr(user_team, "expires_at") and user_team.expires_at:
                from datetime import datetime

                if datetime.utcnow() > user_team.expires_at:
                    logger.warning(
                        f"UserTeam relationship has expired for user_id={user_id}, team_id={team_id}"
                    )
                    return False

            admin_role_id = env("ADMIN_ROLE_ID")
            is_admin = user_team.role_id == admin_role_id

            logger.debug(
                f"Admin access check: user_id={user_id}, team_id={team_id}, "
                f"role_id={user_team.role_id}, admin_role_id={admin_role_id}, is_admin={is_admin}"
            )

            return is_admin

        except Exception as e:
            logger.error(
                f"Database error during admin access check for user_id={user_id}, team_id={team_id}: {str(e)}"
            )
            # Re-raise the exception to be handled by the caller
            raise


class UserTeamManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = UserTeamModel
    _entity_label: ClassVar[Optional[str]] = "User Team"

    def get(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a user-team with optional included relationships."""
        result = super().get(include=include, fields=fields, **kwargs)

        # Only check permissions after confirming the record exists
        if "team_id" in kwargs:
            if not self.DB.user_has_read_access(
                self.requester.id, kwargs.get("team_id"), self.db
            ):
                raise HTTPException(status_code=403, detail="get - not permissable")

        return result

    def search(
        self,
        include: Optional[List[str]] | None = None,
        fields: Optional[List[str]] | None = None,
        sort_by: Optional[str] | None = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] | None = None,
        limit: Optional[int] | None = None,
        offset: Optional[int] | None = None,
        page: Optional[int] | None = None,
        pageSize: Optional[int] | None = None,
        **search_params,
    ) -> List[Any]:
        records = super().search(
            include=include,
            fields=fields,
            sort_by=sort_by,
            sort_order=sort_order,
            filters=filters,
            limit=limit,
            offset=offset,
            page=page,
            pageSize=pageSize,
            **search_params,
        )

        if not records:
            return records  # type: ignore[no-any-return]

        def _get_attr(record, attr):
            if isinstance(record, dict):
                return record.get(attr)
            return getattr(record, attr, None)

        def _set_attr(record, attr, value):
            if isinstance(record, dict):
                record[attr] = value
            else:
                setattr(record, attr, value)

        team_ids = {
            team_id
            for team_id in (_get_attr(record, "team_id") for record in records)
            if team_id
        }
        role_ids = {
            role_id
            for role_id in (_get_attr(record, "role_id") for record in records)
            if role_id
        }

        team_map: Dict[str, Any] = {}
        role_map: Dict[str, Any] = {}

        if team_ids:
            team_manager = TeamManager(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
            )
            teams = team_manager.list(filters=[team_manager.DB.id.in_(team_ids)])
            team_map = {team.id: team for team in teams}

        if role_ids:
            role_manager = RoleManager(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
            )
            roles = role_manager.list(filters=[role_manager.DB.id.in_(role_ids)])
            role_map = {role.id: role for role in roles}

        for record in records:
            team_id = _get_attr(record, "team_id")
            if team_id and team_id in team_map:
                _set_attr(record, "team", team_map[team_id])

            role_id = _get_attr(record, "role_id")
            if role_id and role_id in role_map:
                _set_attr(record, "role", role_map[role_id])

        return records  # type: ignore[no-any-return]

    def update(
        self, id: str, team_id: str | None = None, db=None, db_manager=None, **kwargs
    ):
        """Update user team record with improved error handling"""
        db = db or self.db

        # Ensure db_manager is set
        if db_manager is None:
            # Try to get from model_registry if available
            if hasattr(self, "model_registry") and hasattr(self.model_registry, "DB"):
                db_manager = getattr(self.model_registry.DB, "manager", None)
        if db_manager is None:
            raise RuntimeError(
                "db_manager is required for permission checks but was not provided or found."
            )

        if team_id is not None:
            # First check if the requester is a member of the team at all
            try:
                user_team_membership = (
                    db.query(self.Model.DB(db_manager.Base))
                    .filter(
                        self.Model.DB(db_manager.Base).user_id == self.requester.id,
                        self.Model.DB(db_manager.Base).team_id == team_id,
                    )
                    .first()
                )
            except Exception as e:
                from zephyrex.lib.Logging import logger

                logger.error(
                    f"Database error checking team membership for user {self.requester.id} in team {team_id}: {str(e)}"
                )
                raise HTTPException(
                    status_code=500,
                    detail="Internal error while checking team membership",
                )

            if user_team_membership is None:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: You must be a member of team '{team_id}' to modify user roles",
                )

            # Check if user has deleted membership
            if (
                hasattr(user_team_membership, "deleted_at")
                and user_team_membership.deleted_at is not None
            ):
                from zephyrex.database.StaticPermissions import is_root_id

                if not is_root_id(self.requester.id):
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied: Your team membership has been revoked",
                    )

            # Check admin access using the existing method signature
            try:
                has_admin_access = self.DB.user_has_admin_access(
                    self.requester.id,
                    team_id,
                    db,
                    db_manager=db_manager,  # Only pass db_manager, not model_registry
                )
            except Exception as e:
                # Log the specific error for debugging
                from zephyrex.lib.Logging import logger

                logger.error(
                    f"Error checking admin access for user {self.requester.id} in team {team_id}: {str(e)}"
                )
                raise HTTPException(
                    status_code=500, detail="Internal error while checking permissions"
                )

            if not has_admin_access:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied: You must have administrator privileges in this team to modify user roles",
                )

        return super().update(id, **kwargs)

    def validate(self, user_id: str, team_id: str, body: Dict[str, str]):
        try:
            UserModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=user_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched UserModel and could not find the required record.",
            )

        try:
            TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=team_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched TeamModel and could not find the required record.",
            )

        role_id = body["user_team"]["role_id"]  # type: ignore[index]
        try:
            RoleModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=role_id,
            )
        except Exception:
            raise HTTPException(
                status_code=404,
                detail="Request searched RoleModel and could not find the required record.",
            )

    def patch_role(self, user_id: str, team_id: str, body: Dict[str, str]):

        self.validate(user_id=user_id, team_id=team_id, body=body)

        # Find the UserTeam record by user_id and team_id
        user_team_list = self.list(team_id=team_id, user_id=user_id)
        if not user_team_list:
            raise HTTPException(
                status_code=404,
                detail=f"User Team with ID 'user_id={user_id}, team_id={team_id}' not found",
            )

        target_user_team = user_team_list[0]

        target_role_id = body["user_team"]["role_id"]  # type: ignore[index]
        updated_data = {"role_id": target_role_id}

        self.update(id=target_user_team.id, team_id=team_id, **updated_data)

        return {"message": "Role updated successfully"}


class RateLimitPolicyModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RateLimitPolicyManager"]] = None  # type: ignore[assignment]
    resource_pattern: str = Field(..., description="Resource pattern to match")
    window_seconds: int = Field(..., description="Time window in seconds")
    max_requests: int = Field(..., description="Maximum requests in time window")
    scope: str = Field(..., description="Scope of rate limiting (user, ip, global)")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Rate limiting policies for API endpoints and resources"
    )
    is_system_entity: ClassVar[bool] = True
    seed_creator_id: ClassVar[str] = env("SYSTEM_ID")

    class Create(BaseModel, NameMixinModel):
        resource_pattern: str = Field(..., description="Resource pattern to match")
        window_seconds: int = Field(..., description="Time window in seconds")
        max_requests: int = Field(..., description="Maximum requests in time window")
        scope: str = Field(..., description="Scope of rate limiting (user, ip, global)")

    class Update(BaseModel):
        name: Optional[str] = Field(None, description="Policy name")
        resource_pattern: Optional[str] = Field(
            None, description="Resource pattern to match"
        )
        window_seconds: Optional[int] = Field(
            None, description="Time window in seconds"
        )
        max_requests: Optional[int] = Field(
            None, description="Maximum requests in time window"
        )
        scope: Optional[str] = Field(
            None, description="Scope of rate limiting (user, ip, global)"
        )

    class Search(ApplicationModel.Search, NameMixinModel.Search):
        resource_pattern: Optional[StringSearchModel] | None = None
        window_seconds: Optional[NumericalSearchModel] | None = None
        max_requests: Optional[NumericalSearchModel] | None = None
        scope: Optional[StringSearchModel] | None = None


class RateLimitPolicyManager(AbstractBLLManager, RouterMixin):  # type: ignore[no-redef]
    _model = RateLimitPolicyModel


# Set up Model.Manager relationships for classes still defined in core.
# Extensions wire their own (see auth_recovery_questions, auth_lockout,
# auth_session, metadata, auth_invitations).
UserModel.Manager = UserManager
UserCredentialModel.Manager = UserCredentialManager
TeamModel.Manager = TeamManager
RoleModel.Manager = RoleManager
UserTeamModel.Manager = UserTeamManager
RateLimitPolicyModel.Manager = RateLimitPolicyManager

# Symbols still anchored in core. Extracted classes (UserRecoveryQuestion*,
# FailedLoginAttempt*, SessionModel/SessionManager, Metadata*, Invitation*,
# Invitee*, Permission*) are resolved lazily through the PEP 562 ``__getattr__``
# shim above, which forwards to the relevant extension when loaded and raises a
# typed migration error otherwise. Listing them in ``__all__`` would force eager
# imports and break the lazy-loading contract.
__all__ = [
    "UserModel",
    "UserManager",
    "UserCredentialModel",
    "UserCredentialManager",
    "TeamModel",
    "TeamManager",
    "RoleModel",
    "RoleManager",
    "UserTeamModel",
    "UserTeamManager",
    "RateLimitPolicyModel",
    "RateLimitPolicyManager",
]
