# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SMS extension: number validation, bulk sending's per-number results,
each provider's configuration, and real calls to Twilio and Amazon SNS
(refused credentials need only the network; a Twilio send uses Twilio's test
credentials and magic numbers when they are configured)."""

import httpx
import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.sms.EXT_SMS import EXT_SMS, e164
from zephyrex.extensions.sms.PRV_Amazon import PRV_Amazon_SMS
from zephyrex.extensions.sms.PRV_Twilio import PRV_Twilio_SMS

# Twilio's test-credential magic numbers: a valid sender, a valid recipient.
TWILIO_TEST_FROM = "+15005550006"
TWILIO_TEST_TO = "+14108675310"


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


class TestNumbers:
    @pytest.mark.parametrize(
        "given, number",
        [
            ("+15551234567", "+15551234567"),
            ("+1 (555) 123-4567", "+15551234567"),
            ("+44 20 7946 0958", "+442079460958"),
        ],
    )
    def test_e164(self, given, number):
        assert e164(given) == number

    @pytest.mark.parametrize("given", ["5551234567", "+0123", "+1555abc4567", "", "+"])
    def test_a_number_that_is_not_e164_is_refused(self, given):
        with pytest.raises(InvalidInputExternalError):
            e164(given)


class TestExtension:
    def test_providers(self):
        assert {p.name for p in EXT_SMS.providers} == {"twilio", "amazon_sns"}

    async def test_bulk_reports_each_number_and_carries_on(self):
        """A refused number is reported in its own result; the others are
        still attempted (here refused too, before any gateway is asked)."""
        results = await EXT_SMS.send_bulk_sms(["555", "not a number"], "hi")
        assert [r["phone_number"] for r in results] == ["555", "not a number"]
        assert all(r["success"] is False and "E.164" in r["error"] for r in results)


class TestConfiguration:
    async def test_twilio_without_credentials_fails_over(
        self, provider_instance, set_env
    ):
        set_env("TWILIO_ACCOUNT_SID", "")
        set_env("TWILIO_AUTH_TOKEN", "")
        set_env("TWILIO_FROM_NUMBER", TWILIO_TEST_FROM)
        with pytest.raises(TransientExternalError, match="account_sid"):
            await PRV_Twilio_SMS.send_sms(
                provider_instance(PRV_Twilio_SMS), TWILIO_TEST_TO, "hi"
            )

    async def test_twilio_without_a_sender_fails_over(self, provider_instance, set_env):
        set_env("TWILIO_FROM_NUMBER", "")
        with pytest.raises(TransientExternalError, match="from_number"):
            await PRV_Twilio_SMS.send_sms(
                provider_instance(PRV_Twilio_SMS, api_key="token"), TWILIO_TEST_TO, "hi"
            )

    async def test_sns_without_credentials_fails_over(self, provider_instance, set_env):
        set_env("AWS_ACCESS_KEY_ID", "")
        set_env("AWS_SECRET_ACCESS_KEY", "")
        with pytest.raises(TransientExternalError, match="AWS credentials"):
            await PRV_Amazon_SMS.send_sms(
                provider_instance(PRV_Amazon_SMS), TWILIO_TEST_TO, "hi"
            )

    async def test_sns_refuses_a_bad_sender_id(self, provider_instance):
        instance = provider_instance(
            PRV_Amazon_SMS,
            api_key="AKIA",
            settings={"aws_secret_key": "x", "sender_id": "Not Valid!"},
        )
        with pytest.raises(InvalidInputExternalError, match="sender_id"):
            await PRV_Amazon_SMS.send_sms(instance, TWILIO_TEST_TO, "hi")

    async def test_sns_has_no_message_status(self, provider_instance):
        with pytest.raises(PermanentExternalError, match="delivery-status"):
            await PRV_Amazon_SMS.get_sms_status(provider_instance(PRV_Amazon_SMS), "id")


@pytest.mark.xfail(not _online("https://api.twilio.com"), reason="Twilio unreachable")
async def test_twilio_refused_credentials_are_an_auth_error(provider_instance):
    instance = provider_instance(
        PRV_Twilio_SMS,
        api_key="0" * 32,
        settings={"account_sid": "AC" + "0" * 32, "from_number": TWILIO_TEST_FROM},
    )
    with pytest.raises(AuthExternalError):
        await PRV_Twilio_SMS.send_sms(instance, TWILIO_TEST_TO, "hi")


@pytest.mark.xfail(
    not _online("https://sns.us-east-1.amazonaws.com"), reason="AWS unreachable"
)
async def test_sns_refused_credentials_are_an_auth_error(provider_instance):
    instance = provider_instance(
        PRV_Amazon_SMS,
        api_key="AKIAZEPHYREXTEST0000",
        settings={"aws_secret_key": "0" * 40},
    )
    with pytest.raises(AuthExternalError):
        await PRV_Amazon_SMS.send_sms(instance, TWILIO_TEST_TO, "hi")


@pytest.mark.external_api(provider="twilio_test")
async def test_twilio_sends_with_test_credentials(
    provider_instance, sandbox_credentials_for
):
    """Twilio's test credentials send nothing and charge nothing; the magic
    sender and recipient numbers answer as a real send would."""
    creds = sandbox_credentials_for("twilio_test")
    instance = provider_instance(
        PRV_Twilio_SMS,
        api_key=creds["TWILIO_TEST_AUTH_TOKEN"],
        settings={
            "account_sid": creds["TWILIO_TEST_ACCOUNT_SID"],
            "from_number": TWILIO_TEST_FROM,
        },
    )
    sent = await PRV_Twilio_SMS.send_sms(instance, TWILIO_TEST_TO, "zephyrex test")
    assert sent["message_id"].startswith("SM")
    assert sent["provider"] == "twilio"
