# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_session extension definition.

Registers the ``SessionModel`` with the framework's model registry.
``BLL_Session`` wires the four ``_session_hooks`` consumed by core
``UserManager`` (``issue_session``, ``enforce_not_revoked``,
``manager_factory``, ``revoke_user_sessions``) when it is imported. Without
this extension on ``APP_EXTENSIONS``, core JWTs are stateless and the per-user
"sessions" surface is absent.
"""

from typing import ClassVar, List

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
