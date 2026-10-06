# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any, Dict, Iterator, List

import pytest

from zephyrex.AbstractTest import ClassOfTestsConfig, CategoryOfTest
from zephyrex.logic.AbstractBLLTest import AbstractBLLTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.EmailTestSupport import email_instance
from zephyrex.extensions.email.EXT_EMail import FROM_EMAIL_SETTING, EXT_EMail
from zephyrex.extensions.email.MailAPITestServers import SMTP2goTestServer
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import (
    RotationProviderInstanceManager,
    RotationProviderInstanceModel,
)

from zephyrex.extensions.auth_invitations.BLL_Invitations import InvitationManager

ACCOUNT_KEY = "invitations-account-key"
SENDER = "invitations@example.org"
# Mail goes out on a background thread; this bounds the wait for it.
DELIVERY_TIMEOUT_SECONDS = 15


class TestEmailManager(AbstractBLLTest, ExtensionServerMixin):
    """
    Test class for email-related behavior that integrates with the
    InvitationManager. We run the generic BLL tests (create/list/get/update/etc.)
    against InvitationManager so the full suite of logic tests in this
    extension folder remain active.
    """

    class_under_test = InvitationManager
    extension_class = EXT_EMail

    test_config = ClassOfTestsConfig(
        categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
    )
    create_fields: Dict[str, Any] = {}
    update_fields: Dict[str, Any] = {}
    unique_fields: List[str] = []

    @pytest.fixture
    def account_in_the_root_rotation(
        self, model_registry, monkeypatch, set_env
    ) -> Iterator[SMTP2goTestServer]:
        """An SMTP2go account served in process, an operator instance on it
        in the email extension's root rotation, and no SendGrid configured."""
        set_env("SENDGRID_API_KEY", "")
        set_env("SENDGRID_FROM_EMAIL", "")
        root = EXT_EMail.root
        assert root is not None, "the email extension has a root rotation"
        links = RotationProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        with SMTP2goTestServer(ACCOUNT_KEY) as account:
            monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", account.host)
            instance = email_instance(
                model_registry,
                "smtp2go",
                {"api_url": account.api_url, FROM_EMAIL_SETTING: SENDER},
                api_key=ACCOUNT_KEY,
            )
            link = RotationProviderInstanceModel.model_validate(
                links.create(
                    rotation_id=root.target_id,
                    provider_instance_id=instance.id,
                    parent_id=None,
                ),
                from_attributes=True,
            )
            try:
                yield account
            finally:
                links.delete(link.id)

    def test_an_invitee_is_mailed_by_whichever_provider_the_rotation_holds(
        self, admin_a, team_a, model_registry, account_in_the_root_rotation
    ):
        """Adding an invitee used to send mail only when SENDGRID_API_KEY and
        SENDGRID_FROM_EMAIL were set, though the mail goes through the root
        rotation's providers: a deployment sending through any other
        provider never mailed its invitees."""
        account = account_in_the_root_rotation
        with InvitationManager(
            model_registry=model_registry,
            requester_id=admin_a.id,
        ) as manager:
            invitation = manager.create(
                team_id=team_a.id,
                role_id=env("USER_ROLE_ID"),
                email="invitee@example.org",
            )
        assert invitation is not None

        assert account.wait_for(1, timeout=DELIVERY_TIMEOUT_SECONDS)
        (sent,) = account.sent
        assert sent["to"] == ["invitee@example.org"]
        assert sent["sender"] == SENDER
        assert sent["subject"] == f"You've been invited to {team_a.name}"
        assert "accept-invitation?" in sent["text_body"]
