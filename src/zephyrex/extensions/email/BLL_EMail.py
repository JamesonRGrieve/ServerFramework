# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Email extension business logic: the invitation email ``InvitationManager``
sends when it adds an invitee.
"""

from typing import Any


def send_invitation_email_hook(manager: Any, entity: Any) -> None:
    """Queue the invitation email for a newly added invitee; called by
    ``InvitationManager.add_invitee``. It goes through the extension's root
    rotation, whichever providers the operator configured there."""
    from zephyrex.extensions.email.EXT_EMail import EXT_EMail

    invitation = getattr(entity, "invitation", None)
    if invitation is None or not invitation.code or not getattr(entity, "email", None):
        return
    EXT_EMail.send_invitation_email(entity=entity)
