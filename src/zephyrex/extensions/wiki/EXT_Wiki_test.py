# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wiki extension: its providers' configuration and safety, and real
calls to Wikipedia, Fandom and Kanka (Kanka only with sandbox credentials;
the public wikis xfail when the network is unreachable)."""

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.wiki.EXT_Wiki import (
    NO_SUMMARY,
    EXT_Wiki,
    first_paragraph,
    host_label,
    plain_text,
)
from zephyrex.extensions.wiki.PRV_Fandom import PRV_Fandom_Wiki
from zephyrex.extensions.wiki.PRV_Kanka import PRV_Kanka_Wiki
from zephyrex.extensions.wiki.PRV_Wikipedia import PRV_Wikipedia_Wiki


def _online() -> bool:
    try:
        httpx.head("https://en.wikipedia.org", timeout=5)
        return True
    except httpx.HTTPError:
        return False


online = pytest.mark.xfail(not _online(), reason="Wikipedia is unreachable")


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Wiki.providers} == {"wikipedia", "fandom", "kanka"}

    def test_abilities(self):
        assert {"search_wiki", "get_wiki_article", "get_wiki_summary"} <= set(
            EXT_Wiki.get_abilities()
        )

    async def test_no_configured_provider_is_503(self, monkeypatch):
        """No app attached: no root rotation to run an ability on."""
        from fastapi import HTTPException

        from zephyrex.pydantic2.registry import ModelRegistry

        monkeypatch.setattr(ModelRegistry, "attached", classmethod(lambda cls: None))
        with pytest.raises(HTTPException) as raised:
            await EXT_Wiki.search_wiki("anything")
        assert raised.value.status_code == 503


class TestHelpers:
    @pytest.mark.parametrize("label", ["en", "zh-yue", "simple", "harrypotter"])
    def test_host_labels(self, label):
        assert host_label(label, "language") == label

    @pytest.mark.parametrize(
        "value", ["evil.example/#", "en.evil.example", "", "a b", "../x", "-en"]
    )
    def test_a_value_that_would_change_the_host_is_refused(self, value):
        """language and wiki_domain become part of the hostname."""
        with pytest.raises(InvalidInputExternalError):
            host_label(value, "language")

    def test_plain_text_and_first_paragraph(self):
        assert plain_text("<p>Ada <b>Lovelace</b></p>") == "Ada Lovelace"
        assert first_paragraph("\n\nFirst.\n\nSecond.") == "First."
        assert first_paragraph("   ") == NO_SUMMARY


class TestConfiguration:
    def test_wikipedia_language_from_the_instance(self, provider_instance):
        instance = provider_instance(PRV_Wikipedia_Wiki, settings={"language": "de"})
        assert PRV_Wikipedia_Wiki.site(instance) == "https://de.wikipedia.org"

    def test_wikipedia_language_defaults_to_english(self, provider_instance):
        instance = provider_instance(PRV_Wikipedia_Wiki)
        assert PRV_Wikipedia_Wiki.site(instance) == "https://en.wikipedia.org"

    def test_a_hostile_wiki_domain_is_refused(self, provider_instance):
        instance = provider_instance(
            PRV_Fandom_Wiki, settings={"wiki_domain": "evil.example/#"}
        )
        with pytest.raises(InvalidInputExternalError):
            PRV_Fandom_Wiki.site(instance)

    async def test_kanka_without_a_campaign_fails_over(
        self, provider_instance, set_env
    ):
        set_env("KANKA_CAMPAIGN_ID", "")
        instance = provider_instance(PRV_Kanka_Wiki, api_key="token")
        with pytest.raises(TransientExternalError, match="campaign_id"):
            await PRV_Kanka_Wiki.search(instance, "dragon")

    async def test_kanka_without_a_token_fails_over(self, provider_instance, set_env):
        set_env("KANKA_API_TOKEN", "")
        instance = provider_instance(PRV_Kanka_Wiki, settings={"campaign_id": "1"})
        with pytest.raises(TransientExternalError, match="token"):
            await PRV_Kanka_Wiki.search(instance, "dragon")

    async def test_kanka_refuses_an_empty_search(self, provider_instance):
        instance = provider_instance(
            PRV_Kanka_Wiki, api_key="token", settings={"campaign_id": "1"}
        )
        with pytest.raises(InvalidInputExternalError):
            await PRV_Kanka_Wiki.search(instance, "  ")


@online
class TestWikipediaLive:
    async def test_search(self, provider_instance):
        results = await PRV_Wikipedia_Wiki.search(
            provider_instance(PRV_Wikipedia_Wiki), "Ada Lovelace", limit=3
        )
        assert 0 < len(results) <= 3
        assert any("Lovelace" in r["title"] for r in results)
        assert all(
            r["url"].startswith("https://en.wikipedia.org/wiki/") for r in results
        )

    async def test_article_and_summary(self, provider_instance):
        instance = provider_instance(PRV_Wikipedia_Wiki)
        article = await PRV_Wikipedia_Wiki.get_article(instance, "Ada Lovelace")
        assert article["title"] == "Ada Lovelace"
        assert "mathematician" in article["content"]
        assert article["pageid"]
        summary = await PRV_Wikipedia_Wiki.get_summary(instance, "Ada Lovelace")
        assert "Lovelace" in summary and len(summary) < len(article["content"])

    async def test_a_missing_article_is_empty(self, provider_instance):
        article = await PRV_Wikipedia_Wiki.get_article(
            provider_instance(PRV_Wikipedia_Wiki), "Zx no such article qq9"
        )
        assert article["content"] == "" and article["pageid"] == ""


@online
class TestFandomLive:
    async def test_search_and_article(self, provider_instance):
        instance = provider_instance(
            PRV_Fandom_Wiki, settings={"wiki_domain": "harrypotter"}
        )
        results = await PRV_Fandom_Wiki.search(instance, "Hermione", limit=3)
        assert results and "Hermione" in results[0]["title"]
        article = await PRV_Fandom_Wiki.get_article(instance, results[0]["title"])
        assert article["content"] and "<" not in article["content"][:200]


@pytest.mark.external_api(provider="kanka")
class TestKankaLive:
    async def test_search(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("kanka")
        instance = provider_instance(
            PRV_Kanka_Wiki,
            api_key=creds["KANKA_API_TOKEN"],
            settings={"campaign_id": creds["KANKA_CAMPAIGN_ID"]},
        )
        results = await PRV_Kanka_Wiki.search(instance, "a", limit=2)
        assert isinstance(results, list)
