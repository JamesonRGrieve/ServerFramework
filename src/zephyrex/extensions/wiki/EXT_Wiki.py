# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wiki content access: search, article retrieval and summaries over
Wikipedia, Fandom and Kanka, through the provider rotation.

Each provider instance carries its own target: ``language`` for Wikipedia,
``wiki_domain`` for Fandom, a ``campaign_id`` and API key for Kanka, each
falling back to the environment. An ability runs on the first healthy
instance of the extension's root rotation.
"""

import re
from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.Environment import env
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

WIKI_REQUEST_TIMEOUT_SECONDS = 10.0
NO_SUMMARY = "No summary available"
# A wiki language code or subdomain becomes part of a hostname.
_HOST_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_TAG = re.compile(r"<[^>]*>")


def plain_text(html: str) -> str:
    """Text of an HTML fragment, its tags dropped."""
    return _TAG.sub("", html)


def host_label(value: str, setting: str) -> str:
    """``value`` as one DNS label, refused otherwise: it is interpolated
    into a hostname, where ``evil.example/#`` would redirect the request."""
    label = value.strip().lower()
    if not _HOST_LABEL.match(label):
        raise InvalidInputExternalError(
            f"{setting} {value!r} is not a valid hostname label"
        )
    return label


def user_agent() -> str:
    """``zephyrex/<version> (<this deployment's source>)``: who is asking,
    per Wikimedia's User-Agent policy."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        release = version("zephyrex")
    except PackageNotFoundError:
        release = "unknown"
    return f"zephyrex/{release} ({env('APP_REPOSITORY')})"


def first_paragraph(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return paragraphs[0] if paragraphs else NO_SUMMARY


class AbstractWikiProvider(AbstractStaticProvider):
    """A wiki platform. Every ability takes the rotated instance and reads
    its settings, falling back to the environment."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {
        "search_wiki",
        "get_wiki_article",
        "get_wiki_summary",
    }
    _env: ClassVar[Dict[str, Any]] = {}

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def http(cls) -> ProviderHTTPClient:
        return ProviderHTTPClient(
            policy=ClientPolicy(timeout=WIKI_REQUEST_TIMEOUT_SECONDS),
            provider_name=cls.name,
            provider=cls,
        )

    @classmethod
    async def get_json(
        cls,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """The decoded JSON answer. Every request names this software:
        Wikimedia refuses clients without a descriptive User-Agent."""
        return await cls.http().get(
            url, params=params, headers={"User-Agent": user_agent(), **(headers or {})}
        )

    @classmethod
    @abstractmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Articles matching ``query``: each a ``title``, ``snippet`` and ``url``."""

    @classmethod
    @abstractmethod
    async def get_article(
        cls, instance: ProviderInstanceModel, title: str
    ) -> Dict[str, Any]:
        """The article titled ``title``: its ``title``, plain-text ``content``,
        ``url`` and ``pageid``; empty strings when there is none."""

    @classmethod
    @abstractmethod
    async def get_summary(cls, instance: ProviderInstanceModel, title: str) -> str:
        """The leading excerpt of the article titled ``title``."""

    @classmethod
    def services(cls) -> List[str]:
        return ["wiki", "knowledge_base"]


class EXT_Wiki(AbstractStaticExtension):
    name: ClassVar[str] = "wiki"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Wiki search, articles and summaries across Wikipedia, Fandom and Kanka"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {
        "search_wiki",
        "get_wiki_article",
        "get_wiki_summary",
    }

    @classmethod
    @ability("search_wiki")
    async def search_wiki(cls, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Articles matching ``query``, with provider failover."""
        result: List[Dict[str, Any]] = await cls.rotate_provider("search", query, limit)
        return result

    @classmethod
    @ability("get_wiki_article")
    async def get_wiki_article(cls, title: str) -> Dict[str, Any]:
        """The full plain-text article titled ``title``."""
        result: Dict[str, Any] = await cls.rotate_provider("get_article", title)
        return result

    @classmethod
    @ability("get_wiki_summary")
    async def get_wiki_summary(cls, title: str) -> str:
        """The leading excerpt of the article titled ``title``."""
        return str(await cls.rotate_provider("get_summary", title))
