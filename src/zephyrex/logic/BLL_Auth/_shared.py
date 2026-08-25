import base64 as _b64
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import (
    TYPE_CHECKING,
    Callable,
    ClassVar,
    Dict,
    List,
    Optional,
    Tuple,
)

if TYPE_CHECKING:
    from zephyrex.logic.BLL_Auth.user import UserModel

import bcrypt
from fastapi import HTTPException

from pydantic import BaseModel, Field


def _validate_team_name(v):
    if v is not None:
        v = v.strip()
        if not v:
            raise ValueError("Team name cannot be empty")
    return v


from zephyrex.database.HookRegistries import (
    _acl_hooks as _acl_hooks,
    _invitation_hooks as _invitation_hooks,
    register_acl_hooks as register_acl_hooks,
    register_invitation_hooks as register_invitation_hooks,
)
from zephyrex.lib.Environment import env
from zephyrex.pydantic2.registry import BaseModel  # type: ignore[no-redef]

# Resolve _BCRYPT_ROUNDS from env now that the import is available.
# Using gensalt() without a rounds argument leaves the cost at whatever the
# installed bcrypt's default is, which has shifted across library versions
# and is invisible to operators. Configurable via BCRYPT_ROUNDS (default 12).
_BCRYPT_ROUNDS = int(env("BCRYPT_ROUNDS"))
_DUMMY_BCRYPT_HASH = bcrypt.hashpw(
    b"timing-equalization-dummy", bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
)


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


# The auth_invitations (``_invitation_hooks``) and acl_rbac (``_acl_hooks``)
# hook registries — plus their ``register_*`` helpers — now live at the
# database layer in ``database/HookRegistries.py``, at/below their
# ``StaticPermissions`` consumer (issue #222). They are re-exported at the
# top of this module for backward compatibility; extensions still register
# into them, and this module still reads ``_invitation_hooks`` internally.


# The privacy (``_pii_hooks``) and federation (``_registry_hooks``) hook
# registries — plus their ``register_*`` helpers — now live at the lib layer in
# ``lib/Hooks.py``, at/below their lib/ consumers (issue #221; same inversion as
# issue #222). Re-exported here for backward compatibility with the privacy and
# federation extensions that register into them through BLL_Auth; the dict
# objects are shared by identity so registrations remain visible either way.
from zephyrex.lib.Hooks import (
    _pii_hooks as _pii_hooks,
    _registry_hooks as _registry_hooks,
    register_pii_hooks as register_pii_hooks,
    register_registry_hooks as register_registry_hooks,
)
