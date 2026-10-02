# SPDX-License-Identifier: AGPL-3.0-or-later
"""The websearch extension: page reading (text, title, links, binary and
oversize pages), the SSRF guard on fetch_page (the old code fetched any
address with plain requests), SearXNG's answer, Brave refusing a bogus
token, real page reads, and a live search with test credentials."""

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.extensions.websearch.EXT_Websearch import (
    MAX_LINKS,
    EXT_Websearch,
    plain,
    read_page,
    result_limit,
)
from zephyrex.extensions.websearch.PRV_Brave import PRV_Brave_Websearch
from zephyrex.extensions.websearch.PRV_SearXNG import PRV_SearXNG_Websearch

PAGE = b"""<!doctype html><html><head><title> The  Page </title>
<style>body { color: red }</style><script>var secret = 1;</script></head>
<body><h1>Heading</h1><p>First   paragraph with <a href="/next">a link</a>.</p>
<p>Second &amp; last.</p><a href="mailto:x@y.z">mail</a>
<a href="https://other.example/page">elsewhere</a></body></html>"""


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestReading:
    def test_an_html_page(self):
        page = read_page(
            "https://site.example/a/", "text/html; charset=utf-8", PAGE, 10_000, False
        )
        assert page["title"] == "The Page"
        assert page["text"] == (
            "Heading\nFirst paragraph with a link.\nSecond & last.\nmail\nelsewhere"
        )
        assert "secret" not in page["text"] and "color" not in page["text"]
        assert page["links"] == [
            {"text": "a link", "url": "https://site.example/next"},
            {"text": "elsewhere", "url": "https://other.example/page"},
        ]

    def test_text_is_cut_to_the_limit(self):
        page = read_page("https://s.example", "text/plain", b"abcdef", 3, False)
        assert page["text"] == "abc" and page["truncated"]

    def test_a_declared_charset_is_honoured(self):
        page = read_page(
            "https://s.example",
            "text/plain; charset=latin-1",
            "café".encode("latin-1"),
            99,
            False,
        )
        assert page["text"] == "café"

    @pytest.mark.parametrize("content_type", ["image/png", "application/pdf", ""])
    def test_a_page_that_is_not_text_is_refused(self, content_type):
        with pytest.raises(InvalidInputExternalError):
            read_page("https://s.example/x", content_type, b"\x89PNG", 99, False)

    def test_links_are_bounded_and_unique(self):
        anchors = "".join(f'<a href="/p{i % 150}">x</a>' for i in range(400))
        page = read_page(
            "https://s.example/", "text/html", anchors.encode(), 99_999, False
        )
        assert len(page["links"]) == MAX_LINKS
        assert len({link["url"] for link in page["links"]}) == MAX_LINKS

    def test_snippets_lose_their_markup(self):
        assert plain("the <strong>best</strong> result") == "the best result"

    def test_limits(self):
        assert result_limit(5) == 5
        with pytest.raises(InvalidInputExternalError):
            result_limit(21)


class TestFetchPage:
    async def test_a_page_through_the_guard(self, local_http_server):
        server = local_http_server(
            {
                "/start": (301, {"Location": "/page"}, b""),
                "/page": (200, {"Content-Type": "text/html"}, PAGE),
            }
        )
        page = await EXT_Websearch.fetch_page(f"{server.base_url}/start")
        assert page["url"] == f"{server.base_url}/page"
        assert page["title"] == "The Page"

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "file:///etc/passwd",
        ],
    )
    async def test_a_private_or_non_web_address_is_refused(self, url):
        """The old fetch used plain requests: any address an agent named."""
        with pytest.raises(InvalidInputExternalError):
            await EXT_Websearch.fetch_page(url)

    async def test_arguments_are_checked(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Websearch.fetch_page("https://example.com", max_chars=0)
        with pytest.raises(InvalidInputExternalError):
            await EXT_Websearch.web_search("   ")
        with pytest.raises(InvalidInputExternalError):
            await EXT_Websearch.research("anything", pages=9)

    @pytest.mark.xfail(
        not _online("https://example.com"), reason="example.com is unreachable"
    )
    async def test_a_real_page(self):
        page = await EXT_Websearch.fetch_page("https://example.com/")
        assert "Example Domain" in page["title"]


class TestProviders:
    def test_searxng_results(self):
        answer = {
            "results": [
                {
                    "title": "A <b>title</b>",
                    "url": "https://a.example",
                    "content": "Text",
                },
                {"title": "No address"},
                {"title": "B", "url": "https://b.example", "content": ""},
            ]
        }
        assert PRV_SearXNG_Websearch.results(answer, 5) == [
            {
                "title": "A title",
                "url": "https://a.example",
                "snippet": "Text",
                "provider": "searxng",
            },
            {
                "title": "B",
                "url": "https://b.example",
                "snippet": "",
                "provider": "searxng",
            },
        ]

    @pytest.mark.xfail(
        not _online("https://api.search.brave.com"), reason="Brave is unreachable"
    )
    async def test_brave_refuses_a_bogus_token(self, provider_instance):
        instance = provider_instance(PRV_Brave_Websearch, api_key="not-a-real-key")
        with pytest.raises(AuthExternalError):
            await PRV_Brave_Websearch.search(instance, "zephyrex", 3)

    @pytest.mark.external_api(provider="brave_test")
    async def test_brave_live(self, provider_instance, sandbox_credentials_for):
        key = sandbox_credentials_for("brave_test")["BRAVE_SEARCH_API_KEY"]
        found = await PRV_Brave_Websearch.search(
            provider_instance(PRV_Brave_Websearch, api_key=key), "python programming", 3
        )
        assert found and all(result["url"].startswith("http") for result in found)

    @pytest.mark.external_api(provider="searxng_test")
    async def test_searxng_live(self, provider_instance, sandbox_credentials_for):
        url = sandbox_credentials_for("searxng_test")["SEARXNG_URL"]
        found = await PRV_SearXNG_Websearch.search(
            provider_instance(PRV_SearXNG_Websearch, settings={"base_url": url}),
            "python programming",
            3,
        )
        assert found
