# SPDX-License-Identifier: AGPL-3.0-or-later
"""auth_invitations extension definition.

``BLL_Invitations`` wires every place core BLL_Auth used to reach into
Invitation/Invitee through hook callables when it is imported, so the extension
can be enabled/disabled via APP_EXTENSIONS without modifying core.
"""

from typing import ClassVar, List

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AuthInvitationsExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_invitations"
    description: ClassVar[str] = (
        "Team invitation workflow with role assignment (Scope #4)"
    )
    extension_dependencies: ClassVar[List[str]] = []
