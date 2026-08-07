"""acl_rbac extension definition.

Owns the per-record ACL grant table. Hooks let core code (notably
`database/StaticPermissions`) resolve the SA model + create grants
without importing this module directly.
"""

from typing import Any, ClassVar, List, Optional, Type

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


def _permission_db_class(declarative_base):
    """Resolve `PermissionModel.DB(declarative_base)` for callers that
    have a declarative base in hand (StaticPermissions filter generation)."""
    from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionModel

    return PermissionModel.DB(declarative_base)


def _create_permission(
    *,
    resource_type: str,
    resource_id: str,
    user_id: Optional[str] = None,
    team_id: Optional[str] = None,
    role_id: Optional[str] = None,
    can_view: bool = False,
    can_execute: bool = False,
    can_copy: bool = False,
    can_edit: bool = False,
    can_delete: bool = False,
    can_share: bool = False,
    requester_id: Optional[str] = None,
    model_registry: Any = None,
):
    """Create a permission row through the canonical manager. Used by
    AbstractDBTest and any future caller that needs a typed grant insert."""
    from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionManager
    from zephyrex.lib.Environment import env

    with PermissionManager(
        requester_id=requester_id or env("ROOT_ID"),
        model_registry=model_registry,
    ) as mgr:
        return mgr.create(
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
            can_view=can_view,
            can_execute=can_execute,
            can_copy=can_copy,
            can_edit=can_edit,
            can_delete=can_delete,
            can_share=can_share,
        )


class AclRbacExtension(AbstractStaticExtension):
    name: ClassVar[str] = "acl_rbac"
    description: ClassVar[str] = (
        "Per-record ACL with the canonical 6-verb shape (Scope #5)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from zephyrex.extensions.acl_rbac.BLL_ACL import PermissionModel

        return [PermissionModel]

    @classmethod
    def on_load(cls) -> None:
        from zephyrex.logic.BLL_Auth import register_acl_hooks

        register_acl_hooks(
            permission_db_class=_permission_db_class,
            create_permission=_create_permission,
        )
