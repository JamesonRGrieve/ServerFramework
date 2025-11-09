import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Type

if TYPE_CHECKING:
    from logic.BLL_Auth import (
        UserManager,
        UserCredentialManager,
        UserRecoveryQuestionManager,
        FailedLoginAttemptManager,
        TeamManager,
        RoleManager,
        UserTeamManager,
        PermissionManager,
        InvitationManager,
        InviteeManager,
        RateLimitPolicyManager,
        SessionManager,
    )

import bcrypt
from fastapi import Header, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_

from database.StaticPermissions import can_manage_permissions
from lib.Dependencies import jwt
from lib.Environment import env, extract_base_domain
from lib.Logging import logger
from lib.Pydantic import BaseModel
from lib.Pydantic2FastAPI import (
    AuthType,
    RequestInfo,
    RouterMixin,
    RouteType,
    static_route,
)
from logic.AbstractLogicManager import (
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


class UserModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ImageMixinModel.Optional,
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore", "populate_by_name": True}
    Manager: ClassVar[Type["UserManager"]] = None
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
        from database.StaticPermissions import (
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
        from database.StaticPermissions import (
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
        from database.StaticPermissions import (
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
            if "@" not in self.email:
                raise ValueError("Invalid email format")
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
        email: Optional[StringSearchModel] = None
        username: Optional[StringSearchModel] = None
        display_name: Optional[StringSearchModel] = None
        first_name: Optional[StringSearchModel] = None
        last_name: Optional[StringSearchModel] = None
        active: Optional[bool] = None
        timezone: Optional[str] = None
        language: Optional[str] = None


class UserManager(AbstractBLLManager, RouterMixin):
    _model = UserModel

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
                __import__("logic.BLL_Auth", fromlist=["UserTeamManager"]),
                "UserTeamManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "metadata": {
            "child_resource_name": "metadata",
            "manager_property": "metadata",
            "child_manager_class": lambda: getattr(
                __import__("logic.BLL_Auth", fromlist=["UserMetadataManager"]),
                "UserMetadataManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "session": {
            "child_resource_name": "session",
            "manager_property": "sessions",
            "child_manager_class": lambda: getattr(
                __import__("logic.BLL_Auth", fromlist=["SessionManager"]),
                "SessionManager",
            ),
            # child_network_model_cls will be inferred from the manager
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
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
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

        search_value = f"%{value}%"
        db_model = self.DB
        return [
            or_(
                db_model.first_name.ilike(search_value),
                db_model.last_name.ilike(search_value),
                db_model.display_name.ilike(search_value),
                db_model.username.ilike(search_value),
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
            self._metadata = UserMetadataManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._metadata

    @property
    def failed_logins(self):
        if self._failed_logins is None:
            self._failed_logins = FailedLoginAttemptManager(
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
        if self._sessions is None:
            self._sessions = SessionManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                model_registry=self.model_registry,
            )
        return self._sessions

    @property
    def sessions(self):
        if not hasattr(self, "_sessions") or self._sessions is None:
            self._sessions = SessionManager(
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
        user_id: str, email: str, timezone_str: str = "UTC", expiration_hours: int = 24
    ) -> str:
        """Generate a JWT token for authentication"""
        expiration = datetime.now(timezone.utc) + timedelta(hours=expiration_hours)
        payload = {
            "sub": user_id,
            "email": email,
            "timezone": timezone_str,
            "exp": expiration,
            "iat": datetime.now(timezone.utc),
        }
        return jwt.encode(payload, env("JWT_SECRET"), algorithm="HS256")

    @staticmethod
    def verify_token(
        token: str,
        model_registry=None,
    ) -> Dict[str, Any]:
        """Verify a JWT token and return user information"""
        if model_registry is None:
            raise ValueError("model_registry is required for verify_token")

        try:
            payload = jwt.decode(token, env("JWT_SECRET"), algorithms=["HS256"])

            user = UserModel.DB(model_registry.DB.manager.Base).get(
                requester_id=env("ROOT_ID"),
                model_registry=model_registry,
                id=payload["sub"],
                return_type="dto",
                override_dto=UserModel,
            )

            if not user.active:
                raise HTTPException(status_code=401, detail="Inactive user")

            return {"id": user.id, "email": user.email}
        except Exception as e:
            raise HTTPException(
                status_code=401, detail=f"Token verification failed: {str(e)}"
            )

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")
        except Exception as e:
            raise HTTPException(
                status_code=401, detail=f"Token verification failed: {str(e)}"
            )

    @staticmethod
    def auth(
        model_registry,
        authorization: str = Header(None),
        request: Dict = None,
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
            return None

        if not authorization:
            raise HTTPException(
                status_code=401, detail="Authorization header is missing!"
            )

        ip = None
        server = None
        if request:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                ip = forwarded_for.split(",")[0].strip()
            elif request.client:
                if hasattr(request.client, "host"):
                    # Named tuple or object with host attribute
                    ip = request.client.host
                elif (
                    isinstance(request.client, (tuple, list))
                    and len(request.client) > 0
                ):
                    # Plain tuple/list (host, port)
                    ip = request.client[0]
                elif isinstance(request.client, dict) and "host" in request.client:
                    # Dictionary format
                    ip = request.client["host"]
                else:
                    ip = None
        host = request.headers.get("Host")
        scheme = request.headers.get("X-Forwarded-Proto", "http")
        server = None
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

                # Check if this is API key authentication (X-API-Key header present)
                # Only process as API key if X-API-Key header is present in the request
                if (
                    request
                    and request.headers.get("X-API-Key")
                    and token == env("ROOT_API_KEY")
                ):
                    return (
                        db.query(UserModel.DB(db_manager.Base))
                        .filter(UserModel.DB(db_manager.Base).id == env("SYSTEM_ID"))
                        .first()
                    )

                try:
                    # Regular JWT auth
                    payload = jwt.decode(
                        jwt=token,
                        key=env("JWT_SECRET"),
                        algorithms=["HS256"],
                        leeway=timedelta(minutes=5),
                        i=ip,
                        s=server,
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

                    return user
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
                            change_date = old_credentials.password_changed_at.strftime(
                                "%Y-%m"
                            )
                            raise HTTPException(
                                status_code=401,
                                detail=f"Your password was changed during {change_date}.",
                            )
                        else:
                            raise HTTPException(
                                status_code=401, detail="Invalid credentials"
                            )

                    return user
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
        """Get all metadata for the target user"""
        metadata_items = MetadataModel.DB(self.model_registry.DB.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=self.target_user_id,
        )
        return {item.key: item.value for item in metadata_items}

    def revoke_all_user_sessions(self, user_id: str):
        """Revoke all sessions for a user (nested custom route method)"""
        return self.sessions.revoke_all_user_sessions(user_id=user_id)

    # Login-specific models (not part of the main entity model system)
    class UserLoginModel(BaseModel):
        email: str = Field(..., description="User's email or username")
        password: str = Field(..., description="User's password")

    @staticmethod
    def login(
        login_data: Dict[str, Any] = None,
        ip_address: str = None,
        req_uri: Optional[str] = None,
        authorization: Optional[str] = None,
        model_registry=None,
    ) -> Dict[str, Any]:
        """Process user login from various input methods"""
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

            login_model = UserManager.UserLoginModel(**login_data)
            normalized_identifier = login_model.email.lower().strip()

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
                raise HTTPException(status_code=401, detail="Invalid credentials.")

            user = user[0]

            # Check for too many failed login attempts
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            failed_login_count = FailedLoginAttemptModel.DB(
                model_registry.DB.manager.Base
            ).count(
                requester_id=user["id"],
                model_registry=model_registry,
                user_id=user["id"],
                filters=[
                    FailedLoginAttemptModel.DB(
                        model_registry.DB.manager.Base
                    ).created_at
                    >= one_hour_ago
                ],
            )

            max_failed_attempts = 5
            if failed_login_count >= max_failed_attempts:
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed login attempts. Please try again later.",
                )

            # Check if user account is active
            if not user["active"]:
                FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).create(
                    requester_id=user["id"],
                    model_registry=model_registry,
                    user_id=user["id"],
                    ip_address=ip_address,
                )
                raise HTTPException(status_code=401, detail="Invalid credentials")

            # Check if user account was deleted
            # TODO: This is a temporary fix to block users from logging in after they have been deleted but the DB layer should handle this
            if user["deleted_at"]:
                FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).create(
                    requester_id=user["id"],
                    model_registry=model_registry,
                    user_id=user["id"],
                    ip_address=ip_address,
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
                        change_date = old_credentials.password_changed_at.strftime(
                            "%Y-%m"
                        )
                        raise HTTPException(
                            status_code=401,
                            detail=f"Your password was changed during {change_date}.",
                        )
                    else:
                        FailedLoginAttemptModel.DB(
                            model_registry.DB.manager.Base
                        ).create(
                            requester_id=user["id"],
                            model_registry=model_registry,
                            user_id=user["id"],
                            ip_address=ip_address,
                        )
                        raise HTTPException(
                            status_code=401, detail="Invalid credentials"
                        )

            else:
                raise HTTPException(
                    status_code=400, detail="Either password or token is required"
                )

            # Login successful - generate JWT token
            user_timezone = (
                user.get("timezone", "UTC")
                if isinstance(user, dict)
                else getattr(user, "timezone", "UTC")
            )
            token = UserManager.generate_jwt_token(
                user_id=str(user["id"]), email=user["email"], timezone_str=user_timezone
            )

            # Create session
            session_key = secrets.token_hex(16)
            SessionModel.DB(model_registry.DB.manager.Base).create(
                requester_id=user["id"],
                model_registry=model_registry,
                user_id=user["id"],
                session_key=session_key,
                jwt_issued_at=datetime.now(timezone.utc),
                device_type="web",
                browser="unknown",
                is_active=True,
                last_activity=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                revoked=False,
                trust_score=50,
            )

            # Get user preferences
            preferences = {}
            try:
                metadata_items = MetadataModel.DB(model_registry.DB.Base).list(
                    requester_id=root_id,
                    model_registry=model_registry,
                    user_id=user["id"],
                )
                preferences = {item.key: item.value for item in metadata_items}
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

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a user with optional included relationships."""
        options = []

        fields = self.validate_fields(fields)

        # TODO Move generate_joins to AbstractDatabaseEntity.py
        if include:
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)

        if "team_id" in kwargs:
            if not self.DB.user_has_read_access(
                self.requester.id, kwargs.get("team_id"), self.db
            ):
                raise HTTPException(status_code=403, detail="get - not permissable")

        result = self.DB.get(
            requester_id=self.requester.id,
            fields=fields,
            model_registry=self.model_registry,
            return_type="dto" if not fields else "dict",
            override_dto=self.Model if not fields else None,
            options=options,
            **kwargs,
        )

        if result is None:
            team_id = kwargs.get("team_id") or kwargs.get("user_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User Team with ID '{team_id}' not found",
            )

        return result

    def get_current_user(self, fields: Optional[List[str]] = None):
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

    def delete(self, id: str = None):
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
    @static_route("", method="POST", auth_type=AuthType.NONE, status_code=201)
    def register(
        registration_data: dict,
        model_registry,
        request: Request = None,
        authorization: Optional[str] = None,
    ) -> dict:
        """
        Register a new user with the provided data.
        Handles validation, creation, metadata, credentials, and invitation acceptance.
        Accepts either email+password in body OR Basic Auth header (mutually exclusive).
        """
        if model_registry is None:
            raise ValueError("model_registry is required for register")

        # Check registration mode
        from lib.Environment import settings

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
            email = email_from_body
            password = password_from_body

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

        # Handle invitation acceptance scenarios
        if invitation_id:
            try:
                # Direct email invite - validate invitation_id exists
                invitation = (
                    model_registry.DB.session()
                    .query(InvitationModel.DB(model_registry.DB.manager.Base))
                    .filter(
                        InvitationModel.DB(model_registry.DB.manager.Base).id
                        == invitation_id,
                        InvitationModel.DB(
                            model_registry.DB.manager.Base
                        ).deleted_at.is_(None),
                    )
                    .first()
                )

                if invitation:
                    # Check if invitation has expired
                    if invitation.expires_at and invitation.expires_at < datetime.now(
                        timezone.utc
                    ):
                        logger.warning(f"Invitation ID {invitation_id} has expired")
                        invitation_details = None
                    else:
                        invitation_details = {
                            "id": invitation.id,
                            "code": invitation.code,
                            "team_id": invitation.team_id,
                            "role_id": invitation.role_id,
                            "acceptance_type": "direct_email_invite",
                        }
                else:
                    logger.warning(
                        f"Invalid invitation ID during user registration: {invitation_id}"
                    )
                    invitation_details = None
            except Exception as e:
                logger.error(
                    f"Error validating invitation ID during user registration: {str(e)}"
                )
                invitation_details = None
        elif invitation_code:
            try:
                # Public invitation code - validate invitation code exists
                invitation = (
                    model_registry.DB.session()
                    .query(InvitationModel.DB(model_registry.DB.manager.Base))
                    .filter(
                        InvitationModel.DB(model_registry.DB.manager.Base).code
                        == invitation_code,
                        InvitationModel.DB(
                            model_registry.DB.manager.Base
                        ).deleted_at.is_(None),
                    )
                    .first()
                )

                if invitation:
                    logger.debug(
                        f"DEBUG: Found invitation with code {invitation_code}, team_id={invitation.team_id}"
                    )
                    # Check if invitation has expired
                    if invitation.expires_at and invitation.expires_at < datetime.now(
                        timezone.utc
                    ):
                        logger.warning(f"Invitation code {invitation_code} has expired")
                        invitation_details = None
                    else:
                        invitation_details = {
                            "id": invitation.id,
                            "code": invitation.code,
                            "team_id": invitation.team_id,
                            "role_id": invitation.role_id,
                            "acceptance_type": "public_code",
                        }
                        logger.debug(
                            f"DEBUG: Created invitation_details with team_id={invitation_details['team_id']}"
                        )
                else:
                    logger.warning(
                        f"Invalid invitation code during user registration: {invitation_code}"
                    )
                    invitation_details = None
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

        # Create metadata if provided
        if metadata_fields and user:
            metadata_manager = UserMetadataManager(
                requester_id=root_id,
                target_id=user.id,
                model_registry=model_registry,
            )
            for key, value in metadata_fields.items():
                metadata_manager.create(
                    user_id=user.id,
                    key=key,
                    value=str(value),
                )

        # Create credentials for the user
        credentials_manager = UserCredentialManager(
            requester_id=root_id,
            target_id=user.id,
            model_registry=model_registry,
        )
        credentials_manager.create(user_id=user.id, password=password)

        # Handle invitation acceptance if invitation details were validated
        if invitation_details:
            try:
                user_email = user.email.lower()

                # Check for existing invitee record
                invitee_manager = InviteeManager(
                    requester_id=root_id,
                    model_registry=model_registry,
                )

                existing_invitees = invitee_manager.list(
                    invitation_id=invitation_details["id"], email=user_email
                )

                if existing_invitees and len(existing_invitees) > 0:
                    # Update existing invitee record
                    invitee = existing_invitees[0]
                    invitee_manager.update(
                        id=invitee.id,
                        accepted_at=datetime.now(timezone.utc),
                        user_id=user.id,
                    )
                else:
                    # Create a new invitee record for this acceptance
                    invitee_manager.create(
                        invitation_id=invitation_details["id"],
                        email=user_email,
                        accepted_at=datetime.now(timezone.utc),
                        user_id=user.id,
                    )

                # Add user to team if this is a team invitation
                if invitation_details["team_id"] and invitation_details["role_id"]:
                    logger.debug(
                        f"Adding user {user.id} to team {invitation_details['team_id']} with role {invitation_details['role_id']}"
                    )
                    user_team_manager = UserTeamManager(
                        requester_id=root_id,
                        target_id=user.id,
                        model_registry=model_registry,
                    )

                    # Check for existing team membership
                    existing_memberships = user_team_manager.list(
                        user_id=user.id,
                        team_id=invitation_details["team_id"],
                    )
                    logger.debug(
                        f"Found {len(existing_memberships) if existing_memberships else 0} existing memberships"
                    )

                    if existing_memberships:
                        # Update existing team membership
                        logger.debug(
                            f"Updating existing membership {existing_memberships[0].id}"
                        )
                        user_team_manager.update(
                            id=existing_memberships[0].id,
                            role_id=invitation_details["role_id"],
                            enabled=True,
                        )
                    else:
                        # Create new team membership
                        logger.debug(f"Creating new team membership")
                        user_team_manager.create(
                            user_id=user.id,
                            team_id=invitation_details["team_id"],
                            role_id=invitation_details["role_id"],
                            enabled=True,
                        )

                # Add invitation acceptance details to user metadata
                metadata_manager = UserMetadataManager(
                    requester_id=root_id,
                    target_id=user.id,
                    model_registry=model_registry,
                )
                metadata_manager.create(
                    user_id=user.id,
                    key="invitation_accepted",
                    value="true",
                )
                metadata_manager.create(
                    user_id=user.id,
                    key="invitation_code",
                    value=invitation_details["code"],
                )
                if invitation_details["team_id"]:
                    logger.debug(
                        f"DEBUG: Storing invitation_team_id={invitation_details['team_id']} for user {user.id}"
                    )
                    metadata_manager.create(
                        user_id=user.id,
                        key="invitation_team_id",
                        value=str(invitation_details["team_id"]),
                    )

                logger.debug(
                    f"User {user.id} successfully accepted invitation {invitation_details['code']} during registration"
                )

            except Exception as e:
                # Log the error but don't fail user creation
                logger.error(
                    f"Failed to accept invitation during user creation: {str(e)}"
                )

        return user

    def list_invitations_for_user(self):
        """List all invitations for a team (nested custom route method)"""

        invitation_manager = InvitationManager(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
        )

        invitations = invitation_manager.list(include=["invitation"])
        invitations_dict = []

        user_id = self.requester.id
        user = self.get(id=user_id)

        from lib.Pydantic import obj_to_dict

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
    UserModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserCredentialManager"]] = None
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
        password: str = Field(None, description="New password (will be hashed)")

    class Update(BaseModel):
        # This model and entity should not be manually updatable, only via the User password change function.
        # However, we need to allow updating the password_changed_at field for tests
        password_changed_at: Optional[datetime] = None

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        password_changed_at: Optional[DateSearchModel] = None


class UserCredentialManager(AbstractBLLManager, RouterMixin):
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
        salt = bcrypt.gensalt()
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
                salt = bcrypt.gensalt()
                kwargs["password_hash"] = bcrypt.hashpw(
                    password.encode(), salt
                ).decode()
                kwargs["password_salt"] = salt.decode()

        return super().update(id, **kwargs)

    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> Dict[str, str]:
        """Change a user's password with verification"""
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


class UserRecoveryQuestionModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserRecoveryQuestionManager"]] = None
    question: str = Field(..., description="Recovery question")
    answer: str = Field(..., description="Hashed answer to recovery question")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Security questions for account recovery when a user forgets their password"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        question: str = Field(..., description="Recovery question")
        answer: str = Field(
            ..., description="Answer to recovery question (will be hashed)"
        )

    class Update(BaseModel):
        question: Optional[str] = Field(None, description="Recovery question")
        answer: Optional[str] = Field(
            None, description="Answer to recovery question (will be hashed)"
        )

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        question: Optional[StringSearchModel] = None


class UserRecoveryQuestionManager(AbstractBLLManager, RouterMixin):
    _model = UserRecoveryQuestionModel

    def create(self, **kwargs):
        """Create a recovery question with hashed answer"""
        if "answer" in kwargs:
            answer = kwargs.pop("answer")
            normalized_answer = answer.lower().strip()
            salt = bcrypt.gensalt()
            kwargs["answer"] = bcrypt.hashpw(normalized_answer.encode(), salt).decode()

        return super().create(**kwargs)

    def update(self, id: str, **kwargs):
        """Update a recovery question with hashed answer"""
        if "answer" in kwargs:
            answer = kwargs.pop("answer")
            normalized_answer = answer.lower().strip()
            salt = bcrypt.gensalt()
            kwargs["answer"] = bcrypt.hashpw(normalized_answer.encode(), salt).decode()

        return super().update(id, **kwargs)

    def verify_answer(self, question_id: str, answer: str) -> bool:
        """Verify a recovery question answer"""
        question = UserRecoveryQuestionModel.DB(
            self.model_registry.DB.manager.Base
        ).get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=question_id,
        )

        if not question:
            return False

        normalized_answer = answer.lower().strip()
        return bcrypt.checkpw(normalized_answer.encode(), question.answer.encode())


class FailedLoginAttemptModel(
    ApplicationModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["FailedLoginAttemptManager"]] = None
    ip_address: Optional[str] = Field(
        None, description="IP address of failed login attempt"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Records of failed login attempts for security monitoring and lockout enforcement"
    )

    class Create(BaseModel, UserModel.Reference.ID):
        ip_address: Optional[str] = Field(
            None, description="IP address of the failed login attempt"
        )

    class Update(BaseModel):
        pass

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        ip_address: Optional[StringSearchModel] = None
        created_at: Optional[DateSearchModel] = None


class FailedLoginAttemptManager(AbstractBLLManager, RouterMixin):
    _model = FailedLoginAttemptModel

    def _register_search_transformers(self):
        self.register_search_transformer("recent", self._transform_recent_search)

    def _transform_recent_search(self, hours):
        """Transform a 'recent' search parameter to filter by recent time period"""
        if not hours or not isinstance(hours, int):
            hours = 1

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            FailedLoginAttemptModel.DB(self.model_registry.DB.manager.Base).created_at
            >= cutoff_time
        ]

    def count_recent(self, user_id: str, hours: int = 1) -> int:
        """Count recent failed login attempts for a user"""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        return FailedLoginAttemptModel.DB(self.model_registry.DB.manager.Base).count(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=user_id,
            filters=[
                FailedLoginAttemptModel.DB(
                    self.model_registry.DB.manager.Base
                ).created_at
                >= cutoff_time
            ],
        )

    def is_account_locked(
        self, user_id: str, max_attempts: int = 5, hours: int = 1
    ) -> bool:
        """Check if an account is locked due to too many failed attempts"""
        recent_count = self.count_recent(user_id, hours)
        return recent_count >= max_attempts


class TeamModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ParentMixinModel.Optional,
    NameMixinModel.Optional,
    ImageMixinModel.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["TeamManager"]] = None
    description: Optional[str] = Field(None, description="Team description")
    # TODO rename to encryption_salt
    encryption_key: Optional[str] = Field(
        ..., description="Encryption key for team data"
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
            "encryption_key": "",
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
        from database.StaticPermissions import (
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
        encryption_key: Optional[str] = Field(
            None, description="Encryption key for team data"
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
        description: Optional[StringSearchModel] = None


class TeamManager(AbstractBLLManager, RouterMixin):
    _model = TeamModel

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
                __import__("logic.BLL_Auth", fromlist=["InvitationManager"]),
                "InvitationManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": ["get", "list", "create", "search"],
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
                __import__("logic.BLL_Auth", fromlist=["TeamMetadataManager"]),
                "TeamMetadataManager",
            ),
            # child_network_model_cls will be inferred from the manager
        },
        "role": {
            "child_resource_name": "role",
            "manager_property": "roles",
            "child_manager_class": lambda: getattr(
                __import__("logic.BLL_Auth", fromlist=["RoleManager"]),
                "RoleManager",
            ),
            # child_network_model_cls will be inferred from the manager
            "routes_to_register": ["create", "list", "search", "get"],
        },
    }

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
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
            self._team_metadata = TeamMetadataManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
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
            self._invitations = InvitationManager(
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

        # Generate encryption key if not provided
        if "encryption_key" not in model_fields:
            model_fields["encryption_key"] = secrets.token_hex(32)

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
        """Get all metadata for the target team"""
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")

        metadata_items = MetadataModel.DB(self.model_registry.DB.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            team_id=self.target_team_id,
        )

        return {item.key: item.value for item in metadata_items}

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a team with optional included relationships. Returns 404 if not found."""

        fields = self.validate_fields(fields)

        options = []
        include_list = self.validate_includes(include)
        if include_list:
            options = self.generate_joins(self.DB, include_list)
        team = self.DB.get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            return_type="dto",
            override_dto=self.Model,
            options=options,
            **kwargs,
        )
        if team is None:
            team_id = kwargs.get("id") or kwargs.get("team_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Team with ID '{team_id}' not found",
            )
        return team

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

        return result

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

        from lib.Pydantic import obj_to_dict

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
class MetadataModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    metaclass=ModelMeta,
):
    # Foreign key fields (optional since metadata can be user-only, team-only, or both)
    user_id: Optional[str] = Field(None, description="Optional foreign key to User")
    team_id: Optional[str] = Field(None, description="Optional foreign key to Team")

    key: str = Field(..., description="Metadata key")
    value: Optional[str] = Field(None, description="Metadata value")

    # Relationship fields
    user: Optional["UserModel"] = None
    team: Optional["TeamModel"] = None

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Unified metadata table for users and teams"
    seed_data: ClassVar[List[Dict[str, Any]]] = []

    class Create(BaseModel):
        user_id: Optional[str] = Field(None, description="User ID if user metadata")
        team_id: Optional[str] = Field(None, description="Team ID if team metadata")
        key: str = Field(..., description="Metadata key")
        value: Optional[str] = Field(None, description="Metadata value")

    class Update(BaseModel):
        value: Optional[str] = Field(None, description="Metadata value")

    class Search(ApplicationModel.Search):
        user_id: Optional[StringSearchModel] = None
        team_id: Optional[StringSearchModel] = None
        key: Optional[StringSearchModel] = None
        value: Optional[StringSearchModel] = None


class MetadataManager(AbstractBLLManager):
    _model = MetadataModel

    def create_validation(self, entity):
        """Validate metadata creation"""
        # Ensure at least one of user_id or team_id is provided
        if not entity.user_id and not entity.team_id:
            raise HTTPException(
                status_code=400, detail="Either user_id or team_id must be provided"
            )

        # Validate user and team existence are handled by database constraints

    def set_preference(
        self,
        key: str,
        value: str,
        user_id: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Set or update a metadata preference"""
        if not user_id and not team_id:
            raise HTTPException(
                status_code=400, detail="Either user_id or team_id is required"
            )

        # Find existing metadata for this EXACT combination of key and user_id/team_id
        search_params = {"key": {"value": key}}
        if user_id:
            search_params["user_id"] = {"value": user_id}
        if team_id:
            search_params["team_id"] = {"value": team_id}

        logger.debug(f"DEBUG: Search params: {search_params}")
        existing = self.search(search_params)
        logger.debug(
            f"DEBUG: Search returned {len(existing) if existing else 0} records"
        )

        # Debug: Show what we actually found
        if existing:
            for i, record in enumerate(existing):
                logger.debug(
                    f"DEBUG: Found record {i}: user_id={record.user_id}, team_id={getattr(record, 'team_id', None)}, key={record.key}, value={record.value}, created_by={getattr(record, 'created_by_user_id', None)}"
                )

        # Filter to only records that EXACTLY match our criteria above
        exact_matches = []
        if existing:
            for record in existing:
                matches_user = (user_id is None) or (
                    getattr(record, "user_id", None) == user_id
                )
                matches_team = (team_id is None) or (
                    getattr(record, "team_id", None) == team_id
                )
                matches_key = record.key == key

                if matches_user and matches_team and matches_key:
                    exact_matches.append(record)
                    logger.debug(f"DEBUG: Record {record.id} is an exact match")
                else:
                    logger.debug(
                        f"DEBUG: Record {record.id} is NOT an exact match - user:{matches_user}, team:{matches_team}, key:{matches_key}"
                    )

        # Filter out system-created records when setting user preferences
        if exact_matches and user_id and not team_id:
            from lib.Environment import env

            user_owned_records = []
            for record in exact_matches:
                created_by = getattr(record, "created_by_user_id", None)
                created_by_system = created_by in [env("ROOT_ID"), env("SYSTEM_ID")]
                logger.debug(
                    f"DEBUG: Record: user_id={record.user_id}, key={record.key}, created_by={created_by}, is_system={created_by_system}"
                )

                # Only include records that were NOT created by system users
                if not created_by_system:
                    user_owned_records.append(record)
            exact_matches = user_owned_records
            logger.debug(
                f"DEBUG: After filtering, {len(exact_matches)} user-owned exact matches remain"
            )

        if exact_matches and len(exact_matches) > 0:
            # Update existing - for user metadata, ensure proper ownership
            record = exact_matches[0]
            logger.debug(f"DEBUG: Updating existing record {record.id}")
            if user_id and not team_id:
                # For user metadata, use the user as the requester to ensure they can update their own records
                with MetadataManager(
                    requester_id=user_id, model_registry=self.model_registry
                ) as temp_mgr:
                    temp_mgr.update(id=record.id, value=value)
            else:
                self.update(id=record.id, value=value)
            return {"key": key, "value": value, "action": "updated"}
        else:
            # Create new - for user preferences, create as the user to ensure proper ownership
            create_args = {"key": key, "value": value}
            logger.debug(f"DEBUG: Creating new metadata: {create_args}")
            if user_id:
                create_args["user_id"] = user_id
            if team_id:
                create_args["team_id"] = team_id

            # For user metadata, temporarily create with the user as the requester for proper ownership
            if user_id and not team_id:
                # Create a temporary MetadataManager with the target user as requester for proper ownership
                with MetadataManager(
                    requester_id=user_id, model_registry=self.model_registry
                ) as temp_mgr:
                    temp_mgr.create(**create_args)
            else:
                self.create(**create_args)
                logger.debug("DEBUG: Metadata creation attempted")
            return {"key": key, "value": value, "action": "created"}

    def get_preference(
        self, key: str, user_id: Optional[str] = None, team_id: Optional[str] = None
    ) -> Optional[str]:
        """Get a metadata preference value"""
        search_params = {"key": {"value": key}}
        if user_id:
            search_params["user_id"] = {"value": user_id}
        if team_id:
            search_params["team_id"] = {"value": team_id}

        logger.debug(
            f"DEBUG get_preference: searching for key='{key}', user_id='{user_id}'"
        )
        logger.debug(f"DEBUG get_preference: search_params={search_params}")
        results = self.search(search_params)
        logger.debug(
            f"DEBUG get_preference: found {len(results) if results else 0} results"
        )

        # Filter to only records that EXACTLY match our criteria
        exact_matches = []
        if results:
            for record in results:
                # Check if this record exactly matches our search criteria
                matches_user = (user_id is None) or (
                    getattr(record, "user_id", None) == user_id
                )
                matches_team = (team_id is None) or (
                    getattr(record, "team_id", None) == team_id
                )
                matches_key = record.key == key

                logger.debug(
                    f"DEBUG get_preference result: key='{record.key}', value='{record.value}', user_id='{getattr(record, 'user_id', None)}', created_by='{getattr(record, 'created_by_user_id', 'unknown')}', exact_match={matches_user and matches_team and matches_key}"
                )

                if matches_user and matches_team and matches_key:
                    exact_matches.append(record)

        # Filter out system-created records when getting user preferences
        if exact_matches and user_id and not team_id:
            from lib.Environment import env

            user_owned_records = []
            for record in exact_matches:
                # Only include records that were NOT created by system users
                created_by_system = getattr(record, "created_by_user_id", None) in [
                    env("ROOT_ID"),
                    env("SYSTEM_ID"),
                ]
                logger.debug(
                    f"DEBUG get_preference: record key='{record.key}', created_by_system={created_by_system}"
                )
                if not created_by_system:
                    user_owned_records.append(record)
            exact_matches = user_owned_records
            logger.debug(
                f"DEBUG get_preference: after filtering, {len(exact_matches)} user-owned exact matches remain"
            )

        if exact_matches and len(exact_matches) > 0:
            result_value = exact_matches[0].value
            logger.debug(f"DEBUG get_preference: returning value='{result_value}'")
            return result_value

        logger.debug("DEBUG get_preference: returning None")
        return None


class TeamMetadataManager(MetadataManager):
    """Alias for backward compatibility - filters by team_id by default"""

    def create_validation(self, entity):
        """Validate team metadata creation"""
        # Ensure team_id is provided for team metadata
        if not hasattr(entity, "team_id") or not entity.team_id:
            raise HTTPException(
                status_code=400, detail="team_id is required for team metadata"
            )

        # Call parent validation
        super().create_validation(entity)

    def set_preference(self, key: str, value: str) -> Dict[str, str]:
        """Set or update a team preference"""
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        return super().set_preference(key, value, team_id=self.target_team_id)

    def get_preference(self, key: str) -> Optional[str]:
        """Get a team preference value"""
        if not self.target_team_id:
            raise HTTPException(status_code=400, detail="Team ID is required")
        return super().get_preference(key, team_id=self.target_team_id)


class RoleModel(
    ApplicationModel,
    ParentMixinModel,
    NameMixinModel,
    UpdateMixinModel,
    TeamModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RoleManager"]] = None
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
        TeamModel.Reference.ID.Optional,
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
        TeamModel.Reference.ID.Search,
    ):
        friendly_name: Optional[StringSearchModel] = None
        mfa_count: Optional[NumericalSearchModel] = None

    create_permission_reference: ClassVar[str] = "resource"

    @classmethod
    def user_can_create(cls, user_id, db, **kwargs):
        """
        Check if a user can create a permission record.
        Users need SHARE permission on the resource they're creating a permission for.
        """
        from database.StaticPermissions import (
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
        from database.StaticPermissions import (
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


class RoleManager(AbstractBLLManager, RouterMixin):
    _model = RoleModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/role"
    tags: ClassVar[Optional[List[str]]] = ["Role Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    # routes_to_register defaults to None, which includes all routes
    auth_dependency: ClassVar[Optional[str]] = "get_role_manager"

    # TODO if a role is deleted, all users with that role should fall back to the role from which it inherits.
    def _register_search_transformers(self):
        self.register_search_transformer("is_system", self._transform_is_system_search)

    def _transform_is_system_search(self, value):
        """Transform is_system search to filter system roles (team_id is NULL)"""
        if value:
            return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id == None]
        return [RoleModel.DB(self.model_registry.DB.manager.Base).team_id != None]

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a role with optional included relationships. Returns 404 if not found."""

        fields = self.validate_fields(fields)

        options = []

        include_list = self.validate_includes(include)
        if include_list:
            options = self.generate_joins(self.DB, include_list)
        role = self.DB.get(
            requester_id=self.requester.id,
            fields=fields,
            model_registry=self.model_registry,
            return_type="dto" if not fields else "dict",
            override_dto=self.Model if not fields else None,
            options=options,
            **kwargs,
        )
        if role is None:
            role_id = kwargs.get("id") or kwargs.get("role_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role with ID '{role_id}' not found",
            )

        if role.created_by_user_id != self.requester.id:
            # Business logic validation: if accessing a team-specific role, validate team membership
            if role.team_id:
                self.validate_user_team(self.requester.id, role.team_id)

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
                detail=f"User {user_id} is not a member of team {team_id}",
            )
        elif len(user_teams) > 1:
            raise HTTPException(
                status_code=409,
                detail="Request uncovered multiple UserTeam when only one was expected.",
            )

    def create_validation(self, entity):
        """Validate role creation."""
        # First, check if the team exists (if team_id is provided)
        # Team existence validation is handled by the database layer

        # Second, check if parent role exists and is accessible
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
    UserModel.Reference,
    TeamModel.Reference,
    RoleModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["UserTeamManager"]] = None
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
        UserModel.Reference.ID,
        TeamModel.Reference.ID,
        RoleModel.Reference.ID,
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
        role_id: str = Field(
            None, description="Role ID to be assigned to the user in this team"
        )

    class Search(
        ApplicationModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        enabled: Optional[bool] = None

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
        from database.StaticPermissions import is_root_id, is_system_user_id

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

        from database.StaticPermissions import is_root_id, is_system_user_id
        from lib.Logging import logger

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


class UserTeamManager(AbstractBLLManager, RouterMixin):
    _model = UserTeamModel

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get a user with optional included relationships."""
        options = []
        
        fields = self.validate_fields(fields)
        # TODO Move generate_joins to AbstractDatabaseEntity.py
        if include:
            include_list = self._parse_includes(include)
            if include_list:
                options = self.generate_joins(self.DB, include_list)

        # First check if the record exists
        result = self.DB.get(
            requester_id=self.requester.id,
            fields=fields,
            model_registry=self.model_registry,
            return_type="dto" if not fields else "dict",
            override_dto=self.Model if not fields else None,
            options=options,
            **kwargs,
        )

        if result is None:
            team_id = kwargs.get("team_id") or kwargs.get("user_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User Team with ID '{team_id}' not found",
            )

        # Only check permissions after confirming the record exists
        if "team_id" in kwargs:
            if not self.DB.user_has_read_access(
                self.requester.id, kwargs.get("team_id"), self.db
            ):
                raise HTTPException(status_code=403, detail="get - not permissable")

        return result

    def search(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "asc",
        filters: Optional[List[Any]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        page: Optional[int] = None,
        pageSize: Optional[int] = None,
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
            return records

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

        return records

    def update(self, id: str, team_id: str = None, db=None, db_manager=None, **kwargs):
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
                from lib.Logging import logger

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
                from database.StaticPermissions import is_root_id

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
                from lib.Logging import logger

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

        role_id = body["user_team"]["role_id"]
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

        target_role_id = body["user_team"]["role_id"]
        updated_data = {"role_id": target_role_id}

        self.update(id=target_user_team.id, team_id=team_id, **updated_data)

        return {"message": "Role updated successfully"}


class UserMetadataManager(MetadataManager):
    """Alias for backward compatibility - filters by user_id by default"""

    def create_validation(self, entity):
        """Validate user metadata creation"""
        # Ensure user_id is provided for user metadata
        if not hasattr(entity, "user_id") or not entity.user_id:
            raise HTTPException(
                status_code=400, detail="user_id is required for user metadata"
            )

        # Call parent validation
        super().create_validation(entity)

    def set_preference(self, key: str, value: str) -> Dict[str, str]:
        """Set or update a user preference"""
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        return super().set_preference(key, value, user_id=self.target_user_id)

    def get_preference(self, key: str) -> Optional[str]:
        """Get a user preference value"""
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")
        return super().get_preference(key, user_id=self.target_user_id)

    def get_preferences(self) -> Dict[str, str]:
        """Get all user preferences"""
        if not self.target_user_id:
            raise HTTPException(status_code=400, detail="User ID is required")

        # Search for all metadata for this user
        results = self.search({"user_id": {"value": self.target_user_id}})

        # Convert to dictionary of key-value pairs
        preferences = {}
        for metadata in results:
            preferences[metadata.key] = metadata.value

        return preferences


class PermissionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["PermissionManager"]] = None
    resource_type: str = Field(..., description="Type of resource")
    resource_id: str = Field(..., description="ID of the resource")
    can_view: bool = Field(False, description="Whether user/team can view the resource")
    can_execute: bool = Field(
        False, description="Whether user/team can execute the resource"
    )
    can_copy: bool = Field(False, description="Whether user/team can copy the resource")
    can_edit: bool = Field(False, description="Whether user/team can edit the resource")
    can_delete: bool = Field(
        False, description="Whether user/team can delete the resource"
    )
    can_share: bool = Field(
        False, description="Whether user/team can share the resource with others"
    )
    expires_at: Optional[datetime] = Field(None, description="Permission expiration")
    enabled: bool = Field(True, description="Whether the permission is enabled")
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Fine-grained permissions for specific resources and actions"
    )
    create_permission_reference: ClassVar[str] = "resource"

    class Create(
        BaseModel,
        UserModel.Reference.ID.Optional,
        TeamModel.Reference.ID.Optional,
        RoleModel.Reference.ID.Optional,
    ):
        resource_type: str = Field(..., description="Type of resource")
        resource_id: str = Field(..., description="ID of the resource")
        can_view: Optional[bool] = Field(
            False, description="Whether user/team can view the resource"
        )
        can_execute: Optional[bool] = Field(
            False, description="Whether user/team can execute the resource"
        )
        can_copy: Optional[bool] = Field(
            False, description="Whether user/team can copy the resource"
        )
        can_edit: Optional[bool] = Field(
            False, description="Whether user/team can edit the resource"
        )
        can_delete: Optional[bool] = Field(
            False, description="Whether user/team can delete the resource"
        )
        can_share: Optional[bool] = Field(
            False, description="Whether user/team can share the resource with others"
        )

        @model_validator(mode="after")
        def validate_permission_combination(self) -> "Create":
            """Validate that the permission has a valid combination of user_id, team_id, and role_id."""
            # Case 1: User-specific permission (user_id only)
            if self.user_id and not self.team_id and not self.role_id:
                return self

            # Case 2: Team-specific permission (team_id only)
            if self.team_id and not self.user_id and not self.role_id:
                return self

            # Case 3: Team role-specific permission (team_id and role_id)
            if self.team_id and not self.user_id and self.role_id:
                return self

            # Case 4: System role-specific permission (role_id only)
            if not self.user_id and not self.team_id and self.role_id:
                # For Case 4, we need to ensure the role is team-specific
                # Since we can't access the database here, we'll enforce that
                # system-wide roles (without team_id) are not allowed
                raise ValueError("Role must be team-specific when used without team_id")

            # Invalid combinations
            if self.user_id and self.role_id:
                raise ValueError("Cannot have both user_id and role_id")

            if self.user_id and self.team_id:
                raise ValueError(
                    "Invalid permission combination: cannot have both user_id and team_id"
                )

            # If we get here, no valid combination was found
            raise ValueError("Invalid permission combination")

    class Update(BaseModel, RoleModel.Reference.ID.Optional):
        can_view: Optional[bool] = Field(
            None, description="Whether user/team can view the resource"
        )
        can_execute: Optional[bool] = Field(
            None, description="Whether user/team can execute the resource"
        )
        can_copy: Optional[bool] = Field(
            None, description="Whether user/team can copy the resource"
        )
        can_edit: Optional[bool] = Field(
            None, description="Whether user/team can edit the resource"
        )
        can_delete: Optional[bool] = Field(
            None, description="Whether user/team can delete the resource"
        )
        can_share: Optional[bool] = Field(
            None, description="Whether user/team can share the resource with others"
        )

    class Search(
        ApplicationModel.Search,
        UserModel.Reference.ID.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        resource_type: Optional[StringSearchModel] = None
        resource_id: Optional[StringSearchModel] = None
        can_view: Optional[bool] = None
        can_execute: Optional[bool] = None
        can_copy: Optional[bool] = None
        can_edit: Optional[bool] = None
        can_delete: Optional[bool] = None
        can_share: Optional[bool] = None


class PermissionManager(AbstractBLLManager, RouterMixin):
    _model = PermissionModel

    def create_validation(self, entity):
        """Validate permission creation with database checks"""
        # Related entity existence validation is handled by the database layer

        if entity.user_id:
            user = UserModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.user_id,
            )

            if not user:
                raise HTTPException(status_code=404, detail="User not found")

        if entity.team_id:
            team = TeamModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.team_id,
            )

            if not team:
                raise HTTPException(status_code=404, detail="Team not found")

        role = None
        if entity.role_id:
            role = RoleModel.DB(self.model_registry.DB.manager.Base).get(
                requester_id=self.requester.id,
                model_registry=self.model_registry,
                id=entity.role_id,
            )

            if not role:
                raise HTTPException(status_code=404, detail="Role not found")

        # If only role_id is provided (no team_id), verify it's a system role
        if role and not entity.team_id and not entity.user_id:
            if not role.team_id:
                raise ValueError("Role must be team-specific when used without team_id")


class InvitationModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    TeamModel.Reference.Optional,
    RoleModel.Reference.Optional,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["InvitationManager"]] = None
    code: Optional[str] = Field(None, description="Invitation code")
    max_uses: Optional[int] = Field(None, description="Maximum number of uses allowed")
    expires_at: Optional[datetime] = Field(None, description="Expiration date/time")

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Invitations to join teams, can be direct or via invitation code"
    )

    class Create(
        BaseModel,
        TeamModel.Reference.ID.Optional,
        RoleModel.Reference.ID.Optional,
        UserModel.Reference.ID.Optional,
    ):
        code: Optional[str] = Field(
            None, description="Invitation code (auto-generated if not provided)"
        )
        # user_id: Optional[str] = Field(None, description="User ID of the inviter")
        max_uses: Optional[int] = Field(
            None, description="Maximum number of uses allowed"
        )
        expires_at: Optional[datetime] = Field(None, description="Expiration date/time")
        email: Optional[str] = Field(
            None, description="Email address of the invitee (if known)"
        )

        @model_validator(mode="after")
        def validate_team_role_combination(self):
            """Validate that if team_id or role_id is provided, both must be provided"""
            has_team = self.team_id is not None
            has_role = self.role_id is not None

            if has_team != has_role:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=400,
                    detail="team_id and role_id must both be provided together, or both be null for app-level invitations",
                )
            return self

    class Patch(BaseModel):
        invitation_code: Optional[str] = Field(
            None, description="Invitation code to accept"
        )
        invitee_id: Optional[str] = Field(
            None, description="ID of existing invitee record"
        )
        action: Optional[str] = Field(
            None, description="Action to perform (e.g., 'accept', 'decline')"
        )

        @model_validator(mode="after")
        def validate_acceptance_method(self):
            """Validate that exactly one acceptance method is provided"""
            methods_provided = sum(
                [
                    self.invitation_code is not None,
                    self.invitee_id is not None,
                ]
            )

            if methods_provided != 1:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=400,
                    detail="Exactly one of invitation_code or invitee_id must be provided",
                )
            return self

    class Accept(BaseModel):
        invitation_code: Optional[str] = Field(
            None, description="Invitation code to accept"
        )
        invitee_id: Optional[str] = Field(
            None, description="ID of existing invitee record"
        )
        action: Optional[str] = Field(
            None, description="Action to perform (e.g., 'accept', 'decline')"
        )

        @model_validator(mode="after")
        def validate_acceptance_method(self):
            """Validate that exactly one acceptance method is provided"""
            methods_provided = sum(
                [
                    self.invitation_code is not None,
                    self.invitee_id is not None,
                ]
            )

            if methods_provided != 1:
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=400,
                    detail="Exactly one of invitation_code or invitee_id must be provided",
                )
            return self

    class Update(BaseModel, RoleModel.Reference.ID.Optional):
        code: Optional[str] = Field(None, description="Invitation code")
        max_uses: Optional[int] = Field(
            None, description="Maximum number of uses allowed"
        )
        expires_at: Optional[datetime] = Field(None, description="Expiration date/time")

    class Search(
        ApplicationModel.Search,
        TeamModel.Reference.ID.Search,
        RoleModel.Reference.ID.Search,
    ):
        code: Optional[StringSearchModel] = None
        user_id: Optional[StringSearchModel] = None
        max_uses: Optional[NumericalSearchModel] = None
        expires_at: Optional[DateSearchModel] = None


class InvitationAcceptanceResponse(BaseModel):
    """Response model for invitation acceptance."""

    success: bool = Field(
        ..., description="Whether the invitation was accepted successfully"
    )
    message: str = Field(..., description="Success or error message")
    team_id: Optional[str] = Field(None, description="ID of the team joined")
    role_id: Optional[str] = Field(None, description="ID of the role assigned")
    user_team_id: Optional[str] = Field(
        None, description="ID of the user-team relationship created"
    )


class InvitationManager(AbstractBLLManager, RouterMixin):
    _model = InvitationModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/invitation"
    tags: ClassVar[Optional[List[str]]] = ["Team Management"]
    auth_type: ClassVar[AuthType] = AuthType.JWT
    auth_dependency: ClassVar[Optional[str]] = "get_invitation_manager"
    custom_routes: ClassVar[List[Dict[str, Any]]] = [
        {
            "path": "/{id}",
            "method": "patch",
            "function": "patch_invitation_endpoint",
            "summary": "Accept invitation",
            "description": """
            Accepts an invitation to a team on behalf of an existing user.
            
            Supports two acceptance methods:
            1. Via invitation_code: For public invitations using a shareable code
            2. Via invitee_id: For direct email invitations where the user was specifically invited
            
            The invitation will create or update the user's team membership if it's a team-specific invitation.
            App-level invitations (without team/role) are accepted for referral tracking purposes.
            """,
            "response_model": "InvitationAcceptanceResponse",
            "status_code": 200,
            "responses": {
                200: {
                    "description": "Invitation processed (success or failure details in response)",
                    "content": {
                        "application/json": {
                            "examples": {
                                "success": {
                                    "summary": "Successful acceptance",
                                    "value": {
                                        "success": True,
                                        "message": "Invitation accepted successfully via code",
                                        "team_id": "team-uuid-here",
                                        "role_id": "role-uuid-here",
                                        "user_team_id": "user-team-uuid-here",
                                    },
                                },
                                "failure": {
                                    "summary": "Failed acceptance",
                                    "value": {
                                        "success": False,
                                        "message": "Invitation has expired",
                                        "team_id": None,
                                        "role_id": None,
                                        "user_team_id": None,
                                    },
                                },
                            }
                        }
                    },
                },
                401: {"description": "Invalid or missing authentication"},
                400: {"description": "Invalid request data"},
            },
        }
    ]

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Optional[Any] = None,
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._Invitee_manager = None

    @property
    def Invitee_manager(self):
        if self._Invitee_manager is None:
            self._Invitee_manager = InviteeManager(
                requester_id=self.requester.id,
                target_team_id=self.target_team_id,
                parent=self,
                model_registry=self.model_registry,
            )
        return self._Invitee_manager

    def create_validation(self, entity):
        """Validate invitation creation"""
        # Check that team/role combination is valid
        if entity.team_id and not entity.role_id:
            raise HTTPException(
                status_code=400, detail="team_id and role_id must both be provided"
            )
        if entity.role_id and not entity.team_id:
            raise HTTPException(
                status_code=400, detail="team_id and role_id must both be provided"
            )

        # Check if team exists (handled by database constraints)

        # Check if role exists (handled by database constraints)

        # Check if inviter exists - handled by database constraints

    def create(self, **kwargs):
        """Create an invitation with auto-generated code if needed"""
        # Determine if this is a public invitation (has team_id/role_id)
        has_team_role = kwargs.get("team_id") and kwargs.get("role_id")

        # Only generate code for public invitations (team-based)
        if has_team_role and ("code" not in kwargs or not kwargs["code"]):
            kwargs["code"] = "".join(
                secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
            )
        elif not has_team_role:
            # App-level invitations should not have codes
            kwargs.pop("code", None)

        existing_invitations = self.list(
            team_id=kwargs.get("team_id"),
            role_id=kwargs.get("role_id"),
            deleted_at=None,
        )

        if "email" in kwargs:
            email = kwargs.pop("email")
            if not email:
                raise HTTPException(status_code=400, detail="empty")
            emails = [email] if isinstance(email, str) else email

            # check if invitation already exists
            existing_invitees = []

            for invitation in existing_invitations:
                invitees = self.Invitee_manager.list(
                    invitation_id=invitation.id, deleted_at=None, declined_at=None
                )
                for invitee in invitees:
                    existing_invitees.append(invitee.email)

            for email in emails:
                email = email.lower().strip()
                if email in existing_invitees:
                    emails.remove(email)  # remove email if already invited

            if not emails:
                raise HTTPException(status_code=400, detail="already invited")

            # create the invitation
            invitation = super().create(**kwargs)

            # create invitees for the invitation
            for email in emails:
                self.add_invitee(invitation_id=invitation.id, email=email)

            return invitation

        user = None
        if "user_id" in kwargs:
            user_id = kwargs.get("user_id")
            for invitation in existing_invitations:
                if user_id == invitation.user_id:
                    raise HTTPException(
                        status_code=400, detail=f"user {user_id} already invited"
                    )
            user_manager = UserManager(
                requester_id=self.requester.id,
                target_id=user_id,
                model_registry=self.model_registry,
            )
            user = user_manager.get()

        invitation = super().create(**kwargs)

        if user is not None:
            invitation.user = user

        return invitation

    @staticmethod
    def generate_invitation_link(code: str, email: str = None) -> str:
        """Generate an invitation link from a code"""
        base_url = env("APP_URI")
        if not email:
            return f"{base_url}/join?code={code}"
        return f"{base_url}/join?code={code}?email={email}"

    def add_invitee(self, invitation_id: str, email: str) -> Dict[str, Any]:
        """Add an invitee to an invitation"""

        if not invitation_id:
            raise HTTPException(status_code=404, detail="Invitation not found")

        # Get the invitation to check if it exists
        invitation = self.get(id=invitation_id)

        # Check if user exists by email - if not, this is an email-only invitation
        user_manager = UserManager(
            requester_id=self.requester.id, model_registry=self.model_registry
        )
        user_id = None
        try:
            user = user_manager.list(email=email.lower().strip())
            if user:
                user_id = user[0].id
        except:
            # User doesn't exist yet, this is fine for email invitations
            pass

        # Create the invitee record
        invitee = self.Invitee_manager.create(
            invitation_id=invitation_id,
            email=email.lower().strip(),
            user_id=user_id,  # Will be None for email-only invitations
        )

        # Only generate invitation link if there's a code (public invitations)
        invitation_link = None
        if invitation.code:
            invitation_link = self.generate_invitation_link(invitation.code)

        if invitation.team_id not in (None, ""):
            # If this is a team invitation, set the team
            with TeamManager(
                requester_id=self.requester.id,
                target_id=invitation.team_id,
                model_registry=self.model_registry,
            ) as team_manager:
                team = team_manager.get(id=invitation.team_id)
                invitation.team = team

        if invitee.invitation is None:
            invitee.invitation = invitation

        # FIXME: This should be done with hooks
        try:

            from extensions.email.BLL_EMail import send_invitation_email_hook

            send_invitation_email_hook(manager=self, entity=invitee)

        except Exception as e:
            from lib.Logging import logger

            logger.error(
                f"Failed to send invitation email for invitation {invitation.id}: {str(e)}"
            )

        return {
            "invitation_id": invitation_id,
            "invitation_code": invitation.code,
            "invitation_link": invitation_link,
            "user_id": user_id,
            "email": email.lower().strip(),
        }

    def get(
        self,
        include: Optional[List[str]] = None,
        fields: Optional[List[str]] = [],
        **kwargs,
    ) -> Any:
        """Get an invitation with optional included relationships. Returns 404 if not found."""
        options = []

        fields = self.validate_fields(fields)
        include_list = self.validate_includes(include)
        
        if include_list:
            options = self.generate_joins(self.DB, include_list)

        invitation = self.DB.get(
            requester_id=self.requester.id,
            fields=fields,
            model_registry=self.model_registry,
            return_type="dto" if not fields else "dict",
            override_dto=self.Model if not fields else None,
            options=options,
            **kwargs,
        )

        if invitation is None:
            invitation_id = kwargs.get("id") or kwargs.get("invitation_id") or "unknown"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Invitation with ID '{invitation_id}' not found",
            )
        return invitation

    def accept_invitation_unified(
        self, accept_data: InvitationModel.Accept, user_id: str
    ) -> Dict[str, Any]:
        """Accept or decline an invitation via invitation code or invitee ID."""
        patch_data = InvitationModel.Patch(**accept_data.model_dump())
        return self.patch_invitation_unified(patch_data, user_id)

    def patch_invitation_unified(
        self, patch_data: InvitationModel.Patch, user_id: str
    ) -> Dict[str, Any]:
        """
        Unified method to accept invitations via either invitation code or invitee ID.

        This method handles both:
        1. Accepting via invitation_code (creates invitee on the spot)
        2. Accepting via invitee_id (for direct email invitations)

        Args:
            accept_data: The acceptance data containing either invitation_code or invitee_id
            user_id: The ID of the user accepting the invitation

        Returns:
            Dict containing success status, message, and team/role details
        """
        if patch_data.invitation_code:
            # Handle invitation code acceptance
            try:
                if patch_data.action and patch_data.action.lower() == "decline":
                    # Handle decline action
                    result = self.Invitee_manager.decline_invitation(
                        patch_data.invitation_code
                    )
                    return {
                        "success": True,
                        "message": "Invitation declined successfully via code",
                        "team_id": result.get("team_id"),
                        "role_id": result.get("role_id"),
                    }
                else:
                    result = self.Invitee_manager.accept_invitation(
                        patch_data.invitation_code, user_id
                    )

                    return {
                        "success": True,
                        "message": "Invitation accepted successfully via code",
                        "team_id": result.get("team_id"),
                        "role_id": result.get("role_id"),
                        "user_team_id": result.get("user_team_id"),
                    }
            except HTTPException as e:
                # Re-raise HTTP exceptions as they contain proper error codes
                raise e
            except Exception as e:
                raise HTTPException(
                    status_code=404, detail=f"Failed to accept invitation: {str(e)}"
                )

        elif patch_data.invitee_id:
            # Handle direct invitee acceptance
            try:
                # Get the invitee record
                invitee = self.Invitee_manager.get(id=patch_data.invitee_id)

                # Get the user details
                user_manager = UserManager(
                    requester_id=env("ROOT_ID"), model_registry=self.model_registry
                )
                user = user_manager.get(id=user_id)

                # Verify the user's email matches the invitee email
                if user.email.lower() != invitee.email.lower():
                    raise HTTPException(
                        status_code=403,
                        detail="User email does not match invitation email",
                    )

                # Check if already accepted
                if invitee.accepted_at:
                    raise HTTPException(
                        status_code=409, detail="Invitation already accepted"
                    )

                # Check if declined
                if invitee.declined_at:
                    raise HTTPException(
                        status_code=409, detail="Invitation was previously declined"
                    )

                # Get the invitation details
                invitation = self.get(id=invitee.invitation_id)

                # Check if invitation has expired
                if invitation.expires_at:
                    # Handle timezone comparison - if expires_at is naive, treat it as UTC
                    expires_at = invitation.expires_at
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    if expires_at < datetime.now(timezone.utc):
                        raise HTTPException(
                            status_code=410, detail="Invitation has expired"
                        )

                if patch_data.action and patch_data.action.lower() == "decline":
                    self.Invitee_manager.update(
                        id=invitee.id,
                        declined_at=datetime.now(timezone.utc),
                        user_id=user_id,
                    )
                    if invitation.team_id and invitation.role_id:
                        return {
                            "success": True,
                            "message": "Invitation declined successfully via invitee ID",
                            "team_id": invitation.team_id,
                            "role_id": invitation.role_id,
                        }

                # Mark invitee as accepted
                self.Invitee_manager.update(
                    id=invitee.id,
                    accepted_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )

                # If this is a team invitation, add user to team
                user_team_id = None
                if invitation.team_id and invitation.role_id:
                    user_team_manager = UserTeamManager(
                        requester_id=env(
                            "ROOT_ID"
                        ),  # Use ROOT_ID for invitation acceptance
                        target_id=user.id,
                        model_registry=self.model_registry,
                    )

                    # Check for existing team membership
                    existing_memberships = user_team_manager.list(
                        user_id=user_id,
                        team_id=invitation.team_id,
                    )

                    if existing_memberships:
                        # Update existing team membership
                        user_team = user_team_manager.update(
                            id=existing_memberships[0].id,
                            role_id=invitation.role_id,
                            enabled=True,
                        )
                        user_team_id = existing_memberships[0].id
                    else:
                        # Create new team membership
                        user_team = user_team_manager.create(
                            user_id=user_id,
                            team_id=invitation.team_id,
                            role_id=invitation.role_id,
                            enabled=True,
                        )
                        user_team_id = user_team.id

                return {
                    "success": True,
                    "message": "Invitation accepted successfully via invitee ID",
                    "team_id": invitation.team_id,
                    "role_id": invitation.role_id,
                    "user_team_id": user_team_id,
                }
            except HTTPException as e:
                # Re-raise HTTP exceptions as they contain proper error codes
                raise e
            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Failed to accept invitation: {str(e)}"
                )

        else:
            raise HTTPException(
                status_code=400,
                detail="Exactly one of invitation_code or invitee_id must be provided",
            )

    def accept_invitation(self, code: str, user_id: str) -> Dict[str, Any]:
        """Accept an invitation using a code and user ID - delegates to InviteeManager"""
        return self.Invitee_manager.accept_invitation(code, user_id)

    def patch_invitation_endpoint(
        self, id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Accept or Decline an invitation endpoint (custom route method)"""
        patch_data = body.get("invitation")
        if not patch_data:
            raise HTTPException(status_code=400, detail="Missing invitation data")

        # Convert dict to InvitationModel.Accept
        patch_model = InvitationModel.Patch(**patch_data)

        # Use the unified acceptance method with the manager's requester ID
        result = self.patch_invitation_unified(patch_model, self.requester.id)
        return result


class InviteeModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    InvitationModel.Reference,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["InviteeManager"]] = None
    email: str = Field(..., description="Email of the invitee")
    declined_at: Optional[datetime] = Field(
        None, description="When the invitation was declined"
    )
    accepted_at: Optional[datetime] = Field(
        None, description="When the invitation was accepted"
    )

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Tracks specific individuals invited to join a team"

    class Create(
        BaseModel, InvitationModel.Reference.ID, UserModel.Reference.ID.Optional
    ):
        email: str = Field(..., description="Email of the invitee")
        declined_at: Optional[datetime] = Field(
            None, description="When the invitation was declined"
        )
        accepted_at: Optional[datetime] = Field(
            None, description="When the invitation was accepted"
        )

    class Update(BaseModel, UserModel.Reference.ID.Optional):
        declined_at: Optional[datetime] = Field(
            None, description="When the invitation was declined"
        )
        accepted_at: Optional[datetime] = Field(
            None, description="When the invitation was accepted"
        )

    class Search(
        ApplicationModel.Search,
        InvitationModel.Reference.ID.Search,
        UserModel.Reference.ID.Search,
    ):
        email: Optional[StringSearchModel] = None
        declined_at: Optional[DateSearchModel] = Field(None)
        accepted_at: Optional[DateSearchModel] = Field(None)


class InviteeManager(AbstractBLLManager):
    _model = InviteeModel

    def create_validation(self, entity):
        """Validate invitee creation"""
        # Database constraints ensure referenced invitation exists.

        if "@" not in entity.email:
            raise HTTPException(status_code=400, detail="Invalid email format")

        # Database constraints ensure referenced user exists.

        existing = InviteeModel.DB(self.model_registry.DB.manager.Base).exists(
            requester_id=env(
                "ROOT_ID"
            ),  # Use ROOT_ID for invitation acceptance validation
            model_registry=self.model_registry,
            invitation_id=entity.invitation_id,
            email=entity.email.lower().strip(),
        )
        if existing:
            raise HTTPException(
                status_code=400, detail="This email has already been invited"
            )

    def accept_invitation_by_email(self, code: str, email: str) -> Dict[str, Any]:
        """
        Accept an invitation by code and email before user registration.

        This method allows accepting invitations during the registration process
        when the user doesn't exist yet.

        Args:
            code: Invitation code
            email: Email address of the user who will be registered

        Returns:
            Dict containing invitation details for later processing
        """
        # Find the invitation by code - use ROOT_ID since invitation acceptance should bypass permission checks
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")

        # Check if invitation has expired
        if invitation.expires_at:
            # Handle timezone comparison - if expires_at is naive, treat it as UTC
            expires_at = invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Invitation has expired")

        # Check if invitation has reached max uses
        if invitation.max_uses is not None:
            InviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
            used_count = InviteeDB.count(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                invitation_id=invitation.id,
                filters=[InviteeDB.accepted_at.isnot(None)],
            )
            if used_count >= invitation.max_uses:
                raise HTTPException(
                    status_code=410, detail="Invitation has reached maximum usage limit"
                )

        # Check if there's already an invitee record for this email
        existing_invitees = InviteeModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=email.lower().strip(),
            override_dto=InviteeModel,
            return_type="dto",
        )

        # Create invitee record if it doesn't exist
        if not existing_invitees:
            self.create(
                invitation_id=invitation.id,
                email=email.lower().strip(),
                user_id=None,  # Explicitly set to None for email-only invitations
            )

        return {
            "invitation_id": invitation.id,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "code": invitation.code,
        }

    def decline_invitation(self, code: str) -> Dict[str, Any]:
        """Decline an invitation using a code and user ID"""
        # Find the invitation by code - use ROOT_ID since invitation acceptance should bypass permission checks
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")

        # Check if the user exists
        user = UserModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=self.requester.id,
            return_type="dto",
            override_dto=UserModel,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Find invitee by email if exists
        inviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
        invitees = inviteeDB.list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=user.email,
            override_dto=InviteeModel,
            return_type="dto",
            filters=[
                inviteeDB.accepted_at.is_(None),
                inviteeDB.declined_at.is_(None),
            ],
        )

        # For invitation codes that don't have specific invitees, create an invitee record
        if invitees:
            # Use existing invitee record
            invitee = invitees[0]
            self.update(
                id=invitee.id,
                declined_at=datetime.now(timezone.utc),
                user_id=self.requester.id,
            )

        return {
            "success": True,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "message": "Invitation declined successfully",
        }

    # FIXME This should be on behalf of the requester, separate user ID not required.
    def accept_invitation(self, code: str, user_id: str) -> Dict[str, Any]:
        """Accept an invitation using a code and user ID"""
        # Find the invitation by code - use ROOT_ID since invitation acceptance should bypass permission checks
        invitation = InvitationModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            code=code,
            return_type="dto",
            override_dto=InvitationModel,
        )

        if not invitation:
            raise HTTPException(status_code=404, detail="Invalid invitation code")

        # Check if invitation has expired
        if invitation.expires_at:
            # Handle timezone comparison - if expires_at is naive, treat it as UTC
            expires_at = invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=410, detail="Invitation has expired")

        # Check if invitation has reached max uses
        if invitation.max_uses is not None:
            InviteeDB = InviteeModel.DB(self.model_registry.DB.manager.Base)
            used_count = InviteeDB.count(
                requester_id=env("ROOT_ID"),
                model_registry=self.model_registry,
                invitation_id=invitation.id,
                filters=[InviteeDB.accepted_at.isnot(None)],
            )
            if used_count >= invitation.max_uses:
                raise HTTPException(
                    status_code=410, detail="Invitation has reached maximum usage limit"
                )

        # Verify the user
        user = UserModel.DB(self.model_registry.DB.manager.Base).get(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            id=user_id,
            return_type="dto",
            override_dto=UserModel,
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Find invitee by email if exists
        invitees = InviteeModel.DB(self.model_registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=self.model_registry,
            invitation_id=invitation.id,
            email=user.email,
            override_dto=InviteeModel,
            return_type="dto",
        )

        # For invitation codes that don't have specific invitees, create an invitee record
        if not invitees:
            if not invitation.code:
                # This is a direct invitation without a code, we should have found a matching invitee
                raise HTTPException(
                    status_code=403, detail="Your email is not invited to this team"
                )
            else:
                # For invitation codes, create an invitee record
                invitee = self.create(
                    invitation_id=invitation.id,
                    email=user.email,
                    accepted_at=datetime.now(timezone.utc),
                    user_id=user_id,
                )
        else:
            # Use existing invitee record
            invitee = invitees[0]
            self.update(
                id=invitee.id,
                accepted_at=datetime.now(timezone.utc),
                user_id=user_id,
            )

        # Add user to team or update existing membership
        user_team_manager = UserTeamManager(
            requester_id=invitation.created_by_user_id,
            target_id=user_id,
            model_registry=self.model_registry,
        )

        existing_team_membership = user_team_manager.list(
            user_id=user_id,
            team_id=invitation.team_id,
        )

        if existing_team_membership:
            # Update existing team membership
            user_team = user_team_manager.update(
                id=existing_team_membership[0].id,
                role_id=invitation.role_id,
                enabled=True,
            )
        else:
            # Create new team membership
            user_team = user_team_manager.create(
                user_id=user_id,
                team_id=invitation.team_id,
                role_id=invitation.role_id,
                enabled=True,
            )

        return {
            "success": True,
            "team_id": invitation.team_id,
            "role_id": invitation.role_id,
            "user_team_id": user_team.id,
        }


class RateLimitPolicyModel(
    ApplicationModel,
    UpdateMixinModel,
    NameMixinModel,
    metaclass=ModelMeta,
):
    Manager: ClassVar[Type["RateLimitPolicyManager"]] = None
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
        resource_pattern: Optional[StringSearchModel] = None
        window_seconds: Optional[NumericalSearchModel] = None
        max_requests: Optional[NumericalSearchModel] = None
        scope: Optional[StringSearchModel] = None


class RateLimitPolicyManager(AbstractBLLManager, RouterMixin):
    _model = RateLimitPolicyModel


class SessionModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore", "populate_by_name": True}
    Manager: ClassVar[Type["SessionManager"]] = None
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

    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = (
        "Active user authentication sessions and related metadata"
    )

    @classmethod
    def user_has_all_access(cls, user_id, id, db, db_manager=None, model_registry=None):
        """
        Override delete permission logic to allow users to delete their own sessions.
        Users can delete sessions where they are the owner (user_id) even if they
        weren't the creator (created_by_user_id).
        """
        # Get Base from either model_registry or db_manager
        if model_registry:
            Base = model_registry.DB.manager.Base
        elif db_manager:
            Base = db_manager.Base
        else:
            raise ValueError("Either model_registry or db_manager is required")
        from lib.Environment import env, is_root_id, is_system_user_id

        # ROOT and SYSTEM users have all access
        if is_root_id(user_id) or is_system_user_id(user_id):
            return True

        # Get the session record directly from database without permission checks
        try:
            SQLAlchemy_model = cls.DB(Base)
            session_record = (
                db.query(SQLAlchemyModel).filter(SQLAlchemyModel.id == id).first()
            )

            if not session_record:
                return False

            # Allow users to delete their own sessions (where they are the owner)
            if hasattr(session_record, "user_id") and session_record.user_id == user_id:
                return True
        except Exception:
            # If there's any error accessing the record, fall back to default
            pass

        # Fall back to default permission check
        return super().user_has_all_access(user_id, id, db, db_manager)

    class Create(BaseModel, UserModel.Reference.ID):
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
        trust_score: Optional[int] = Field(
            50, description="Trust level of this session (0-100)"
        )
        requires_verification: Optional[bool] = Field(
            False, description="Whether additional verification is required"
        )

    class Update(BaseModel):
        is_active: Optional[bool] = Field(
            None, description="Whether this session is currently active"
        )
        last_activity: Optional[datetime] = Field(
            None, description="Timestamp of last activity in this session"
        )
        expires_at: Optional[datetime] = Field(
            None, description="When this session expires"
        )
        revoked: Optional[bool] = Field(
            None, description="Whether this session has been explicitly revoked"
        )
        trust_score: Optional[int] = Field(
            None, description="Trust level of this session (0-100)"
        )
        requires_verification: Optional[bool] = Field(
            None, description="Whether additional verification is required"
        )
        refresh_token_hash: Optional[str] = Field(
            None, description="Hash of refresh token if refresh mechanism is enabled"
        )

    class Search(ApplicationModel.Search, UserModel.Reference.ID.Search):
        session_key: Optional[StringSearchModel] = None
        is_active: Optional[bool] = None
        revoked: Optional[bool] = None
        expires_at: Optional[DateSearchModel] = None
        device_type: Optional[StringSearchModel] = None
        browser: Optional[StringSearchModel] = None
        requires_verification: Optional[bool] = None
        trust_score: Optional[NumericalSearchModel] = None


class SessionManager(AbstractBLLManager, RouterMixin):
    _model = SessionModel

    # RouterMixin configuration
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
    ):
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._users = None

    @property
    def users(self):
        if self._users is None:
            self._users = UserManager(
                requester_id=self.requester.id,
                target_id=self.target_user_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._users

    def create_validation(self, entity):
        """Validate auth session creation"""
        # User existence validation is handled by the database layer

        if self.Model.DB(self.model_registry.DB.manager.Base).exists(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            session_key=entity.session_key,
        ):
            raise HTTPException(status_code=400, detail="Session key already exists")

    def revoke_session(self, id: str) -> Dict[str, str]:
        """Revoke a single session"""
        session = self.Model.DB(self.model_registry.DB.manager.Base).get(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=id,
        )

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        self.Model.DB(self.model_registry.DB.manager.Base).update(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            id=id,
            new_properties={"revoked": True, "is_active": False},
        )

        return {"message": "Session revoked successfully"}

    def revoke_all_user_sessions(self, user_id: str) -> Dict[str, Any]:
        """Revoke all active sessions for a user"""
        sessions = self.Model.DB(self.model_registry.DB.manager.Base).list(
            requester_id=self.requester.id,
            model_registry=self.model_registry,
            user_id=user_id,
            is_active=True,
            revoked=False,
        )

        revoked_count = 0
        for session in sessions:
            session_id = session["id"] if isinstance(session, dict) else session.id
            self.Model.DB(self.model_registry.DB.manager.Base).update(
                requester_id=self.requester.id,
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
        """Update the last activity timestamp for a session"""
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
        """Validate if a session is active and not expired"""
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
            session["expires_at"] if isinstance(session, dict) else session.expires_at
        )

        # Handle timezone comparison
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        return expires_at > datetime.now(timezone.utc)

    def cleanup_expired_sessions(self) -> Dict[str, Any]:
        """Clean up expired sessions"""
        current_time = datetime.now(timezone.utc)

        # Find expired sessions
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
        """Get all sessions for a user"""
        filters = {"user_id": user_id}
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
        """Revoke all sessions for a user - legacy method returning int count"""
        result = self.revoke_all_user_sessions(user_id)
        return result.get("revoked_count", 0)


# Set up Model.Manager relationships
UserModel.Manager = UserManager
UserCredentialModel.Manager = UserCredentialManager
UserRecoveryQuestionModel.Manager = UserRecoveryQuestionManager
FailedLoginAttemptModel.Manager = FailedLoginAttemptManager
TeamModel.Manager = TeamManager
RoleModel.Manager = RoleManager
UserTeamModel.Manager = UserTeamManager
PermissionModel.Manager = PermissionManager
InvitationModel.Manager = InvitationManager
InviteeModel.Manager = InviteeManager
RateLimitPolicyModel.Manager = RateLimitPolicyManager
SessionModel.Manager = SessionManager

# Backwards compatibility aliases - can be removed in future versions
# These allow existing imports like 'from logic.BLL_Auth import UserManager' to continue working
__all__ = [
    "UserModel",
    "UserManager",
    "UserCredentialModel",
    "UserCredentialManager",
    "UserRecoveryQuestionModel",
    "UserRecoveryQuestionManager",
    "FailedLoginAttemptModel",
    "FailedLoginAttemptManager",
    "TeamModel",
    "TeamManager",
    "RoleModel",
    "RoleManager",
    "UserTeamModel",
    "UserTeamManager",
    "PermissionModel",
    "PermissionManager",
    "InvitationModel",
    "InvitationManager",
    "InviteeModel",
    "InviteeManager",
    "RateLimitPolicyModel",
    "RateLimitPolicyManager",
    "SessionModel",
    "SessionManager",
    "MetadataManager",
    "TeamMetadataManager",
    "UserMetadataManager",
]
