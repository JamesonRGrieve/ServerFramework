"""Federation extension — GraphQL and REST upstream federation."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


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
