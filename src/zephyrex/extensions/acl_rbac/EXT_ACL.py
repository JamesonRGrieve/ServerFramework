# SPDX-License-Identifier: AGPL-3.0-or-later
"""acl_rbac extension definition.

Owns the per-record ACL grant table. ``BLL_ACL`` registers the hooks that
let core code (notably `database/StaticPermissions`) resolve the SA model +
create grants without importing the extension; model discovery imports it for
every app that loads ``acl_rbac``.
"""

from typing import ClassVar, List

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AclRbacExtension(AbstractStaticExtension):
    name: ClassVar[str] = "acl_rbac"
    description: ClassVar[str] = (
        "Per-record ACL with the canonical 6-verb shape (Scope #5)"
    )
    extension_dependencies: ClassVar[List[str]] = []
