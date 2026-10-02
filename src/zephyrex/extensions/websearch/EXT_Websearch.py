# SPDX-License-Identifier: AGPL-3.0-or-later
"""Web search and page reading.

``web_search`` asks a search engine (Brave Search's API or a SearXNG
server) through the rotation, failing over between them. ``fetch_page``
reads one page here: through the SSRF guard on the address and every
redirect (a page cannot steer the server into its own network), reading
at most ``MAX_PAGE_BYTES``, text pages only. ``research`` searches and
reads the top results.

A result is ``{title, url, snippet, provider}``; a page is ``{url,
title, text, links, truncated}``.
"""

import asyncio
from abc import abstractmethod
from html.parser import HTMLParser
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
)
from zephyrex.lib.Dependencies import Dependencies
from zephyrex.lib.ProviderHTTPClient import ClientPolicy, ProviderHTTPClient
from zephyrex.logic.BLL_Providers import ProviderInstanceModel

SEARCH_REQUEST_TIMEOUT_SECONDS = 15.0
PAGE_REQUEST_TIMEOUT_SECONDS = 20.0
DEFAULT_RESULTS = 10
MAX_RESULTS = 20
MAX_PAGE_BYTES = 2 * 1024 * 1024
DEFAULT_PAGE_CHARS = 20_000
MAX_PAGE_CHARS = 200_000
MAX_LINKS = 100
MAX_RESEARCH_PAGES = 5
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
# Elements whose text is not the page's content.
_SKIPPED = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}


class PageText(HTMLParser):
    """An HTML page's title, readable text and outgoing links."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.links: List[Dict[str, str]] = []
        self._chunks: List[str] = []
        self._skipping = 0
        self._in_title = False
        self._anchor: Optional[str] = None
        self._anchor_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in _SKIPPED:
            self._skipping += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCKS:
            self._chunks.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self._anchor = urljoin(self.base_url, href) if href else None
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED and self._skipping:
            self._skipping -= 1
        if tag in _BLOCKS:
            self._chunks.append("\n")
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._anchor:
            if urlparse(self._anchor).scheme in ("http", "https"):
                self.links.append(
                    {
                        "text": " ".join("".join(self._anchor_text).split()),
                        "url": self._anchor,
                    }
                )
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skipping:
            return
        self._chunks.append(data)
        if self._anchor is not None:
            self._anchor_text.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._chunks).splitlines())
        return "\n".join(line for line in lines if line)


def plain(html: str) -> str:
    """A fragment's text, tags dropped (search snippets carry <strong>)."""
    parser = PageText("")
    parser.feed(html)
    return parser.text().replace("\n", " ")


def result_limit(limit: int) -> int:
    if not 1 <= limit <= MAX_RESULTS:
        raise InvalidInputExternalError(f"limit must be 1-{MAX_RESULTS}")
    return limit


def read_page(
    url: str, content_type: str, body: bytes, max_chars: int, truncated: bool
) -> Dict[str, Any]:
    """A fetched page's title, text (at most ``max_chars``) and links."""
    media_type = content_type.split(";")[0].strip().lower()
    if media_type not in TEXT_TYPES:
        raise InvalidInputExternalError(
            f"{url} is {media_type or 'of no stated type'}, not a text page"
        )
    charset = "utf-8"
    for parameter in content_type.split(";")[1:]:
        name, _, value = parameter.strip().partition("=")
        if name.lower() == "charset" and value:
            charset = value.strip('"')
    try:
        decoded = body.decode(charset, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    title, links = "", []
    if media_type in ("text/html", "application/xhtml+xml"):
        parser = PageText(url)
        parser.feed(decoded)
        title, text, links = " ".join(parser.title.split()), parser.text(), parser.links
    else:
        text = decoded
    if len(text) > max_chars:
        text, truncated = text[:max_chars], True
    unique: Dict[str, Dict[str, str]] = {}
    for link in links:
        unique.setdefault(link["url"], link)
    return {
        "url": url,
        "title": title,
        "text": text,
        "links": list(unique.values())[:MAX_LINKS],
        "truncated": truncated,
    }


class AbstractWebsearchProvider(AbstractStaticProvider):
    """A web search engine."""

    name: ClassVar[str] = ""
    friendly_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    _abilities: ClassVar[Set[str]] = {"web_search", "research"}
    _env: ClassVar[Dict[str, Any]] = {}
    http_timeout_seconds: ClassVar[float] = SEARCH_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        return AbstractProviderInstance(instance)

    @classmethod
    def result(cls, title: str, url: str, snippet: str) -> Dict[str, Any]:
        return {"title": title, "url": url, "snippet": snippet, "provider": cls.name}

    @classmethod
    @abstractmethod
    async def search(
        cls, instance: ProviderInstanceModel, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Results for ``query``, best first."""

    @classmethod
    def services(cls) -> List[str]:
        return ["websearch"]


class EXT_Websearch(AbstractStaticExtension):
    name: ClassVar[str] = "websearch"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Web search through Brave Search or SearXNG, and reading web pages"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    dependencies: ClassVar[Dependencies] = Dependencies([])
    _abilities: ClassVar[Set[str]] = {"web_search", "fetch_page", "research"}

    @classmethod
    @ability("web_search")
    async def web_search(
        cls, query: str, limit: int = DEFAULT_RESULTS
    ) -> List[Dict[str, Any]]:
        """Search the web."""
        if not query.strip():
            raise InvalidInputExternalError("a search needs a query")
        result: List[Dict[str, Any]] = await cls.rotate_provider(
            "search", query, result_limit(limit)
        )
        return result

    @classmethod
    @ability("fetch_page")
    async def fetch_page(
        cls, url: str, max_chars: int = DEFAULT_PAGE_CHARS
    ) -> Dict[str, Any]:
        """Read a web page's title, text and links."""
        if not 1 <= max_chars <= MAX_PAGE_CHARS:
            raise InvalidInputExternalError(f"max_chars must be 1-{MAX_PAGE_CHARS}")
        client = ProviderHTTPClient(
            policy=ClientPolicy(timeout=PAGE_REQUEST_TIMEOUT_SECONDS),
            provider_name=cls.name,
        )
        fetched = await client.fetch(
            url,
            max_bytes=MAX_PAGE_BYTES,
            headers={"Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
        )
        return read_page(
            fetched.url,
            fetched.content_type,
            fetched.body,
            max_chars,
            fetched.truncated,
        )

    @classmethod
    @ability("research")
    async def research(cls, query: str, pages: int = 3) -> Dict[str, Any]:
        """Search, then read the top ``pages`` results (a page that cannot
        be read says why)."""
        if not 1 <= pages <= MAX_RESEARCH_PAGES:
            raise InvalidInputExternalError(f"pages must be 1-{MAX_RESEARCH_PAGES}")
        results = await cls.web_search(query, pages)

        async def read(found: Dict[str, Any]) -> Dict[str, Any]:
            try:
                page = await cls.fetch_page(found["url"])
            except BaseExternalError as exc:
                return {**found, "error": str(exc)}
            return {
                **found,
                "title": page["title"] or found["title"],
                "text": page["text"],
            }

        return {"query": query, "results": await asyncio.gather(*map(read, results))}
