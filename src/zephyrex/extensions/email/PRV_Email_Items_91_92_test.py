"""Unit tests for Items 91 + 92 surface on email providers.

Item 91: each provider's ``send_via_provider`` carries the ``@idempotent``
marker; ``send_bulk_via_provider`` exists and accepts a list of
``EmailMessage``; per-batch caps reject oversized batches.

Item 92: each provider declares ``default_auth_strategy`` matching the
spec — ``"api_key"`` for SendGrid + SMTP2go, ``"basic"`` for Stalwart.

These tests are pure-utility unit tests that do NOT touch the network
or any real provider SDK; they verify static markers and reject paths
on the typed surface only.
"""

from __future__ import annotations

import pytest

from zephyrex.extensions.AbstractExternalModel import is_idempotent
from zephyrex.extensions.email.EmailErrors import EmailValidationError
from zephyrex.extensions.email.EXT_EMail import EmailAddress, EmailMessage
from zephyrex.extensions.email.PRV_SendGrid_EMail import (
    SendgridProvider,
    Smtp2goProvider,
    StalwartProvider,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Item 92 — default_auth_strategy declarations
# ---------------------------------------------------------------------------


class TestDefaultAuthStrategy:
    def test_sendgrid_uses_api_key(self):
        assert SendgridProvider.default_auth_strategy == "api_key"

    def test_smtp2go_uses_api_key(self):
        assert Smtp2goProvider.default_auth_strategy == "api_key"

    def test_stalwart_uses_basic(self):
        assert StalwartProvider.default_auth_strategy == "basic"


# ---------------------------------------------------------------------------
# Item 91 — @idempotent markers + bulk-cap enforcement
# ---------------------------------------------------------------------------


class TestIdempotentMarker:
    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    def test_send_via_provider_is_idempotent(self, provider):
        assert is_idempotent(provider.send_via_provider), (
            f"{provider.__name__}.send_via_provider must be @idempotent "
            "so the rotation system mints + persists an idempotency key"
        )

    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    def test_send_bulk_via_provider_is_idempotent(self, provider):
        assert is_idempotent(provider.send_bulk_via_provider), (
            f"{provider.__name__}.send_bulk_via_provider must be @idempotent"
        )


class TestSendBulkBatchCap:
    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    def test_max_batch_is_1000(self, provider):
        assert provider.SEND_BULK_MAX_BATCH == 1000

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    async def test_oversized_batch_rejected(self, provider):
        msg = EmailMessage(
            to=[EmailAddress(address="x@example.com")],
            subject="ok",
            body_text="ok",
        )
        # Cap is 1000; a list of 1001 must be rejected with a typed
        # EmailValidationError before any upstream call is attempted.
        oversize = [msg] * (provider.SEND_BULK_MAX_BATCH + 1)
        with pytest.raises(EmailValidationError):
            await provider.send_bulk_via_provider(
                provider_instance=None, messages=oversize
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    async def test_empty_batch_returns_empty_envelope(self, provider):
        result = await provider.send_bulk_via_provider(
            provider_instance=None, messages=[]
        )
        assert result == {"results": [], "succeeded": 0, "failed": 0}


# ---------------------------------------------------------------------------
# Item 91 — typed validation rejection
# ---------------------------------------------------------------------------


class TestSendViaProviderValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider",
        [SendgridProvider, StalwartProvider, Smtp2goProvider],
    )
    async def test_crlf_in_subject_raises_typed_error(self, provider):
        msg = EmailMessage(
            to=[EmailAddress(address="x@example.com")],
            subject="hi\r\nBcc: smuggled@example.com",
            body_text="ok",
        )
        with pytest.raises(EmailValidationError):
            await provider.send_via_provider(
                provider_instance=None, message=msg
            )
