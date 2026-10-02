# SPDX-License-Identifier: AGPL-3.0-or-later
"""The social extension: media and limit checks, what each platform
refuses before any call, the publication record (written by the ability,
read-only over the API), real calls refusing bogus tokens on every
platform, and read-only live checks with test accounts."""

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
)
from zephyrex.extensions.social.BLL_Social import (
    SocialPublicationManager,
    SocialPublicationModel,
)
from zephyrex.extensions.social.EXT_Social import (
    MAX_MEDIA,
    EXT_Social,
    checked_media,
    count,
    feed_limit,
    media_kind,
)
from zephyrex.extensions.social.PRV_Facebook import PRV_Facebook_Social
from zephyrex.extensions.social.PRV_Instagram import PRV_Instagram_Social
from zephyrex.extensions.social.PRV_Postiz import PRV_Postiz_Social, postiz_post_id
from zephyrex.extensions.social.PRV_Threads import PRV_Threads_Social
from zephyrex.extensions.social.PRV_TikTok import PRV_TikTok_Social
from zephyrex.extensions.social.PRV_X import PRV_X_Social, problem
from zephyrex.lib.Environment import env

PHOTO = "https://cdn.example.com/a/photo.jpg"
VIDEO = "https://cdn.example.com/a/clip.mp4"
BOGUS = "not-a-real-token"


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


class TestChecks:
    @pytest.mark.parametrize(
        "url, kind",
        [(PHOTO, "image"), ("https://x.example/p.WEBP", "image"), (VIDEO, "video")],
    )
    def test_media_kinds(self, url, kind):
        assert media_kind(url) == kind

    @pytest.mark.parametrize(
        "url",
        [
            "http://cdn.example.com/photo.jpg",
            "file:///etc/passwd.jpg",
            "https:///photo.jpg",
            "https://cdn.example.com/document.pdf",
        ],
    )
    def test_media_must_be_public_https_images_or_videos(self, url):
        with pytest.raises(InvalidInputExternalError):
            media_kind(url)

    def test_media_count(self):
        assert checked_media(None) == []
        with pytest.raises(InvalidInputExternalError):
            checked_media([PHOTO] * (MAX_MEDIA + 1))

    def test_limits_and_counts(self):
        assert feed_limit(10) == 10
        with pytest.raises(InvalidInputExternalError):
            feed_limit(0)
        assert count(12) == 12 and count(None) is None and count(True) is None

    def test_x_problem_text(self):
        assert problem('{"title": "Forbidden", "detail": "dup"}') == "Forbidden: dup"
        assert problem("not json") == ""

    def test_postiz_post_ids(self):
        assert postiz_post_id([{"postId": "p1", "integration": "i"}]) == "p1"
        assert postiz_post_id({"id": "p2"}) == "p2"
        assert postiz_post_id([]) == ""

    async def test_a_post_needs_text_or_media(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Social.publish_post("x", "   ")


class TestPlatformRefusals:
    """What a platform cannot take is refused before any call."""

    async def test_x_takes_text_only(self, provider_instance):
        with pytest.raises(InvalidInputExternalError):
            await PRV_X_Social.publish(
                provider_instance(PRV_X_Social, api_key=BOGUS), "hi", [PHOTO]
            )

    async def test_instagram_needs_media(self, provider_instance):
        with pytest.raises(InvalidInputExternalError):
            await PRV_Instagram_Social.publish(
                provider_instance(PRV_Instagram_Social, api_key=BOGUS), "hi", []
            )

    async def test_facebook_takes_one_medium(self, provider_instance):
        with pytest.raises(InvalidInputExternalError):
            await PRV_Facebook_Social.publish(
                provider_instance(PRV_Facebook_Social, api_key=BOGUS),
                "hi",
                [PHOTO, PHOTO],
            )

    @pytest.mark.parametrize("media", [[], [PHOTO, VIDEO], [VIDEO, VIDEO]])
    async def test_tiktok_takes_one_video_or_photos(self, provider_instance, media):
        with pytest.raises(InvalidInputExternalError):
            await PRV_TikTok_Social.publish(
                provider_instance(PRV_TikTok_Social, api_key=BOGUS), "hi", media
            )


class TestPublicationRecord:
    def test_the_ability_records_an_accepted_post(
        self, provider_instance, rotation_over, monkeypatch, extension_app
    ):
        instance = provider_instance(PRV_Threads_Social)
        monkeypatch.setattr(EXT_Social, "_root_rotation_cache", rotation_over(instance))
        publication_id = EXT_Social._record(
            "Hello",
            [PHOTO],
            {
                "platform_post_id": "1789",
                "url": "https://www.threads.net/@ada/post/1789",
                "provider": "threads",
                "provider_instance_id": str(instance.id),
            },
        )
        found = SocialPublicationModel.model_validate(
            SocialPublicationManager(
                model_registry=extension_app.state.model_registry,
                requester_id=env("ROOT_ID"),
            ).get(id=publication_id),
            from_attributes=True,
        )
        assert found.platform_post_id == "1789" and found.media_urls == [PHOTO]
        assert found.provider_instance_id == str(instance.id)

    def test_the_record_is_read_only_over_the_api(self, extension_app):
        """Publications come from the ability; a client cannot forge one."""
        paths = extension_app.openapi()["paths"]
        assert {
            (method, path)
            for path, operations in paths.items()
            if "social_publication" in path
            for method in operations
        } == {
            ("get", "/v1/social_publication"),
            ("get", "/v1/social_publication/{id}"),
            ("post", "/v1/social_publication/search"),
        }


class TestRefusedTokens:
    @reachable("https://api.x.com")
    async def test_x(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_X_Social.profile(provider_instance(PRV_X_Social, api_key=BOGUS))

    @reachable("https://graph.facebook.com")
    async def test_facebook(self, provider_instance):
        instance = provider_instance(
            PRV_Facebook_Social, api_key=BOGUS, settings={"page_id": "1234"}
        )
        with pytest.raises(AuthExternalError):
            await PRV_Facebook_Social.profile(instance)

    @reachable("https://graph.facebook.com")
    async def test_instagram(self, provider_instance):
        instance = provider_instance(
            PRV_Instagram_Social, api_key=BOGUS, settings={"ig_user_id": "1784"}
        )
        with pytest.raises(AuthExternalError):
            await PRV_Instagram_Social.profile(instance)

    @reachable("https://graph.threads.net")
    async def test_threads(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Threads_Social.profile(
                provider_instance(PRV_Threads_Social, api_key=BOGUS)
            )

    @reachable("https://open.tiktokapis.com")
    async def test_tiktok(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_TikTok_Social.profile(
                provider_instance(PRV_TikTok_Social, api_key=BOGUS)
            )

    @reachable("https://api.postiz.com")
    async def test_postiz(self, provider_instance):
        instance = provider_instance(
            PRV_Postiz_Social, api_key=BOGUS, settings={"integration_id": "abc"}
        )
        with pytest.raises(AuthExternalError):
            await PRV_Postiz_Social.profile(instance)


class TestLive:
    """Read-only checks against test accounts (profile, recent posts)."""

    @pytest.mark.external_api(provider="x_test")
    async def test_x(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("x_test")["X_ACCESS_TOKEN"]
        instance = provider_instance(PRV_X_Social, api_key=token)
        assert (await PRV_X_Social.profile(instance))["username"]

    @pytest.mark.external_api(provider="threads_test")
    async def test_threads(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("threads_test")["THREADS_ACCESS_TOKEN"]
        instance = provider_instance(PRV_Threads_Social, api_key=token)
        assert (await PRV_Threads_Social.profile(instance))["username"]
        assert isinstance(await PRV_Threads_Social.feed(instance, 5), list)

    @pytest.mark.external_api(provider="facebook_test")
    async def test_facebook(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("facebook_test")
        instance = provider_instance(
            PRV_Facebook_Social,
            api_key=creds["FACEBOOK_PAGE_TOKEN"],
            settings={"page_id": creds["FACEBOOK_PAGE_ID"]},
        )
        assert (await PRV_Facebook_Social.profile(instance))["name"]

    @pytest.mark.external_api(provider="instagram_test")
    async def test_instagram(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("instagram_test")
        instance = provider_instance(
            PRV_Instagram_Social,
            api_key=creds["INSTAGRAM_ACCESS_TOKEN"],
            settings={"ig_user_id": creds["INSTAGRAM_USER_ID"]},
        )
        assert (await PRV_Instagram_Social.profile(instance))["username"]

    @pytest.mark.external_api(provider="tiktok_test")
    async def test_tiktok(self, provider_instance, sandbox_credentials_for):
        token = sandbox_credentials_for("tiktok_test")["TIKTOK_ACCESS_TOKEN"]
        instance = provider_instance(PRV_TikTok_Social, api_key=token)
        assert (await PRV_TikTok_Social.profile(instance))["name"]

    @pytest.mark.external_api(provider="postiz_test")
    async def test_postiz(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("postiz_test")
        instance = provider_instance(
            PRV_Postiz_Social,
            api_key=creds["POSTIZ_API_KEY"],
            settings={"integration_id": creds["POSTIZ_INTEGRATION_ID"]},
        )
        assert (await PRV_Postiz_Social.profile(instance))["platform"]
