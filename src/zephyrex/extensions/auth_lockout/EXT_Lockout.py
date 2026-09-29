# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_lockout extension definition.

Registers the `FailedLoginAttempt` model. ``BLL_Lockout`` wires the
`UserManager.login` hooks that consult the per-user threshold + record failures
into the durable table when it is imported.
"""

from typing import ClassVar, List

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AuthLockoutExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_lockout"
    description: ClassVar[str] = (
        "Persisted failed-login records and per-user lockout policy"
    )
    extension_dependencies: ClassVar[List[str]] = []
