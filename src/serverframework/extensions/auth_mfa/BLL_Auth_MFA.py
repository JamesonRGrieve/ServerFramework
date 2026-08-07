import secrets
import string
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

import bcrypt
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from serverframework.lib.Environment import env
from serverframework.lib.InboundSecurity import LockoutPolicy, LockoutTracker
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic import BaseModel  # type: ignore[no-redef]
from serverframework.lib.Pydantic2FastAPI import AuthType, RouterMixin, RouteType


# Encryption-at-rest for ``totp_secret``. Implementation lives in the shared
# helper so every extension that persists secrets uses the same envelope and
# fail-fast policy. ``ALLOW_PLAINTEXT_SECRETS=true`` is the only path that
# permits plaintext storage, and only outside production/staging.
from serverframework.lib.SecretEncryption import (
    decrypt_secret as decrypt_totp_secret,
    encrypt_secret as encrypt_totp_secret,
)
from serverframework.logic.AbstractLogicManager import (
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
from serverframework.logic.BLL_Auth import UserManager, UserModel


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


# M-3 — process-shared (NOT manager-instance) lockout tracker. The
# previous implementation kept the dict on ``manager._rate_limit_tracker``,
# which was rebuilt per request, so the rate-limit hook never tripped.
# Multi-worker deployments should swap this for a shared backend the
# same way ``UserManager._lockout_tracker`` is intended to be (Item 71c).
_MFA_VERIFY_LOCKOUT = LockoutTracker(
    LockoutPolicy(failures_per_window=5, window_seconds=60, lockout_seconds=300)
)


def mfa_rate_limiting_hook(context: HookContext) -> None:
    """Lockout-tracker brake on MFA verification attempts.

    5 verification attempts within a 60s sliding window from the same
    requester puts ``(requester_id, "mfa_verify")`` into a 5-minute
    lockout. The hook is BEFORE-timed: a tripped lockout returns 429
    before the verify path even runs. The hook does not record a
    failure here — verification methods themselves call
    ``record_failure`` on a wrong code; this hook is the gate that
    refuses subsequent attempts once the policy has tripped.
    """
    method_name = context.method_name
    if method_name not in ("verify_mfa_code", "verify_recovery_code"):
        return
    manager = context.manager
    if manager.requester is None or manager.requester.id is None:
        return
    actor_key = str(manager.requester.id)
    flow = "mfa_verify"
    if _MFA_VERIFY_LOCKOUT.is_locked(actor_key, flow):
        logger.warning(
            f"Rate limit exceeded for MFA verification by user {actor_key}"
        )
        raise HTTPException(
            status_code=429,
            detail="Too many verification attempts. Please wait before trying again.",
        )


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
        # M-4 — ``totp_secret`` is the server-generated TOTP seed. The
        # field stays on the schema because ``create_validation``
        # populates it before persist, but it is REJECTED at the manager
        # boundary (``MultifactorMethodManager.create``) when supplied by
        # a non-ROOT caller. A client cannot pre-image a backdoored
        # secret onto a victim's account.
        totp_secret: Optional[str] = Field(
            None,
            description=(
                "Server-populated TOTP seed. Client-supplied values are "
                "refused; the manager generates a fresh seed on create."
            ),
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
    # M-4 — explicit allow-list. The default RouteType set exposes
    # SEARCH, which leaks identifiers and ``totp_secret`` to anyone
    # with read access; a user only needs to manage their own methods.
    routes_to_register: ClassVar[Optional[List[RouteType]]] = [
        RouteType.GET,
        RouteType.LIST,
        RouteType.CREATE,
        RouteType.UPDATE,
        RouteType.DELETE,
    ]

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
            self._recovery_codes = MultifactorRecoveryCodeManager(  # type: ignore[assignment]
                requester_id=self.requester.id,
                target_id=self.target_id,
                target_team_id=self.target_team_id,
                model_registry=self.model_registry,
            )
        return self._recovery_codes  # type: ignore[return-value]

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
                # Generate a secret if not provided. The ``create``
                # method has already refused any non-ROOT client-supplied
                # value (M-4) so by this point ``entity.totp_secret`` is
                # either empty (the common path) or a ROOT-vetted seed.
                entity.totp_secret = self.generate_totp_secret()
            # H-1 — encrypt at rest before the row hits the DB. The seed
            # round-trips through `decrypt_totp_secret` at verification.
            entity.totp_secret = encrypt_totp_secret(entity.totp_secret)

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
        """Create new MFA method.

        M-4 — refuse a client-supplied ``totp_secret``. The seed is
        server-generated in ``create_validation``; accepting a value
        here would let a compromised UI (or future router-config drift
        that re-allows ``totp_secret`` on the Create schema) inject a
        backdoored secret onto a victim's account. ROOT may pass a
        seed for migration tooling; encryption-at-rest is performed
        downstream in ``create_validation``.
        """
        from serverframework.database.StaticPermissions import is_root_id

        if (
            "totp_secret" in kwargs
            and kwargs["totp_secret"]
            and not is_root_id(self.requester.id)
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "totp_secret is server-generated; client-supplied "
                    "values are refused"
                ),
            )
        return super().create(**kwargs)

    def update(self, id: str, **kwargs):
        """Update MFA method.

        M-4 — refuse a client-supplied ``totp_secret`` rotation. To
        rotate a TOTP seed the user deletes the existing method and
        creates a new one (or ROOT performs the migration).
        """
        from serverframework.database.StaticPermissions import is_root_id

        if "totp_secret" in kwargs and kwargs["totp_secret"]:
            if not is_root_id(self.requester.id):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "totp_secret cannot be rotated via update; delete "
                        "the method and create a new one"
                    ),
                )
            kwargs["totp_secret"] = encrypt_totp_secret(kwargs["totp_secret"])
        return super().update(id, **kwargs)

    def generate_totp_secret(self) -> str:
        """Generate a secure TOTP secret"""
        # Generate 32 random bytes and encode as base32
        secret_bytes = secrets.token_bytes(32)
        import base64

        return base64.b32encode(secret_bytes).decode("utf-8")

    # TTL must comfortably exceed the 60s drift window pyotp's
    # ``valid_window=1`` accepts so a code that has just been used can't be
    # replayed within the same window.
    _TOTP_REPLAY_TTL_SECONDS: ClassVar[int] = 120

    @classmethod
    def _replay_key(cls, secret_fingerprint: str, code: str) -> str:
        return f"mfa-totp:{secret_fingerprint}:{code}"

    @classmethod
    def _record_used_totp(cls, secret_fingerprint: str, code: str) -> None:
        """Remember that a (secret, code) pair has been accepted.

        Goes through the shared :class:`ReplayCache` so multi-worker
        deployments that install a Valkey/Postgres-backed cache see a
        consistent view. The default in-memory cache is fine for
        single-process testing.
        """
        from serverframework.lib.ReplayCache import get_replay_cache

        get_replay_cache().mark_used(
            cls._replay_key(secret_fingerprint, code),
            ttl_seconds=cls._TOTP_REPLAY_TTL_SECONDS,
        )

    @classmethod
    def _is_totp_replayed(cls, secret_fingerprint: str, code: str) -> bool:
        """Return True if this (secret, code) pair has already been used."""
        from serverframework.lib.ReplayCache import get_replay_cache

        return get_replay_cache().is_used(cls._replay_key(secret_fingerprint, code))

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
            # secret is never stored in the replay cache.
            import hashlib

            fingerprint = hashlib.sha256(secret.encode()).hexdigest()
            if self._is_totp_replayed(fingerprint, code):
                return False
            self._record_used_totp(fingerprint, code)
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
        actor_key = (
            str(self.requester.id) if self.requester and self.requester.id else None
        )

        if not method or not method.is_enabled:
            if actor_key:
                _MFA_VERIFY_LOCKOUT.record_failure(actor_key, "mfa_verify")
            return False

        if method.method_type == MultifactorMethodType.TOTP:
            # H-1 — secret is Fernet-encrypted at rest; decrypt for the
            # verification math, never re-store the cleartext.
            ok = self.verify_totp_code(
                decrypt_totp_secret(method.totp_secret),
                code,
                method.totp_algorithm,
                method.totp_digits,
                method.totp_period,
            )
            if actor_key:
                if ok:
                    _MFA_VERIFY_LOCKOUT.clear(actor_key, "mfa_verify")
                else:
                    _MFA_VERIFY_LOCKOUT.record_failure(actor_key, "mfa_verify")
            return ok
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
    MultifactorMethodModel.Reference,  # type: ignore[name-defined]
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

    class Create(BaseModel, MultifactorMethodModel.Reference.ID):  # type: ignore[name-defined]
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
        MultifactorMethodModel.Reference.ID.Search,  # type: ignore[name-defined]
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
        self, multifactor_method_id: str, count: int = 10
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

            # Hash and store the code. L-5 — pin rounds via the framework
            # constant so cost stays consistent across releases.
            from serverframework.logic.BLL_Auth import _BCRYPT_ROUNDS as _ROUNDS

            salt = bcrypt.gensalt(rounds=_ROUNDS)
            code_hash = bcrypt.hashpw(code.encode(), salt).decode()

            self.create(
                multifactor_method_id=multifactor_method_id,
                code_hash=code_hash,
                code_salt=salt.decode(),
                is_used=False,
                created_ip=None,  # Could be populated from request context
            )

        return codes

    def verify_recovery_code(self, multifactor_method_id: str, code: str) -> bool:
        """Verify and mark a recovery code as used"""
        actor_key = (
            str(self.requester.id) if self.requester and self.requester.id else None
        )
        # Get all unused recovery codes for this MFA method
        recovery_codes = self.list(
            multifactor_method_id=multifactor_method_id,
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
            if bcrypt.checkpw(code.encode(), code_hash.encode()):  # type: ignore[union-attr]
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
                if actor_key:
                    _MFA_VERIFY_LOCKOUT.clear(actor_key, "mfa_verify")
                return True

        if actor_key:
            _MFA_VERIFY_LOCKOUT.record_failure(actor_key, "mfa_verify")
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
