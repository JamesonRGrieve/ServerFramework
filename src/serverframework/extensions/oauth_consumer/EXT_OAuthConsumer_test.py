"""Tests for the oauth_consumer extension."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("ALLOW_PLAINTEXT_SECRETS", "true")
os.environ.setdefault("PYTEST_CURRENT_TEST", "oauth_consumer_test")

import pytest
from fastapi import HTTPException

from serverframework.extensions.oauth_consumer.BLL_OAuthConsumer import (
    OAuthConsumerManager,
    OAuthCallbackRequest,
    UserOAuthLinkModel,
)
from serverframework.extensions.oauth_consumer.EXT_OAuthConsumer import (
    EXT_OAuthConsumer,
)
from serverframework.extensions.oauth_consumer.IdPRegistry import (
    get_idp,
    list_idps,
)
from serverframework.extensions.oauth_consumer.PRV_AbstractIdP import (
    AbstractIdPProvider,
)
from serverframework.lib.ReplayCache import (
    InMemoryReplayCache,
    set_replay_cache,
)
from serverframework.logic.BLL_Auth import (
    InvalidGrantError,
    PasswordlessGrantRegistry,
)


class TestIdPRegistry:
    def test_four_idps_self_registered(self):
        # Force-import each so the register_idp(...) calls execute.
        from serverframework.extensions.oauth_consumer import (  # noqa: F401
            Amazon,
            GitHub,
            Google,
            Microsoft,
        )

        names = set(list_idps())
        for expected in ("amazon", "github", "google", "microsoft"):
            assert expected in names

    def test_each_idp_has_authorize_url(self):
        from serverframework.extensions.oauth_consumer import (  # noqa: F401
            Amazon,
            GitHub,
            Google,
            Microsoft,
        )

        for name in ("amazon", "github", "google", "microsoft"):
            cls = get_idp(name)
            instance = cls()
            assert getattr(instance, "AUTHORIZE_URL", None), (
                f"{name} IdP missing AUTHORIZE_URL"
            )

    def test_unknown_idp_raises(self):
        with pytest.raises(ValueError):
            get_idp("does-not-exist")


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_OAuthConsumer.name == "oauth_consumer"

    def test_grant_registered(self):
        # BLL import side-effect registers oauth_consumer with the
        # PasswordlessGrantRegistry.
        from serverframework.extensions.oauth_consumer import (  # noqa: F401
            BLL_OAuthConsumer,
        )

        assert "oauth_consumer" in PasswordlessGrantRegistry.list_grant_types()


class TestStateCSRF:
    def setup_method(self):
        # Fresh in-memory cache per test so state isolation is guaranteed.
        set_replay_cache(InMemoryReplayCache())

    def _manager(self):
        m = OAuthConsumerManager.__new__(OAuthConsumerManager)
        m.model_registry = None
        return m

    def test_callback_without_state_rejected(self):
        manager = self._manager()
        with pytest.raises(InvalidGrantError):
            import asyncio

            asyncio.run(
                manager.complete_callback(
                    provider="google", code="x", redirect_uri="https://x", state=""
                )
            )

    def test_callback_with_unknown_state_rejected(self):
        manager = self._manager()
        with pytest.raises(InvalidGrantError):
            import asyncio

            asyncio.run(
                manager.complete_callback(
                    provider="google",
                    code="x",
                    redirect_uri="https://x",
                    state="never-issued",
                )
            )


class TestRequestSchema:
    def test_state_is_required(self):
        # Pydantic should reject a callback request that omits state now
        # that the field is no longer Optional.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            OAuthCallbackRequest(provider="google", code="abc")
