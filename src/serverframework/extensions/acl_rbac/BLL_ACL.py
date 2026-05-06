"""Re-export shim for the per-record ACL extracted from core (Scope #5).

The canonical Pydantic class still lives in `logic/BLL_Auth.py`; this module
provides the clean import path. Code that consumed `BLL_Auth.PermissionModel`
should switch to:

    from serverframework.extensions.acl_rbac.BLL_ACL import (
        PermissionModel, PermissionManager,
    )
"""

from serverframework.logic import BLL_Auth as _core


def __getattr__(name: str):
    if name in {"PermissionModel", "PermissionManager"}:
        return getattr(_core, name)
    raise AttributeError(name)


__all__ = ["PermissionModel", "PermissionManager"]
