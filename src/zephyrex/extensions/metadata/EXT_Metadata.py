# SPDX-License-Identifier: AGPL-3.0-or-later
"""metadata extension definition.

Owns the unified `metadata` table. ``BLL_Metadata`` registers the hooks that
let core BLL_Auth talk to the extension without importing it; model discovery
imports it for every app that loads ``metadata``.
"""

from typing import ClassVar, List

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class MetadataExtension(AbstractStaticExtension):
    name: ClassVar[str] = "metadata"
    description: ClassVar[str] = (
        "Free-form key/value metadata for users and teams (Scope #3)"
    )
    extension_dependencies: ClassVar[List[str]] = []
