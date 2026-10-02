# SPDX-License-Identifier: AGPL-3.0-or-later
"""The messaging extension: routing by platform, each provider's validation,
configuration and refusals, real calls with refused credentials (which
need only the network), and live round trips when a platform's test
credentials and channel are set."""

import httpx
import pytest
from fastapi import HTTPException

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.messaging.EXT_Messaging import EXT_Messaging, history_limit
from zephyrex.extensions.messaging.PRV_Discord import PRV_Discord_Messaging
from zephyrex.extensions.messaging.PRV_Facebook import PRV_Facebook_Messaging
from zephyrex.extensions.messaging.PRV_Microsoft import PRV_Microsoft_Messaging
from zephyrex.extensions.messaging.PRV_Signal import PRV_Signal_Messaging
from zephyrex.extensions.messaging.PRV_Slack import PRV_Slack_Messaging
from zephyrex.extensions.messaging.PRV_TeamSpeak import PRV_TeamSpeak_Messaging
from zephyrex.extensions.messaging.PRV_Telegram import PRV_Telegram_Messaging
from zephyrex.extensions.messaging.PRV_WhatsApp import PRV_WhatsApp_Messaging

ALL = {
    "discord",
    "slack",
    "telegram",
    "msteams",
    "whatsapp",
    "messenger",
    "signal",
    "teamspeak",
}
FAKE_TELEGRAM_TOKEN = "123456789:" + "A" * 35


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_Messaging.providers} == ALL

    async def test_an_unknown_platform_is_400(self):
        with pytest.raises(HTTPException) as raised:
            await EXT_Messaging.send_message("pager", "x", "hi")
        assert raised.value.status_code == 400

    async def test_a_message_needs_text(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Messaging.send_message("slack", "C1", "   ")

    def test_history_is_bounded(self):
        assert history_limit(0) == 1 and history_limit(10_000) == 100


class TestRefusals:
    @pytest.mark.parametrize(
        "provider, operation",
        [
            (PRV_Microsoft_Messaging, "get_message_history"),
            (PRV_WhatsApp_Messaging, "get_message_history"),
            (PRV_Facebook_Messaging, "get_message_history"),
            (PRV_TeamSpeak_Messaging, "get_message_history"),
        ],
    )
    async def test_history_a_platform_does_not_offer(
        self, provider_instance, provider, operation
    ):
        with pytest.raises(PermanentExternalError, match="cannot read history"):
            await getattr(provider, operation)(provider_instance(provider), "x", 5)

    @pytest.mark.parametrize(
        "provider",
        [
            PRV_Microsoft_Messaging,
            PRV_WhatsApp_Messaging,
            PRV_Facebook_Messaging,
            PRV_TeamSpeak_Messaging,
        ],
    )
    async def test_deletion_a_platform_does_not_offer(
        self, provider_instance, provider
    ):
        with pytest.raises(PermanentExternalError, match="cannot delete"):
            await provider.delete_message(provider_instance(provider), "x", "1")


class TestConfiguration:
    @pytest.mark.parametrize(
        "provider, variables",
        [
            (PRV_Discord_Messaging, ["DISCORD_BOT_TOKEN"]),
            (PRV_Slack_Messaging, ["SLACK_BOT_TOKEN"]),
            (PRV_Telegram_Messaging, ["TELEGRAM_BOT_TOKEN"]),
            (PRV_Microsoft_Messaging, ["TEAMS_WEBHOOK_URL"]),
            (
                PRV_WhatsApp_Messaging,
                ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"],
            ),
            (PRV_Facebook_Messaging, ["MESSENGER_PAGE_TOKEN"]),
            (PRV_Signal_Messaging, ["SIGNAL_API_URL", "SIGNAL_NUMBER"]),
            (PRV_TeamSpeak_Messaging, ["TEAMSPEAK_API_URL", "TEAMSPEAK_API_KEY"]),
        ],
    )
    async def test_unconfigured_fails_over(
        self, provider_instance, set_env, provider, variables
    ):
        for variable in variables:
            set_env(variable, "")
        recipient = "channel:1" if provider is PRV_TeamSpeak_Messaging else "123"
        with pytest.raises(TransientExternalError, match="not configured"):
            await provider.send_message(provider_instance(provider), recipient, "hi")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.webhook.office.com/hook",
            "https://evil.example/webhook.office.com",
            "https://prod-00.westus.logic.azure.com.evil.example/x",
        ],
    )
    async def test_teams_refuses_a_webhook_that_is_not_teams(
        self, provider_instance, url
    ):
        instance = provider_instance(
            PRV_Microsoft_Messaging, settings={"webhook_url": url}
        )
        with pytest.raises(InvalidInputExternalError, match="webhook_url"):
            await PRV_Microsoft_Messaging.send_message(instance, "general", "hi")

    @pytest.mark.parametrize(
        "recipient", ["channel:", "client:x", "server:5", "group:1", "5"]
    )
    def test_teamspeak_targets(self, recipient):
        with pytest.raises(InvalidInputExternalError):
            PRV_TeamSpeak_Messaging._target(recipient)

    def test_teamspeak_target_modes(self):
        assert PRV_TeamSpeak_Messaging._target("channel:7") == (2, "7")
        assert PRV_TeamSpeak_Messaging._target("client:3") == (1, "3")
        assert PRV_TeamSpeak_Messaging._target("server") == (3, "0")

    async def test_telegram_refuses_a_malformed_token(self, provider_instance):
        instance = provider_instance(PRV_Telegram_Messaging, api_key="not a token/../x")
        with pytest.raises(InvalidInputExternalError, match="bot token"):
            await PRV_Telegram_Messaging.send_message(instance, "1", "hi")


class TestRefusedCredentialsLive:
    """Real calls with credentials each platform refuses: what each answer
    is typed as decides whether the rotation fails over."""

    @pytest.mark.xfail(not _online("https://discord.com"), reason="Discord unreachable")
    async def test_discord(self, provider_instance):
        instance = provider_instance(PRV_Discord_Messaging, api_key="not-a-real-token")
        with pytest.raises(AuthExternalError):
            await PRV_Discord_Messaging.send_message(instance, "1234567890", "hi")

    @pytest.mark.xfail(not _online("https://slack.com"), reason="Slack unreachable")
    async def test_slack_answers_200_but_it_is_an_auth_error(self, provider_instance):
        instance = provider_instance(PRV_Slack_Messaging, api_key="xoxb-not-real")
        with pytest.raises(AuthExternalError, match="invalid_auth"):
            await PRV_Slack_Messaging.send_message(instance, "C0000000", "hi")

    @pytest.mark.xfail(
        not _online("https://api.telegram.org"), reason="Telegram unreachable"
    )
    async def test_telegram_without_its_token_in_the_error(self, provider_instance):
        instance = provider_instance(
            PRV_Telegram_Messaging, api_key=FAKE_TELEGRAM_TOKEN
        )
        with pytest.raises(BaseExternalError) as raised:
            await PRV_Telegram_Messaging.send_message(instance, "1", "hi")
        assert isinstance(raised.value, AuthExternalError)
        assert "A" * 35 not in raised.value.message

    @pytest.mark.xfail(
        not _online("https://graph.facebook.com"), reason="Meta unreachable"
    )
    @pytest.mark.parametrize(
        "provider, settings",
        [
            (PRV_WhatsApp_Messaging, {"phone_number_id": "100000000000000"}),
            (PRV_Facebook_Messaging, {}),
        ],
    )
    async def test_a_refused_meta_token_is_an_auth_error(
        self, provider_instance, provider, settings
    ):
        """Meta answers 401, or 400 with OAuthException code 190: both are
        a refused token."""
        instance = provider_instance(provider, api_key="EAAnotreal", settings=settings)
        with pytest.raises(AuthExternalError):
            await provider.send_message(instance, "15551234567", "hi")


class TestRoundTripLive:
    """Send, read back and delete, on a channel set aside for tests."""

    @pytest.mark.external_api(provider="slack_test")
    async def test_slack(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("slack_test")
        instance = provider_instance(
            PRV_Slack_Messaging, api_key=creds["SLACK_BOT_TOKEN"]
        )
        channel = creds["SLACK_TEST_CHANNEL"]
        sent = await PRV_Slack_Messaging.send_message(
            instance, channel, "zephyrex test"
        )
        history = await PRV_Slack_Messaging.get_message_history(instance, channel, 5)
        assert any(m["id"] == sent["message_id"] for m in history)
        deleted = await PRV_Slack_Messaging.delete_message(
            instance, channel, sent["message_id"]
        )
        assert deleted["deleted"] is True

    @pytest.mark.external_api(provider="discord_test")
    async def test_discord(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("discord_test")
        instance = provider_instance(
            PRV_Discord_Messaging, api_key=creds["DISCORD_BOT_TOKEN"]
        )
        channel = creds["DISCORD_TEST_CHANNEL"]
        sent = await PRV_Discord_Messaging.send_message(
            instance, channel, "zephyrex test"
        )
        history = await PRV_Discord_Messaging.get_message_history(instance, channel, 5)
        assert any(m["id"] == sent["message_id"] for m in history)
        await PRV_Discord_Messaging.delete_message(
            instance, channel, sent["message_id"]
        )

    @pytest.mark.external_api(provider="telegram_test")
    async def test_telegram(self, provider_instance, sandbox_credentials_for):
        creds = sandbox_credentials_for("telegram_test")
        instance = provider_instance(
            PRV_Telegram_Messaging, api_key=creds["TELEGRAM_BOT_TOKEN"]
        )
        chat = creds["TELEGRAM_TEST_CHAT"]
        sent = await PRV_Telegram_Messaging.send_message(
            instance, chat, "zephyrex test"
        )
        await PRV_Telegram_Messaging.delete_message(instance, chat, sent["message_id"])
