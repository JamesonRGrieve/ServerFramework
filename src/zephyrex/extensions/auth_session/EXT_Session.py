"""auth_session extension definition.

Registers the ``SessionModel`` with the framework's model registry and
wires the four ``_session_hooks`` consumed by core ``UserManager``:
``issue_session``, ``enforce_not_revoked``, ``manager_factory``, and
``revoke_user_sessions``. Without this extension on ``APP_EXTENSIONS``,
core JWTs are stateless and the per-user "sessions" surface is absent.
"""

from typing import ClassVar, List, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AuthSessionExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_session"
    description: ClassVar[str] = (
        "Persisted session rows that back JWT revocation, refresh, and "
        "device-pairing pending-state."
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from zephyrex.extensions.auth_session.BLL_Session import (
            SessionModel,
        )

        return [SessionModel]

    @classmethod
    def on_load(cls) -> None:
        from zephyrex.extensions.auth_session.BLL_Session import (
            enforce_not_revoked,
            issue_session,
            revoke_user_sessions,
            session_manager_factory,
        )
        from zephyrex.logic.BLL_Auth import register_session_hooks

        register_session_hooks(
            issue_session=issue_session,
            enforce_not_revoked=enforce_not_revoked,
            manager_factory=session_manager_factory,
            revoke_user_sessions=revoke_user_sessions,
        )
