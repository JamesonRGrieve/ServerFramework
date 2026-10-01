# SPDX-License-Identifier: AGPL-3.0-or-later
"""The media extension: media ids and their routing to the provider that
owns them, each provider's validation and configuration, and real calls to
YouTube and TMDb with their API keys."""

import pytest
from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.media.EXT_Media import EXT_Media, split_media_id
from zephyrex.extensions.media.PRV_TMDb import PRV_TMDb_Media
from zephyrex.extensions.media.PRV_YouTube import PRV_YouTube_Media

# The Matrix (1999).
TMDB_MATRIX = "movie:603"
MATRIX_IMDB = "tt0133093"


class TestMediaIds:
    @pytest.mark.parametrize(
        "media, parts",
        [
            ("tmdb:movie:603", ("tmdb", "movie:603")),
            ("youtube:dQw4w9WgXcQ", ("youtube", "dQw4w9WgXcQ")),
        ],
    )
    def test_split(self, media, parts):
        assert split_media_id(media) == parts

    @pytest.mark.parametrize("media", ["603", ":603", "tmdb:", ""])
    def test_an_id_naming_no_provider_is_refused(self, media):
        with pytest.raises(InvalidInputExternalError):
            split_media_id(media)

    def test_ids_carry_their_provider(self):
        assert PRV_TMDb_Media.media_id("tv:1399") == "tmdb:tv:1399"


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Media.providers} == {"youtube", "tmdb"}

    async def test_an_unknown_kind_is_refused(self):
        with pytest.raises(InvalidInputExternalError, match="kind"):
            await EXT_Media.search_media("x", kind="podcast")

    async def test_an_id_for_a_provider_it_does_not_have(self):
        with pytest.raises(HTTPException) as raised:
            await EXT_Media.get_media_info("netflix:80057281")
        assert raised.value.status_code == 400

    async def test_a_lookup_runs_on_its_own_providers_instances(
        self, provider_instance, rotation_over, monkeypatch, set_env
    ):
        """YouTube leads the rotation, but a TMDb id is TMDb's to answer:
        the failure is TMDb's (no key), never YouTube's."""
        set_env("YOUTUBE_API_KEY", "")
        set_env("TMDB_API_KEY", "")
        rotation = rotation_over(
            provider_instance(PRV_YouTube_Media), provider_instance(PRV_TMDb_Media)
        )
        monkeypatch.setattr(EXT_Media, "_root_rotation_cache", rotation)
        with pytest.raises(Exception, match="TMDb API key"):
            await EXT_Media.get_media_info(f"tmdb:{TMDB_MATRIX}")


class TestProviders:
    @pytest.mark.parametrize("native", ["603", "film:603", "movie:abc", "movie:"])
    async def test_tmdb_refuses_a_malformed_id(self, provider_instance, native):
        instance = provider_instance(PRV_TMDb_Media, api_key="k")
        with pytest.raises(InvalidInputExternalError, match="TMDb id"):
            await PRV_TMDb_Media.get_info(instance, native)

    @pytest.mark.parametrize("native", ["short", "has space1234", "../../videos"])
    async def test_youtube_refuses_a_malformed_video_id(
        self, provider_instance, native
    ):
        instance = provider_instance(PRV_YouTube_Media, api_key="k")
        with pytest.raises(InvalidInputExternalError, match="video id"):
            await PRV_YouTube_Media.get_info(instance, native)

    def test_tmdb_sends_a_v3_key_as_a_parameter(self, provider_instance):
        params, headers = PRV_TMDb_Media._auth(
            provider_instance(PRV_TMDb_Media, api_key="0" * 32)
        )
        assert params == {"api_key": "0" * 32} and headers == {}

    def test_tmdb_sends_a_v4_token_as_a_bearer(self, provider_instance):
        token = "eyJhbGciOiJIUzI1NiJ9.e30.sig"
        params, headers = PRV_TMDb_Media._auth(
            provider_instance(PRV_TMDb_Media, api_key=token)
        )
        assert params == {} and headers == {"Authorization": f"Bearer {token}"}

    async def test_without_a_key_each_fails_over(self, provider_instance, set_env):
        set_env("YOUTUBE_API_KEY", "")
        set_env("TMDB_API_KEY", "")
        for provider in (PRV_YouTube_Media, PRV_TMDb_Media):
            with pytest.raises(TransientExternalError, match="API key"):
                await provider.search(provider_instance(provider), "x")

    async def test_a_kind_the_catalogue_does_not_hold_is_no_results(
        self, provider_instance
    ):
        assert (
            await PRV_YouTube_Media.search(
                provider_instance(PRV_YouTube_Media, api_key="k"), "x", kind="movie"
            )
            == []
        )
        assert (
            await PRV_TMDb_Media.search(
                provider_instance(PRV_TMDb_Media, api_key="k"), "x", kind="video"
            )
            == []
        )


@pytest.mark.external_api(provider="tmdb")
class TestTMDbLive:
    @pytest.fixture
    def instance(self, provider_instance, sandbox_credentials_for):
        return provider_instance(
            PRV_TMDb_Media, api_key=sandbox_credentials_for("tmdb")["TMDB_API_KEY"]
        )

    async def test_search_info_and_recommendations(self, instance):
        found = await PRV_TMDb_Media.search(instance, "The Matrix", kind="movie")
        assert any(item["id"] == f"tmdb:{TMDB_MATRIX}" for item in found)
        info = await PRV_TMDb_Media.get_info(instance, TMDB_MATRIX)
        assert info["imdb_id"] == MATRIX_IMDB and info["year"] == 1999
        like = await PRV_TMDb_Media.get_recommendations(instance, TMDB_MATRIX, 3)
        assert 0 < len(like) <= 3
        assert all(item["id"].startswith("tmdb:movie:") for item in like)


@pytest.mark.external_api(provider="youtube")
class TestYouTubeLive:
    @pytest.fixture
    def instance(self, provider_instance, sandbox_credentials_for):
        return provider_instance(
            PRV_YouTube_Media,
            api_key=sandbox_credentials_for("youtube")["YOUTUBE_API_KEY"],
        )

    async def test_search_info_and_recommendations(self, instance):
        found = await PRV_YouTube_Media.search(
            instance, "Never Gonna Give You Up", limit=3
        )
        assert found and all(item["id"].startswith("youtube:") for item in found)
        video = found[0]["id"].split(":", 1)[1]
        info = await PRV_YouTube_Media.get_info(instance, video)
        assert info["channel_id"] and info["views"] >= 0
        like = await PRV_YouTube_Media.get_recommendations(instance, video, 2)
        assert all(item["id"] != found[0]["id"] for item in like)
