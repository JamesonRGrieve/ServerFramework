"""Extension manifest for meta_labels.

Owns ``LabelModel`` and ``LabelLinkModel``. The original per-entity join
tables (``AgentLabel``, ``PromptLabel``, ``ChainLabel``, …) have been
collapsed into a single polymorphic link.
"""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Meta_Labels(AbstractStaticExtension):
    name: ClassVar[str] = "meta_labels"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Polymorphic label catalog and attachment table."

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])

    _abilities: ClassVar[Set[str]] = {"labels_attach", "labels_detach"}
    _providers: ClassVar[List] = []
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.meta_labels import (  # noqa: F401
            BLL_Meta_Labels,
        )

        logger.debug("meta_labels initialized")
        return True
