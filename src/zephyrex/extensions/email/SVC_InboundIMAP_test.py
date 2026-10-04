# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inbound IMAP poller against a real IMAP server on a loopback port
(IMAPTestServer: no dovecot where the tests run), reading mailboxes
configured on real provider instances, delivering to the real hook point.

Before this, nothing read a mailbox into the inbound hook: the IMAP provider
only listed messages on request, over whatever transport, plaintext
included."""

from typing import Any, Callable, Dict, Iterator, List

import pytest
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.email.BLL_InboundEmail import (
    EmailInboundMailboxManager,
    root_mailboxes,
)
from zephyrex.extensions.email.EXT_EMail import EXT_EMail
from zephyrex.extensions.email.IMAPTestServer import (
    SEEN,
    IMAPTestServer,
    Transport,
    issue_certificates,
)
from zephyrex.extensions.email.InboundEmail import (
    MAX_INBOUND_EMAIL_BYTES,
    InboundEmail,
)
from zephyrex.extensions.email.InboundTestSupport import (
    listening,
    operator_instance,
    raw_message,
)
from zephyrex.extensions.email.InboundIMAP import (
    InboundConfigError,
    TLSRequiredError,
    mailbox_config,
)
from zephyrex.extensions.email.PRV_IMAP_EMail import IMAPProvider
from zephyrex.extensions.email.SVC_InboundIMAP import (
    InboundIMAPService,
    polled_instances,
)
from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Providers import ProviderInstanceManager, ProviderInstanceModel

USER = "inbox@example.org"
PASSWORD = "mailbox-password"


def settings_for(imap: IMAPTestServer, **overrides: str) -> Dict[str, str]:
    settings = {
        "inbound_enabled": "true",
        "inbound_host": "127.0.0.1",
        "inbound_port": str(imap.port),
        "inbound_security": imap.transport,
        "inbound_ca_certificate": imap.certificates.ca_pem,
        "inbound_username": USER,
        "inbound_password": PASSWORD,
        "inbound_after": "seen",
    }
    settings.update(overrides)
    return settings


def subjects(messages: List[Any]) -> List[str]:
    return [message.subject for message in messages]


MailboxFactory = Callable[..., ProviderInstanceModel]
# The server each refusal case runs against.
REFUSAL_TRANSPORTS: Dict[str, Transport] = {
    "starttls_not_offered": "starttls",
    "plain_without_opt_in": "plain",
    "tls_to_a_plaintext_server": "plain",
    "untrusted_certificate": "tls",
}


@pytest.fixture
def received() -> Iterator[List[InboundEmail]]:
    with listening() as seen:
        yield seen


class TestInboundIMAPPoller(ExtensionServerMixin):
    extension_class = EXT_EMail

    @pytest.fixture
    def mailbox(self, model_registry) -> Iterator[MailboxFactory]:
        """Operator mailboxes for this test, disabled after it so no later
        test's sweep polls them."""
        made: List[ProviderInstanceModel] = []

        def make(settings: Dict[str, str], **options: Any) -> ProviderInstanceModel:
            instance = operator_instance(model_registry, settings, **options)
            made.append(instance)
            return instance

        yield make
        instances = ProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        )
        for instance in made:
            instances.update(id=instance.id, enabled=False)

    @pytest.fixture
    def poller(self, model_registry) -> InboundIMAPService:
        return InboundIMAPService(
            requester_id=env("ROOT_ID"), model_registry=model_registry
        )

    def _cursor(
        self, model_registry: Any, instance: Any, mailbox: str = "INBOX"
    ) -> Any:
        return root_mailboxes(model_registry).cursor(instance.id, mailbox)

    async def test_new_mail_is_delivered_once(
        self, model_registry, mailbox, poller, received
    ):
        with IMAPTestServer(USER, PASSWORD) as imap:
            imap.deliver(raw_message("One"))
            imap.deliver(raw_message("Two"))
            instance = mailbox(settings_for(imap))

            assert (await poller.poll(IMAPProvider, instance)).delivered == 2
            assert subjects(received) == ["One", "Two"]
            assert all(SEEN in message.flags for message in imap.messages())

            assert (await poller.poll(IMAPProvider, instance)).delivered == 0
            # The cursor, not only the \Seen flag, keeps them from coming
            # again: another client marking everything unread changes nothing.
            imap.clear_flags()
            assert (await poller.poll(IMAPProvider, instance)).delivered == 0

            imap.deliver(raw_message("Three"))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            assert subjects(received) == ["One", "Two", "Three"]
            assert imap.logins and all(login.encrypted for login in imap.logins)
        cursor = self._cursor(model_registry, instance)
        assert (cursor.uid_validity, cursor.last_uid) == ("1000", "3")
        assert cursor.last_error is None and cursor.last_polled_at is not None

    @pytest.mark.parametrize("transport", ["starttls", "plain"])
    async def test_starttls_and_opted_in_loopback_plaintext_deliver(
        self, model_registry, mailbox, poller, received, transport: Transport
    ):
        with IMAPTestServer(USER, PASSWORD, transport=transport) as imap:
            imap.deliver(raw_message("Over " + transport))
            instance = mailbox(settings_for(imap, inbound_allow_plaintext="true"))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            assert [login.encrypted for login in imap.logins] == [
                transport == "starttls"
            ]
        assert subjects(received) == ["Over " + transport]

    @pytest.mark.parametrize("case", sorted(REFUSAL_TRANSPORTS))
    async def test_credentials_never_cross_an_unencrypted_or_unverified_link(
        self, model_registry, mailbox, poller, received, case
    ) -> None:
        transport = REFUSAL_TRANSPORTS[case]
        with IMAPTestServer(
            USER,
            PASSWORD,
            transport=transport,
            offer_starttls=case != "starttls_not_offered",
        ) as imap:
            imap.deliver(raw_message("Never read"))
            overrides: Dict[str, str] = {}
            if case == "tls_to_a_plaintext_server":
                overrides["inbound_security"] = "tls"
            if case == "untrusted_certificate":
                overrides["inbound_ca_certificate"] = issue_certificates().ca_pem
            instance = mailbox(settings_for(imap, **overrides))
            result = await poller.poll(IMAPProvider, instance)
            assert imap.logins == []
            assert all(SEEN not in message.flags for message in imap.messages())
        assert result.delivered == 0 and received == []
        if case == "starttls_not_offered":
            assert "TLSRequiredError" in (
                self._cursor(model_registry, instance).last_error or ""
            )

    def test_plaintext_is_refused_off_loopback_even_when_opted_in(self, model_registry):
        instance = operator_instance(
            model_registry,
            {
                "inbound_host": "imap.example.org",
                "inbound_username": USER,
                "inbound_password": PASSWORD,
                "inbound_security": "plain",
                "inbound_allow_plaintext": "true",
            },
        )
        with pytest.raises(TLSRequiredError):
            mailbox_config(IMAPProvider, instance)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"inbound_host": ""},
            {"inbound_password": ""},
            {"inbound_security": "ssl3"},
            {"inbound_after": "archive"},
            {"inbound_after": "move"},
            {"inbound_after": "move", "inbound_move_to": "INBOX"},
            {"inbound_poll_seconds": "1"},
            {"inbound_port": "70000"},
            {"inbound_mailbox": "Inbox\r\nA1 DELETE INBOX"},
        ],
        ids=[
            "no-host",
            "no-password",
            "unknown-security",
            "unknown-after",
            "move-nowhere",
            "move-to-itself",
            "too-frequent",
            "bad-port",
            "injected-mailbox",
        ],
    )
    def test_a_mailbox_that_can_never_be_read_is_a_config_error(
        self, model_registry, overrides
    ):
        settings = {
            "inbound_host": "127.0.0.1",
            "inbound_username": USER,
            "inbound_password": PASSWORD,
            **overrides,
        }
        instance = operator_instance(
            model_registry, {key: value for key, value in settings.items() if value}
        )
        with pytest.raises(InboundConfigError):
            mailbox_config(IMAPProvider, instance)

    async def test_a_new_uidvalidity_reads_the_mailbox_afresh(
        self, model_registry, mailbox, poller, received
    ):
        with IMAPTestServer(USER, PASSWORD) as imap:
            imap.create_mailbox("Processed")
            imap.deliver(raw_message("One"))
            imap.deliver(raw_message("Two"))
            instance = mailbox(
                settings_for(imap, inbound_after="move", inbound_move_to="Processed")
            )
            assert (await poller.poll(IMAPProvider, instance)).delivered == 2
            assert imap.messages() == []
            assert len(imap.messages("Processed")) == 2

            # The server is rebuilt: a new UIDVALIDITY, and the next message
            # numbered 1, below the cursor's UID 2.
            imap.deliver(raw_message("Three"))
            imap.renumber("INBOX", uid_validity=2000)
            assert [message.uid for message in imap.messages()] == [1]
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
        assert subjects(received) == ["One", "Two", "Three"]
        cursor = self._cursor(model_registry, instance)
        assert (cursor.uid_validity, cursor.last_uid) == ("2000", "1")

    async def test_mail_already_delivered_stays_delivered_across_a_new_uidvalidity(
        self, mailbox, poller, received
    ):
        with IMAPTestServer(USER, PASSWORD) as imap:
            imap.deliver(raw_message("One"))
            instance = mailbox(settings_for(imap))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            imap.renumber("INBOX", uid_validity=3000)
            assert (await poller.poll(IMAPProvider, instance)).delivered == 0
            imap.deliver(raw_message("Two"))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
        assert subjects(received) == ["One", "Two"]

    @pytest.mark.parametrize("extensions", [("UIDPLUS", "MOVE"), ("UIDPLUS",)])
    async def test_moved_mail_leaves_the_mailbox(
        self, mailbox, poller, received, extensions
    ):
        """With MOVE, one command; with only UIDPLUS, a copy and an expunge
        of that message alone."""
        with IMAPTestServer(USER, PASSWORD, extensions=extensions) as imap:
            imap.create_mailbox("Processed")
            imap.deliver(raw_message("Moved"))
            instance = mailbox(
                settings_for(imap, inbound_after="move", inbound_move_to="Processed")
            )
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            assert imap.messages() == []
            [moved] = imap.messages("Processed")
            assert b"Subject: Moved" in moved.body
        assert subjects(received) == ["Moved"]

    async def test_deleted_mail_is_expunged_alone(self, mailbox, poller, received):
        with IMAPTestServer(USER, PASSWORD) as imap:
            other = imap.deliver(raw_message("Someone else's"), flags=["\\Deleted"])
            instance = mailbox(settings_for(imap, inbound_after="delete"))
            # Read from above the other client's message: only new mail.
            root_mailboxes(poller.model_registry).advance(
                self._cursor(poller.model_registry, instance).id,
                None,
                "0",
                "1000",
                str(other),
            )
            imap.deliver(raw_message("Delivered"))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            assert [m.uid for m in imap.messages()] == [other]
        assert subjects(received) == ["Delivered"]

    async def test_delete_without_uidplus_is_refused_untouched(
        self, model_registry, mailbox, poller, received
    ):
        with IMAPTestServer(USER, PASSWORD, extensions=("MOVE",)) as imap:
            imap.deliver(raw_message("Stays"))
            instance = mailbox(settings_for(imap, inbound_after="delete"))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 0
            assert len(imap.messages()) == 1
        assert received == []
        assert "UIDPLUS" in (self._cursor(model_registry, instance).last_error or "")

    async def test_an_oversized_message_is_passed_over_and_left(
        self, mailbox, poller, received
    ):
        with IMAPTestServer(USER, PASSWORD) as imap:
            huge = imap.deliver(
                b"Subject: Huge\r\n\r\n" + b"x" * MAX_INBOUND_EMAIL_BYTES
            )
            imap.deliver(raw_message("Small"))
            instance = mailbox(settings_for(imap))
            assert (await poller.poll(IMAPProvider, instance)).delivered == 1
            left = {m.uid: m.flags for m in imap.messages()}
            assert SEEN not in left[huge]
            assert (await poller.poll(IMAPProvider, instance)).delivered == 0
        assert subjects(received) == ["Small"]

    async def test_a_failing_mailbox_backs_off_and_spares_the_others(
        self, model_registry, mailbox, poller, received
    ):
        with (
            IMAPTestServer(USER, PASSWORD) as good,
            IMAPTestServer(USER, PASSWORD) as bad,
        ):
            good.deliver(raw_message("Good"))
            bad.deliver(raw_message("Bad"))
            mailbox(settings_for(good))
            refused = mailbox(settings_for(bad, inbound_password="wrong"))

            await poller.update()
            assert subjects(received) == ["Good"]
            assert len(bad.logins) == 1
            cursor = self._cursor(model_registry, refused)
            assert "AUTHENTICATIONFAILED" in (cursor.last_error or "")

            # Backing off: the next sweep leaves it alone (and the good one,
            # not yet due), and the service is still running.
            await poller.update()
            assert len(bad.logins) == 1 and len(good.logins) == 1
            assert not poller.failed

    def test_only_enabled_operator_imap_mailboxes_are_polled(
        self, model_registry, mailbox
    ):
        polled = mailbox({"inbound_enabled": "true"})
        not_polled = [
            mailbox({"inbound_enabled": "true"}, scope="user"),
            mailbox({"inbound_enabled": "false"}),
            mailbox({}),
            mailbox({}, provider_name="pop3"),
        ]
        disabled = mailbox({"inbound_enabled": "true"})
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).update(id=disabled.id, enabled=False)
        found = {str(instance.id) for _, instance in polled_instances(model_registry)}
        assert str(polled.id) in found
        assert not found & {str(i.id) for i in [*not_polled, disabled]}

    def test_a_message_is_claimed_once(self, model_registry, mailbox):
        instance = mailbox({})
        cursors = root_mailboxes(model_registry)
        cursor = cursors.cursor(instance.id, "INBOX")
        assert cursors.advance(cursor.id, None, "0", "7", "3")
        # A second reader that read the cursor before the first moved it.
        assert not cursors.advance(cursor.id, None, "0", "7", "3")
        assert not cursors.advance(cursor.id, "7", "2", "7", "4")
        assert cursors.advance(cursor.id, "7", "3", "7", "4")
        assert cursors.cursor(instance.id, "INBOX").id == cursor.id

    def test_cursors_are_server_state(self, model_registry, admin_a):
        with pytest.raises(HTTPException) as refused:
            EmailInboundMailboxManager(
                requester_id=admin_a.id, model_registry=model_registry
            )
        assert refused.value.status_code == 403
