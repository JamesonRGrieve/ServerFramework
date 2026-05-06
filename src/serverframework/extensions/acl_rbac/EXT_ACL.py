"""acl_rbac extension definition."""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AclRbacExtension(AbstractStaticExtension):
    name: ClassVar[str] = "acl_rbac"
    description: ClassVar[str] = (
        "Per-record ACL with the canonical 6-verb shape (Scope #5)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.logic.BLL_Auth import PermissionModel

        return [PermissionModel]
