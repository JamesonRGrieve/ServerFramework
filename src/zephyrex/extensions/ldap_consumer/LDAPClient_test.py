# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search-then-bind against a real OpenLDAP directory (see conftest.py):
who gets in, and every way of being refused."""

import uuid
from dataclasses import replace
from typing import Any, Callable, Dict

import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.ldap_consumer.conftest import (
    Directory,
    unauthenticated_bind,
)
from zephyrex.extensions.ldap_consumer.LDAPClient import (
    ALLOW_PLAINTEXT_LOOPBACK,
    CredentialsRefused,
    DirectorySettings,
    LDAPDirectoryClient,
    configuration_problems,
    external_id_of,
    user_filter,
)

INJECTIONS = (
    "*",
    "al*ce",
    "*)(uid=*",
    "alice)(|(uid=*",
    "alice)(objectClass=*",
    "*)(|(objectClass=*",
    "alice\x00",
    "\\2a",
)


def settings(directory: Directory, **overrides: Any) -> DirectorySettings:
    fields: Dict[str, Any] = {
        key: value
        for key, value in directory.settings(**overrides).items()
        if key != "name"
    }
    fields.setdefault("group_object_filter", "(objectClass=groupOfNames)")
    fields.setdefault("group_member_attribute", "member")
    return DirectorySettings(**fields)


def client(directory: Directory, **overrides: Any) -> LDAPDirectoryClient:
    return LDAPDirectoryClient(settings(directory, **overrides))


class TestSignIn:
    def test_ldaps_with_the_directorys_own_ca(self, directory: Directory) -> None:
        alice = directory.people["alice"]
        account = client(directory).authenticate("alice", alice.password)
        assert account.dn == alice.dn
        assert account.email == alice.mail
        assert account.display_name == "Alice A"
        assert account.groups == (directory.group_dn("engineers"),)

    def test_starttls(self, directory: Directory) -> None:
        bob = directory.people["bob"]
        account = client(
            directory, security="starttls", port=directory.ldap_port
        ).authenticate("bob", bob.password)
        assert account.dn == bob.dn

    def test_identity_is_the_entry_uuid_not_the_dn(self, directory: Directory) -> None:
        alice = directory.people["alice"]
        first = client(directory).authenticate("alice", alice.password)
        again = client(directory).authenticate("alice", alice.password)
        assert uuid.UUID(first.external_id)
        assert first.external_id == again.external_id
        assert first.external_id not in first.dn

    def test_a_username_with_filter_characters_still_signs_in(
        self, directory: Directory
    ) -> None:
        rob = directory.people["rob(admin)"]
        account = client(directory).authenticate("rob(admin)", rob.password)
        assert account.dn == rob.dn

    def test_groups_by_search_match_member_of(self, directory: Directory) -> None:
        bob = directory.people["bob"]
        by_member_of = client(directory).authenticate("bob", bob.password)
        by_search = client(directory, group_source="search").authenticate(
            "bob", bob.password
        )
        expected = tuple(sorted(directory.group_dn(g) for g in ("engineers", "ops")))
        assert by_member_of.groups == expected
        assert by_search.groups == expected
        assert (
            client(directory, group_source="none")
            .authenticate("bob", bob.password)
            .groups
            == ()
        )

    def test_find_looks_up_without_a_password(self, directory: Directory) -> None:
        assert client(directory).find("alice").dn == directory.people["alice"].dn

    def test_check_binds_the_service_account(self, directory: Directory) -> None:
        client(directory).check()


class TestRefusals:
    def test_wrong_password(self, directory: Directory) -> None:
        with pytest.raises(CredentialsRefused):
            client(directory).authenticate("alice", "not-her-password")

    def test_another_persons_password(self, directory: Directory) -> None:
        with pytest.raises(CredentialsRefused):
            client(directory).authenticate("alice", directory.people["bob"].password)

    def test_unknown_user(self, directory: Directory) -> None:
        with pytest.raises(CredentialsRefused):
            client(directory).authenticate("nobody", "whatever-1")

    def test_empty_password_although_the_directory_would_accept_it(
        self, directory: Directory
    ) -> None:
        """The directory answers a DN with an empty password as a successful
        (unauthenticated) bind; the client never sends one."""
        alice = directory.people["alice"]
        assert unauthenticated_bind(directory, alice.dn).returncode == 0
        with pytest.raises(CredentialsRefused):
            client(directory).authenticate("alice", "")

    @pytest.mark.parametrize("username", INJECTIONS)
    def test_filter_injection(self, directory: Directory, username: str) -> None:
        """``al*ce`` unescaped matches alice alone, so her password would let
        it in; escaped, it names no one."""
        with pytest.raises(CredentialsRefused):
            client(directory).authenticate(username, directory.people["alice"].password)

    def test_the_username_is_escaped_in_the_filter(self, directory: Directory) -> None:
        assert user_filter(settings(directory), "*)(uid=*") == (
            "(&(objectClass=inetOrgPerson)(uid=\\2a\\29\\28uid=\\2a))"
        )

    def test_an_ambiguous_username(self, directory: Directory) -> None:
        """Two entries answer ``cn=*``-style matches: never pick one."""
        loose = client(directory, username_attribute="objectClass")
        with pytest.raises(CredentialsRefused):
            loose.authenticate("inetOrgPerson", directory.people["alice"].password)

    def test_plain_ldap_by_default(self, directory: Directory) -> None:
        plain = client(directory, security="plain", port=directory.ldap_port)
        with pytest.raises(PermanentExternalError):
            plain.authenticate("alice", directory.people["alice"].password)
        assert configuration_problems(directory.settings(security="plain"))

    def test_plain_ldap_off_loopback_even_when_allowed(
        self, directory: Directory, set_env: Callable[[str, str], None]
    ) -> None:
        set_env(ALLOW_PLAINTEXT_LOOPBACK, "true")
        remote = client(directory, security="plain", host="192.0.2.1")
        with pytest.raises(PermanentExternalError):
            remote.authenticate("alice", directory.people["alice"].password)
        assert configuration_problems(
            directory.settings(security="plain", host="192.0.2.1")
        )

    def test_plain_ldap_on_loopback_when_allowed(
        self, directory: Directory, set_env: Callable[[str, str], None]
    ) -> None:
        set_env(ALLOW_PLAINTEXT_LOOPBACK, "true")
        plain = client(directory, security="plain", port=directory.ldap_port)
        alice = directory.people["alice"]
        assert plain.authenticate("alice", alice.password).dn == alice.dn
        assert not configuration_problems(directory.settings(security="plain"))

    def test_a_certificate_no_trusted_ca_signed(self, directory: Directory) -> None:
        for ca in (None, directory.other_ca_pem):
            with pytest.raises(PermanentExternalError):
                client(directory, ca_certificate=ca).authenticate(
                    "alice", directory.people["alice"].password
                )

    def test_starttls_with_an_untrusted_certificate(self, directory: Directory) -> None:
        untrusted = client(
            directory,
            security="starttls",
            port=directory.ldap_port,
            ca_certificate=directory.other_ca_pem,
        )
        with pytest.raises(PermanentExternalError):
            untrusted.authenticate("alice", directory.people["alice"].password)

    def test_a_certificate_for_another_name(self, directory: Directory) -> None:
        """The certificate names localhost; reached as 127.0.0.1 it does not
        match, though its CA is trusted."""
        with pytest.raises(PermanentExternalError):
            client(directory, host="127.0.0.1").authenticate(
                "alice", directory.people["alice"].password
            )

    def test_a_refused_service_account(self, directory: Directory) -> None:
        wrong = client(directory, bind_password="not-the-service-password")
        with pytest.raises(AuthExternalError) as refused:
            wrong.authenticate("alice", directory.people["alice"].password)
        assert not isinstance(refused.value, CredentialsRefused)

    def test_an_unreachable_directory(self, directory: Directory) -> None:
        closed = replace(settings(directory), port=1, timeout_seconds=2)
        with pytest.raises(TransientExternalError):
            LDAPDirectoryClient(closed).authenticate(
                "alice", directory.people["alice"].password
            )

    def test_a_missing_base(self, directory: Directory) -> None:
        with pytest.raises(PermanentExternalError):
            client(directory, base_dn="ou=nowhere,dc=example,dc=test").authenticate(
                "alice", directory.people["alice"].password
            )


class TestConfiguration:
    def test_a_usable_configuration(self, directory: Directory) -> None:
        assert configuration_problems(directory.settings()) == []

    @pytest.mark.parametrize(
        "overrides",
        [
            {"username_attribute": "uid)(x"},
            {"id_attribute": ""},
            {"user_object_filter": "objectClass=person"},
            {"user_object_filter": "(objectClass=person"},
            {"group_source": "search", "group_search_base": None},
            {"security": "ssl"},
            {"timeout_seconds": 0},
            {"ca_certificate": "not a certificate"},
            {"host": ""},
        ],
    )
    def test_problems_are_named(
        self, directory: Directory, overrides: Dict[str, Any]
    ) -> None:
        assert configuration_problems(directory.settings(**overrides))


def test_an_active_directory_object_guid_reads_as_its_guid() -> None:
    guid = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")
    assert external_id_of("objectGUID", [guid.bytes_le]) == str(guid)
    assert external_id_of("entryUUID", [b"5a8d-text-id"]) == "5a8d-text-id"
    assert external_id_of("entryUUID", []) is None
