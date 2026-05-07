"""rpg_state extension definition.

Hard-depends on ``genealogy`` (widens PersonModel and RelationshipModel
via @extension_model). ``rpg_log`` depends on rpg_state for trait /
item / location references.
"""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class RPGStateExtension(AbstractStaticExtension):
    name: ClassVar[str] = "rpg_state"
    description: ClassVar[str] = (
        "Present-state schema for RPG campaigns (system-agnostic). "
        "Widens genealogy.PersonModel to serve as the character table."
    )
    extension_dependencies: ClassVar[List[str]] = ["genealogy"]

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.rpg_state.BLL_RPGState import ALL_MODELS

        return list(ALL_MODELS)
