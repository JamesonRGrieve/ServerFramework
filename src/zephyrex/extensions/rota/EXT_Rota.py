# SPDX-License-Identifier: AGPL-3.0-or-later
"""A repeating rota of nights with the people in someone's life: who each
night of the cycle is with, and one-off swaps of a night that both people
agree to (``BLL_Rota``)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Rota(AbstractStaticExtension):
    name: ClassVar[str] = "rota"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "A repeating rota of nights with the people in someone's life, and swaps both agree to"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set()
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.rota import BLL_Rota  # noqa: F401

        logger.debug("rota initialized")
        return True
