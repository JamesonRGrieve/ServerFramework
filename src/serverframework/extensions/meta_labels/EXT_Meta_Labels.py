"""Extension manifest for meta_labels.

Owns ``LabelModel`` and ``LabelLinkModel``. The original per-entity join
tables (``AgentLabel``, ``PromptLabel``, ``ChainLabel``, …) have been
collapsed into a single polymorphic link.
"""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Dependencies import Dependencies
from serverframework.lib.Logging import logger


class EXT_Meta_Labels(AbstractStaticExtension):
    name: ClassVar[str] = "meta_labels"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Polymorphic label catalog and attachment table."
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {"labels_attach", "labels_detach"}
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from serverframework.extensions.meta_labels import (  # noqa: F401
            BLL_Meta_Labels,
        )

        logger.debug("meta_labels initialized")
        return True

    @classmethod
    def on_start(cls) -> bool:
        return True

    @classmethod
    def on_stop(cls) -> bool:
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
