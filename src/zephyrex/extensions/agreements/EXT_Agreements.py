# SPDX-License-Identifier: AGPL-3.0-or-later
"""Written agreements between people: what they have agreed, likes and
limits included, which each of them confirms and they revisit together
(``BLL_Agreements``)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Agreements(AbstractStaticExtension):
    name: ClassVar[str] = "agreements"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Written agreements between people, confirmed by each of them and revisited together"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set()
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.agreements import BLL_Agreements  # noqa: F401

        logger.debug("agreements initialized")
        return True
