"""genealogy extension definition.

Foundational. No upstream extension dependencies. Downstream extensions
(notably ``rpg_state``) widen ``RelationshipModel`` via
``@extension_model`` to add their own endpoint columns.
"""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class GenealogyExtension(AbstractStaticExtension):
    name: ClassVar[str] = "genealogy"
    description: ClassVar[str] = (
        "Person and labelled-relationship graph; family-tree algorithms"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.genealogy.BLL_Genealogy import ALL_MODELS

        return list(ALL_MODELS)
