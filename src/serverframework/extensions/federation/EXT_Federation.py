"""Federation extension — GraphQL and REST upstream federation."""

from typing import Any, ClassVar, Dict, List, Set

from serverframework.extensions.AbstractExtensionProvider import AbstractStaticExtension
from serverframework.lib.Logging import logger


class EXT_Federation(AbstractStaticExtension):
    """GraphQL/REST upstream federation: schema introspection, model lift,
    surface integration.

    Federation is an advanced provider pattern, not a framework primitive.
    Most deployments don't federate upstream services. The core ships a
    generic provider system (``BLL_Providers``); federation is a
    specialization that opts in.
    """

    name: ClassVar[str] = "federation"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "GraphQL and REST upstream federation: schema introspection, "
        "model lift, surface integration"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {
        "federation_gql",
        "federation_rest",
        "federation_bootstrap",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing federation extension")
        return True

    @classmethod
    def on_start(cls) -> bool:
        logger.debug("federation extension started")
        return True

    @classmethod
    def on_stop(cls) -> bool:
        logger.debug("federation extension stopped")
        return True

    @classmethod
    def validate_config(cls) -> List[str]:
        return []

    @classmethod
    def get_abilities(cls) -> Set[str]:
        return cls._abilities.copy()

    def has_ability(self, ability: str) -> bool:
        return ability in self._abilities
