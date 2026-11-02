# SPDX-License-Identifier: AGPL-3.0-or-later
"""A private list of gift ideas for the people in someone's life, and links
to the shop that carry the operator's affiliate tag (``BLL_Gifts``)."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Logging import logger


class EXT_Gifts(AbstractStaticExtension):
    name: ClassVar[str] = "gifts"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "A private list of gift ideas for the people in someone's life, with links to the shop"
    )

    _env: ClassVar[Dict[str, Any]] = {
        "AMAZON_ASSOCIATE_TAG": "",
        "AMAZON_DOMAIN": "www.amazon.com",
    }
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = set()
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.gifts import BLL_Gifts  # noqa: F401

        logger.debug("gifts initialized")
        return True
