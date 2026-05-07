"""rpg_state extension definition.

Owns the present-state tables for an RPG campaign. No upstream extension
dependencies; ``rpg_log`` depends on this for character / item / location
references.
"""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class RPGStateExtension(AbstractStaticExtension):
    name: ClassVar[str] = "rpg_state"
    description: ClassVar[str] = (
        "Present-state schema for RPG campaigns (system-agnostic)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.rpg_state.BLL_RPGState import ALL_MODELS

        return list(ALL_MODELS)
