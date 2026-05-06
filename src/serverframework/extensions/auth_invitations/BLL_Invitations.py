"""Re-export shim for the invitation models extracted from core (Scope #4).

The canonical Pydantic classes still live in `logic/BLL_Auth.py`; this module
provides the clean import path. Code that consumed `BLL_Auth.InvitationModel`
should switch to:

    from serverframework.extensions.auth_invitations.BLL_Invitations import (
        InvitationModel, InvitationManager, InviteeModel, InviteeManager,
    )
"""

from serverframework.logic import BLL_Auth as _core


def __getattr__(name: str):
    if name in {
        "InvitationModel",
        "InvitationManager",
        "InviteeModel",
        "InviteeManager",
    }:
        return getattr(_core, name)
    raise AttributeError(name)


__all__ = [
    "InvitationModel",
    "InvitationManager",
    "InviteeModel",
    "InviteeManager",
]
