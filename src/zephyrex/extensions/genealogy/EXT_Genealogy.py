# SPDX-License-Identifier: AGPL-3.0-or-later
"""genealogy extension definition.

Foundational: no extension dependencies. Downstream extensions (notably
``rpg_state``) widen ``RelationshipModel`` via ``@extension_model`` to add
their own endpoint columns.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Genealogy(AbstractStaticExtension):
    name: ClassVar[str] = "genealogy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Person and labelled-relationship graph; family-tree algorithms; "
        "GEDCOM import and export"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {
        "genealogy_ancestors",
        "genealogy_descendants",
        "genealogy_kinship",
        "genealogy_gedcom",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.genealogy import BLL_Genealogy  # noqa: F401

        logger.debug("genealogy initialized")
        return True
