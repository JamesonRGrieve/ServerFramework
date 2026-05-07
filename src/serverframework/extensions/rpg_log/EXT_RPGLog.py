"""rpg_log extension definition.

Owns event-log tables. Depends on rpg_state for character / location /
item / trait / campaign references.
"""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class RPGLogExtension(AbstractStaticExtension):
    name: ClassVar[str] = "rpg_log"
    description: ClassVar[str] = (
        "Event-log schema for RPG campaigns (combat, dialogue, transactions)"
    )
    extension_dependencies: ClassVar[List[str]] = ["rpg_state"]

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.rpg_log.BLL_RPGLog import ALL_MODELS

        return list(ALL_MODELS)
