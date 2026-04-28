import secrets
import string
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

import bcrypt
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from lib.Logging import logger
from lib.Pydantic import BaseModel
from lib.Pydantic2FastAPI import AuthType, RouterMixin
from logic.AbstractLogicManager import (
    AbstractBLLManager,
    ApplicationModel,
    DateSearchModel,
    HookContext,
    HookTiming,
    ModelMeta,
    StringSearchModel,
    UpdateMixinModel,
    hook_bll,
)
from logic.BLL_Auth import UserManager, UserModel


# MFA method type constants
class MultifactorMethodType:
    TOTP = "totp"
    EMAIL = "email"
    SMS = "sms"


# Security and audit hooks for MFA operations
def security_validation_hook(context: HookContext) -> None:
    """Security validation for all MFA manager operations."""
    manager = context.manager
    method_name = context.method_name

    # Ensure requester has permission for MFA operations
    if not hasattr(manager.requester, "id"):
        raise HTTPException(
            status_code=401, detail="Authentication required for MFA operations"
        )

    # For operations targeting specific users, ensure authorization
    if hasattr(manager, "target_user_id") and manager.target_user_id:
        if manager.target_user_id != manager.requester.id:
            # Additional authorization check could be added here
            logger.warning(
                f"User {manager.requester.id} accessing MFA for user {manager.target_user_id}"
            )


def audit_mfa_operations(context: HookContext) -> None:
    """Audit logging for all MFA operations."""
    manager = context.manager
    method_name = context.method_name
    requester_id = manager.requester.id

    if context.timing == HookTiming.BEFORE:
        # Log operation start
        logger.info(f"MFA operation started: {method_name} by user {requester_id}")

        # Store audit data for after hook
        context.condition_data["audit_start"] = datetime.now(timezone.utc)
        context.condition_data["method"] = method_name
        context.condition_data["requester"] = requester_id

    elif context.timing == HookTiming.AFTER:
        # Log operation completion
        duration = datetime.now(timezone.utc) - context.condition_data["audit_start"]
        success = context.result is not None

        logger.info(
            f"MFA operation completed: {method_name} by user {requester_id}, "
            f"success={success}, duration={duration.total_seconds():.3f}s"
        )

        # For sensitive operations, log additional details
        if method_name in [
            "create",
            "delete",
            "verify_mfa_code",
            "verify_recovery_code",
        ]:
            # Could integrate with audit system here
            logger.warning(f"Sensitive MFA operation: {method_name} by {requester_id}")


def mfa_rate_limiting_hook(context: HookContext) -> None:
    """Rate limiting for MFA verification attempts."""
    method_name = context.method_name
    manager = context.manager

    # Apply rate limiting specifically to verification methods
    if method_name in ["verify_mfa_code", "verify_recovery_code"]:
        # Simple in-memory rate limiting (could be enhanced with Redis)
        if not hasattr(manager, "_rate_limit_tracker"):
            manager._rate_limit_tracker = {}

        requester_id = manager.requester.id
        current_time = datetime.now(timezone.utc)

        # Check if user has exceeded rate limit (5 attempts per minute)
        if requester_id in manager._rate_limit_tracker:
            attempts = manager._rate_limit_tracker[requester_id]
            recent_attempts = [t for t in attempts if (current_time - t).seconds < 60]

            if len(recent_attempts) >= 5:
                logger.warning(
                    f"Rate limit exceeded for MFA verification by user {requester_id}"
                )
                raise HTTPException(
                    status_code=429,
                    detail="Too many verification attempts. Please wait before trying again.",
                )

            manager._rate_limit_tracker[requester_id] = recent_attempts + [current_time]
        else:
            manager._rate_limit_tracker[requester_id] = [current_time]


class MultifactorMethodModel(
    ApplicationModel,
    UpdateMixinModel,
    UserModel.Reference.Optional,
    metaclass=ModelMeta,
):
    method_type: str = Field(..., description="Type of MFA method (totp, email, sms)")
    identifier: Optional[str] = Field(
        None, description="Phone number for SMS or backup email"
    )
    totp_secret: Optional[str] = Field(None, description="Secret key for TOTP method")
    totp_algorithm: str = Field("SHA1", description="TOTP algorithm")
    totp_digits: int = Field(6, description="Number of digits in TOTP code")
    totp_period: int = Field(30, description="TOTP code validity period in seconds")
    is_enabled: bool = Field(True, description="Whether this MFA method is enabled")
    is_primary: bool = Field(
        False, description="Whether this is the primary MFA method"
    )
    always_ask: bool = Field(False, description="Whether to always ask for this method")
    last_used: Optional[datetime] = Field(
        None, description="When this method was last used"
    )
    verification: bool = Field(False, description="Whether this method is verified")
    verification_expires_at: Optional[datetime] = Field(
        None, description="When verification expires"
    )

    @model_validator(mode="after")
    def validate_method_type(self):
        valid_methods = [
            MultifactorMethodType.TOTP,
            MultifactorMethodType.EMAIL,
            MultifactorMethodType.SMS,
        ]
        if self.method_type not in valid_methods:
            raise ValueError(
                f"Invalid method type '{self.method_type}'. Must be one of: {', '.join(valid_methods)}"
            )
        return self

    class Create(BaseModel, UserModel.Reference.ID.Optional):
        method_type: str = Field(
            ..., description="Type of MFA method (totp, email, sms)"
        )
        identifier: Optional[str] = Field(
            None, description="Phone number or email for SMS/email methods"
        )
        is_primary: bool = Field(
            False, description="Whether this is the primary MFA method"
        )
        always_ask: bool = Field(
            False, description="Whether to always ask for this method"
        )
        # TOTP-specific fields
        totp_secret: Optional[str] = Field(
            None, description="Secret key for TOTP method"
        )
        totp_algorithm: Optional[str] = Field("SHA1", description="TOTP algorithm")
        totp_digits: Optional[int] = Field(
            6, description="Number of digits in TOTP code"
        )
        totp_period: Optional[int] = Field(
            30, description="TOTP code validity period in seconds"
        )

        @model_validator(mode="after")
        def validate_method_type_and_identifier(self):
            # Validate method type
            valid_methods = [
                MultifactorMethodType.TOTP,
                MultifactorMethodType.EMAIL,
                MultifactorMethodType.SMS,
            ]
            if self.method_type not in valid_methods:
                raise ValueError(
                    f"Invalid method type '{self.method_type}'. Must be one of: {', '.join(valid_methods)}"
                )

            # Validate identifier requirement
            if self.method_type in [
                MultifactorMethodType.EMAIL,
                MultifactorMethodType.SMS,
            ]:
                if not self.identifier:
                    raise ValueError(
                        f"Identifier is required for {self.method_type} method"
                    )
            return self

    class Update(BaseModel, UserModel.Reference.ID.Optional):
        identifier: Optional[str] = Field(
            None, description="Phone number or email for SMS/email methods"
        )
        is_enabled: Optional[bool] = Field(
            None, description="Whether this MFA method is enabled"
        )
        is_primary: Optional[bool] = Field(
            None, description="Whether this is the primary MFA method"
        )
        always_ask: Optional[bool] = Field(
            None, description="Whether to always ask for this method"
        )

    class Search(
        ApplicationModel.Search, UpdateMixinModel.Search, UserModel.Reference.ID.Search
    ):
        method_type: Optional[str] = None
        identifier: Optional[StringSearchModel] = None
        is_enabled: Optional[bool] = None
        is_primary: Optional[bool] = None
        verification: Optional[bool] = None
        last_used: Optional[DateSearchModel] = None


class MultifactorMethodManager(AbstractBLLManager, RouterMixin):
    _model = MultifactorMethodModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/user/mfa"
    tags: ClassVar[Optional[List[str]]] = ["Multi-Factor Authentication"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Any = None,
    ) -> None:
        """Initialize MultifactorMethodManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            model_registry: Model registry for dynamic model handling (required)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )
        self._recovery_codes = None

    @property
    def recovery_codes(self) -> "MultifactorRecoveryCodeManager":
        """Get the recovery codes manager for this MFA method manager."""
        if self._recovery_codes is None:
            self._recovery_codes = MultifactorRecoveryCodeManager(
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._recovery_codes

    def create_validation(self, entity):
        """Validate MFA method creation"""
        # Check if user exists (Keep proper layer separation, leveraging UserManager permissions logic)
        if entity.user_id:
            try:
                UserManager(
                    requester_id=self.requester.id,
                    model_registry=self.model_registry,
                ).get(id=entity.user_id)
            except Exception as e:
                logger.error(f"Error validating user existence: {e}")
                raise HTTPException(status_code=404, detail="User not found")

        # For TOTP methods, ensure required fields are present
        if entity.method_type == MultifactorMethodType.TOTP:
            if not entity.totp_secret:
                # Generate a secret if not provided
                entity.totp_secret = self.generate_totp_secret()

        # Check if setting as primary - only one primary allowed per user
        if entity.is_primary and entity.user_id:
            existing_primary = self.list(
                user_id=entity.user_id,
                is_primary=True,
                is_enabled=True,
            )
            if existing_primary:
                # Remove primary flag from existing methods using the manager
                for method in existing_primary:
                    method_id = (
                        method.get("id") if isinstance(method, dict) else method.id
                    )
                    # Use the manager's update method instead of direct DB access
                    self.update(method_id, is_primary=False)

    def create(self, **kwargs):
        """Create new MFA method"""
        return super().create(**kwargs)

    def generate_totp_secret(self) -> str:
        """Generate a secure TOTP secret"""
        # Generate 32 random bytes and encode as base32
        secret_bytes = secrets.token_bytes(32)
        import base64

        return base64.b32encode(secret_bytes).decode("utf-8")

    # Process-local replay-protection set: (secret_fingerprint, code).
    # A real deployment should back this with Redis or a DB column so it
    # survives restarts and is shared across workers. The in-memory store
    # is sufficient to defeat the obvious replay-within-window case.
    _USED_TOTP_CODES: ClassVar[set] = set()

    def verify_totp_code(
        self,
        secret: str,
        code: str,
        algorithm: str = "SHA1",
        digits: int = 6,
        period: int = 30,
    ) -> bool:
        """Verify a TOTP code against a secret.

        Replay-protected: a code that has already been accepted for the
        same secret within the current window is rejected on subsequent
        attempts. Drift tolerance is one 30-second window (60s total).
        """
        try:
            import pyotp

            totp = pyotp.TOTP(
                secret, digest=algorithm.lower(), digits=digits, interval=period
            )
            if not totp.verify(code, valid_window=1):
                return False

            # Replay check: same (secret, code) pair must not be re-used.
            # We key on a SHA-256 fingerprint of the secret so the raw
            # secret is never stored in the replay set.
            import hashlib

            fingerprint = hashlib.sha256(secret.encode()).hexdigest()
            replay_key = (fingerprint, code)
            if replay_key in self._USED_TOTP_CODES:
                return False
            self._USED_TOTP_CODES.add(replay_key)
            return True
        except Exception as e:
            logger.error(f"Error verifying TOTP code: {e}")
            return False

    def send_mfa_code(self, method_id: str) -> Dict[str, str]:
        """Send MFA code for email/SMS methods"""
        method = self.get(id=method_id)

        if method.method_type == MultifactorMethodType.EMAIL:
            return self._send_email_code(method)
        elif method.method_type == MultifactorMethodType.SMS:
            return self._send_sms_code(method)
        else:
            raise HTTPException(
                status_code=400,
                detail="Code sending not supported for this method type",
            )

    def _send_email_code(self, method: MultifactorMethodModel) -> Dict[str, str]:
        """Send MFA code via email"""
        # Implementation would integrate with email extension
        raise HTTPException(status_code=501, detail="Email MFA not yet implemented")

    def _send_sms_code(self, method: MultifactorMethodModel) -> Dict[str, str]:
        """Send MFA code via SMS"""
        # Implementation would integrate with SMS provider
        raise HTTPException(status_code=501, detail="SMS MFA not yet implemented")

    def verify_mfa_code(self, method_id: str, code: str) -> bool:
        """Verify MFA code for any method type"""
        method = self.get(id=method_id)

        if not method or not method.is_enabled:
            return False

        if method.method_type == MultifactorMethodType.TOTP:
            return self.verify_totp_code(
                method.totp_secret,
                code,
                method.totp_algorithm,
                method.totp_digits,
                method.totp_period,
            )
        else:
            # For email/SMS, this would verify against stored temporary codes
            # Implementation depends on how codes are stored and expire
            raise HTTPException(
                status_code=501,
                detail="Code verification for this method type not yet implemented",
            )


class MultifactorRecoveryCodeModel(
    ApplicationModel,
    UpdateMixinModel,
    MultifactorMethodModel.Reference,
    metaclass=ModelMeta,
):
    code_hash: str = Field(..., description="Hashed recovery code")
    code_salt: str = Field(..., description="Salt for the recovery code")
    is_used: bool = Field(False, description="Whether this recovery code has been used")
    used_at: Optional[datetime] = Field(
        None, description="When this recovery code was used"
    )
    created_ip: Optional[str] = Field(
        None, description="IP address where code was created"
    )

    class Create(BaseModel, MultifactorMethodModel.Reference.ID):
        created_ip: Optional[str] = Field(
            None, description="IP address where code was created"
        )
        code_hash: str = Field(..., description="Hashed recovery code")
        code_salt: str = Field(
            ..., description="Salt used for hashing the recovery code"
        )

    class Update(BaseModel):
        is_used: Optional[bool] = Field(
            None, description="Whether this recovery code has been used"
        )
        used_at: Optional[datetime] = Field(
            None, description="When this recovery code was used"
        )

    class Search(
        ApplicationModel.Search,
        UpdateMixinModel.Search,
        MultifactorMethodModel.Reference.ID.Search,
    ):
        is_used: Optional[bool] = None
        created_ip: Optional[StringSearchModel] = None
        used_at: Optional[DateSearchModel] = None


class MultifactorRecoveryCodeManager(AbstractBLLManager):
    _model = MultifactorRecoveryCodeModel

    # RouterMixin configuration
    prefix: ClassVar[Optional[str]] = "/v1/user/mfa/recovery"
    tags: ClassVar[Optional[List[str]]] = ["Multi-Factor Authentication"]
    auth_type: ClassVar[AuthType] = AuthType.JWT

    def __init__(
        self,
        requester_id: str,
        target_id: Optional[str] = None,
        target_team_id: Optional[str] = None,
        model_registry: Any = None,
    ) -> None:
        """Initialize MultifactorRecoveryCodeManager.

        Args:
            requester_id: ID of the user making the request
            target_id: ID of the target entity for operations
            target_team_id: ID of the target team
            model_registry: Model registry for dynamic model handling (required)
        """
        super().__init__(
            requester_id=requester_id,
            target_id=target_id,
            target_team_id=target_team_id,
            model_registry=model_registry,
        )

    def generate_recovery_codes(
        self, multifactormethod_id: str, count: int = 10
    ) -> List[str]:
        """Generate recovery codes for an MFA method.

        Format: ``XXXXX-XXXXX`` (10 alphanumeric chars + dash). 36^10 ≈
        3.7×10^15 combinations — well beyond brute-force given the MFA
        attempt rate-limit at the verify path.
        """
        codes = []

        for _ in range(count):
            first_part = "".join(
                secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5)
            )
            second_part = "".join(
                secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5)
            )
            code = f"{first_part}-{second_part}"
            codes.append(code)

            # Hash and store the code
            salt = bcrypt.gensalt()
            code_hash = bcrypt.hashpw(code.encode(), salt).decode()

            self.create(
                multifactormethod_id=multifactormethod_id,
                code_hash=code_hash,
                code_salt=salt.decode(),
                is_used=False,
                created_ip=None,  # Could be populated from request context
            )

        return codes

    def verify_recovery_code(self, multifactormethod_id: str, code: str) -> bool:
        """Verify and mark a recovery code as used"""
        # Get all unused recovery codes for this MFA method
        recovery_codes = self.list(
            multifactormethod_id=multifactormethod_id,
            is_used=False,
        )

        for recovery_code in recovery_codes:
            code_hash = (
                recovery_code.get("code_hash")
                if isinstance(recovery_code, dict)
                else recovery_code.code_hash
            )
            code_salt = (
                recovery_code.get("code_salt")
                if isinstance(recovery_code, dict)
                else recovery_code.code_salt
            )

            # Verify the code
            if bcrypt.checkpw(code.encode(), code_hash.encode()):
                # Mark as used using the manager's update method
                recovery_id = (
                    recovery_code.get("id")
                    if isinstance(recovery_code, dict)
                    else recovery_code.id
                )
                self.update(
                    recovery_id,
                    is_used=True,
                    used_at=datetime.now(timezone.utc),
                )
                return True

        return False


# TOTP secret validation hook function
def totp_secret_validation_hook(context: HookContext) -> None:
    """Ensure TOTP secrets are properly generated and validated."""
    kwargs = context.kwargs

    # If this is creating a TOTP method, ensure security requirements
    if kwargs.get("method_type") == MultifactorMethodType.TOTP:
        # Validate TOTP parameters
        totp_digits = kwargs.get("totp_digits", 6)
        totp_period = kwargs.get("totp_period", 30)

        if totp_digits not in [6, 8]:
            logger.warning(f"Invalid TOTP digits: {totp_digits}, forcing to 6")
            context.kwargs["totp_digits"] = 6

        if totp_period not in [15, 30, 60]:
            logger.warning(f"Invalid TOTP period: {totp_period}, forcing to 30")
            context.kwargs["totp_period"] = 30

        # Log TOTP method creation for security audit
        logger.info(
            f"TOTP method being created for user {kwargs.get('user_id', 'unknown')}"
        )


# Apply security and audit hooks to MFA manager classes
# Class-level hooks apply to ALL methods of these managers

# Apply hooks to MultifactorMethodManager
hook_bll(MultifactorMethodManager, timing=HookTiming.BEFORE, priority=1)(
    security_validation_hook
)
hook_bll(MultifactorMethodManager, timing=HookTiming.BEFORE, priority=5)(
    audit_mfa_operations
)
hook_bll(MultifactorMethodManager, timing=HookTiming.AFTER, priority=95)(
    audit_mfa_operations
)
hook_bll(MultifactorMethodManager, timing=HookTiming.BEFORE, priority=10)(
    mfa_rate_limiting_hook
)

# Apply hooks to MultifactorRecoveryCodeManager
hook_bll(MultifactorRecoveryCodeManager, timing=HookTiming.BEFORE, priority=1)(
    security_validation_hook
)
hook_bll(MultifactorRecoveryCodeManager, timing=HookTiming.BEFORE, priority=5)(
    audit_mfa_operations
)
hook_bll(MultifactorRecoveryCodeManager, timing=HookTiming.AFTER, priority=95)(
    audit_mfa_operations
)
hook_bll(MultifactorRecoveryCodeManager, timing=HookTiming.BEFORE, priority=10)(
    mfa_rate_limiting_hook
)

# Apply specific hook for TOTP secret generation
hook_bll(MultifactorMethodManager.create, timing=HookTiming.BEFORE, priority=15)(
    totp_secret_validation_hook
)
