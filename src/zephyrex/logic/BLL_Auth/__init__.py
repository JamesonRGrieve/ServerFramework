from zephyrex.logic.BLL_Auth._shared import (
    _BCRYPT_ROUNDS as _BCRYPT_ROUNDS,
    _acl_hooks as _acl_hooks,
    _invitation_hooks as _invitation_hooks,
    _lockout_hooks as _lockout_hooks,
    _metadata_hooks as _metadata_hooks,
    _pii_hooks as _pii_hooks,
    _registry_hooks as _registry_hooks,
    _session_hooks as _session_hooks,
    InvalidGrantError as InvalidGrantError,
    OneTimeTokenMixin as OneTimeTokenMixin,
    PasswordlessGrantRegistry as PasswordlessGrantRegistry,
    PendingSessionError as PendingSessionError,
    register_acl_hooks as register_acl_hooks,
    register_invitation_hooks as register_invitation_hooks,
    register_lockout_hooks as register_lockout_hooks,
    register_metadata_hooks as register_metadata_hooks,
    register_pii_hooks as register_pii_hooks,
    register_registry_hooks as register_registry_hooks,
    register_session_hooks as register_session_hooks,
    reset_session_hooks as reset_session_hooks,
)
from zephyrex.logic.BLL_Auth.user import (
    UserCredentialManager as UserCredentialManager,
    UserCredentialModel as UserCredentialModel,
    UserManager as UserManager,
    UserModel as UserModel,
)
from zephyrex.logic.BLL_Auth.team import (
    TeamManager as TeamManager,
    TeamModel as TeamModel,
)
from zephyrex.logic.BLL_Auth.role import (
    RoleManager as RoleManager,
    RoleModel as RoleModel,
)
from zephyrex.logic.BLL_Auth.user_team import (
    UserTeamManager as UserTeamManager,
    UserTeamModel as UserTeamModel,
)
from zephyrex.logic.BLL_Auth.rate_limit import (
    RateLimitPolicyManager as RateLimitPolicyManager,
    RateLimitPolicyModel as RateLimitPolicyModel,
)


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


from zephyrex.lib.AuthProvider import register_auth_provider

register_auth_provider(UserManager)
