# SPDX-License-Identifier: AGPL-3.0-or-later
"""radius_provider end to end: the app's background services start the UDP
listener on a free port, and real RADIUS clients (FreeRADIUS's radclient and
pyrad packets on a plain UDP socket) authenticate framework accounts with it.
"""

import hashlib
import hmac
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.radius_provider import RADIUSPackets as wire
from zephyrex.extensions.radius_provider.BLL_RADIUSProvider import (
    RadiusClientManager,
    RadiusTeamPolicyManager,
    normalized_address,
)
from zephyrex.extensions.radius_provider.EXT_RADIUSProvider import (
    EXT_RADIUSProvider,
)
from zephyrex.extensions.radius_provider.SVC_RADIUSProvider import (
    SERVICE_ID,
    DuplicateCache,
    RADIUSAuthService,
    RequestHandler,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.SecretEncryption import decrypt_secret
from zephyrex.logic.AbstractService import ServiceRegistry
from zephyrex.testing.factories import (
    TEST_PASSWORD,
    add_user_to_team,
    create_team,
    create_user,
)

from pyrad.packet import AccessAccept, AccessReject, AccessRequest, AuthPacket

NAS_HOST = "127.0.0.1"
GATED_NAS_HOST = "127.0.0.2"
UNKNOWN_HOST = "127.0.0.3"
# Test-only values: these secrets exist nowhere but this module.
NAS_SECRET = "nas-shared-secret-for-the-tests"
GATED_NAS_SECRET = "gated-nas-secret-for-the-tests"
LISTEN_TIMEOUT_SECONDS = 10
# How long to wait for an answer that should come, and for one that should
# not (a dropped request is silence).
ANSWER_TIMEOUT_SECONDS = 5.0
SILENCE_SECONDS = 1.5
RADCLIENT_TIMEOUT_SECONDS = 30
VLAN = 42
AUTH_LOCKOUT_THRESHOLD = 5


def access_request(
    user_name: str,
    password: str,
    secret: str,
    *,
    message_authenticator: bool = True,
    **attributes: Any,
) -> Tuple[AuthPacket, bytes]:
    request = AuthPacket(
        code=AccessRequest,
        secret=secret.encode(),
        dict=wire.dictionary(),
        User_Name=user_name,
        **attributes,
    )
    request["User-Password"] = request.PwCrypt(password)
    if message_authenticator:
        request.add_message_authenticator()
    raw: bytes = request.RequestPacket()
    return request, raw


def exchange(
    raw: bytes,
    port: int,
    source: str = NAS_HOST,
    timeout: float = ANSWER_TIMEOUT_SECONDS,
) -> Optional[bytes]:
    """Send one datagram from ``source``; the answer, or None on silence."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((source, 0))
        sock.settimeout(timeout)
        sock.sendto(raw, (NAS_HOST, port))
        try:
            answer, _ = sock.recvfrom(wire.MAX_PACKET_LENGTH)
        except socket.timeout:
            return None
        return answer


def reply_message_authenticator_is_valid(
    raw_reply: bytes, request: AuthPacket, secret: str
) -> bool:
    """RFC 3579 §3.2: the reply's HMAC-MD5 is over the reply with the Request
    Authenticator in place of the Response Authenticator."""
    found = [
        a
        for a in wire.raw_attributes(raw_reply)
        if a.type == wire.MESSAGE_AUTHENTICATOR
    ]
    if len(found) != 1:
        return False
    start = found[0].offset + 2
    signed = (
        raw_reply[:4]
        + request.authenticator
        + raw_reply[wire.HEADER_LENGTH : start]
        + bytes(16)
        + raw_reply[start + 16 :]
    )
    expected = hmac.new(secret.encode(), signed, hashlib.md5).digest()
    return hmac.compare_digest(expected, found[0].value)


def verified_reply(request: AuthPacket, raw_reply: Optional[bytes]) -> AuthPacket:
    """The reply, after checking every integrity property a NAS checks."""
    assert raw_reply is not None, "no answer"
    reply = request.CreateReply(packet=raw_reply)
    assert request.VerifyReply(reply, raw_reply), "bad Response Authenticator"
    assert (
        raw_reply[wire.HEADER_LENGTH] == wire.MESSAGE_AUTHENTICATOR
    ), "Message-Authenticator is not the first attribute"
    secret = request.secret.decode()
    assert reply_message_authenticator_is_valid(raw_reply, request, secret)
    return reply


def failed_logins(model_registry: Any, user_id: str) -> int:
    from zephyrex.extensions.auth_lockout.BLL_Lockout import FailedLoginAttemptModel

    return int(
        FailedLoginAttemptModel.DB(model_registry.DB.manager.Base).count(
            requester_id=env("ROOT_ID"), model_registry=model_registry, user_id=user_id
        )
    )


def root_headers() -> dict:
    return {"X-API-Key": env("ROOT_API_KEY")}


def upsert_client(
    model_registry: Any,
    name: str,
    address: str,
    secret: str,
    required_team_id: Optional[str] = None,
    is_enabled: bool = True,
) -> Any:
    """The client at ``address``, made or reset: the app database outlives
    a test run."""
    clients = RadiusClientManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    )
    found = clients.list(
        address=normalized_address(address), filters=[clients.DB.deleted_at.is_(None)]
    )
    fields = dict(
        name=name,
        shared_secret=secret,
        required_team_id=required_team_id,
        is_enabled=is_enabled,
    )
    if found:
        return clients.update(found[0].id, **fields)
    return clients.create(address=address, **fields)


class TestRADIUSProvider(ExtensionServerMixin):
    extension_class = EXT_RADIUSProvider

    @pytest.fixture(scope="module")
    def gate_team(self, server, admin_a):
        return create_team(server, admin_a.id, name="Staff network")

    @pytest.fixture(scope="module")
    def vlan_team(self, server, admin_a, model_registry):
        team = create_team(server, admin_a.id, name="VLAN 42")
        RadiusTeamPolicyManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).create(team_id=team.id, vlan_id=VLAN, filter_id="staff", priority=10)
        return team

    @pytest.fixture(scope="module")
    def nas_clients(self, model_registry, gate_team):
        return [
            upsert_client(model_registry, "Access points", NAS_HOST, NAS_SECRET),
            upsert_client(
                model_registry,
                "Staff VPN",
                GATED_NAS_HOST,
                GATED_NAS_SECRET,
                required_team_id=gate_team.id,
            ),
        ]

    @pytest.fixture(scope="module")
    def listener(self, server, nas_clients) -> Iterator[RADIUSAuthService]:
        """The app's own lifespan starts the listener, as in production."""
        from zephyrex.lib import Environment

        with pytest.MonkeyPatch.context() as patch:
            for name, value in (
                ("RUN_BACKGROUND_SERVICES", "true"),
                ("RADIUS_PROVIDER_BIND_ADDRESS", NAS_HOST),
                ("RADIUS_PROVIDER_AUTH_PORT", "0"),
            ):
                patch.setenv(name, value)
                if hasattr(Environment.settings, name):
                    patch.setattr(Environment.settings, name, value)
            with server:
                service = ServiceRegistry.get(SERVICE_ID)
                assert isinstance(service, RADIUSAuthService)
                assert service.listening.wait(LISTEN_TIMEOUT_SECONDS)
                yield service

    @pytest.fixture
    def port(self, listener: RADIUSAuthService) -> int:
        assert listener.local_address is not None
        return listener.local_address[1]

    @pytest.fixture
    def user(self, server):
        return create_user(server, email=f"radius_{uuid.uuid4().hex[:8]}@example.com")

    # -- the extension ----------------------------------------------------

    def test_declares_pyrad_and_optional_lockout(self):
        assert EXT_RADIUSProvider.pip_requirements() == ["pyrad>=2.5.4"]
        assert [(d.name, d.optional) for d in EXT_RADIUSProvider.dependencies.ext] == [
            ("auth_lockout", True)
        ]

    def test_validate_config_names_a_bad_port(self, set_env):
        set_env("RADIUS_PROVIDER_AUTH_PORT", "70000")
        assert any("not a UDP port" in i for i in EXT_RADIUSProvider.validate_config())

    # -- configuration is root's ------------------------------------------

    def test_a_user_cannot_read_or_register_clients(self, server, user_b, nas_clients):
        headers = {"Authorization": f"Bearer {user_b.jwt}"}
        assert server.get("/v1/radius/client", headers=headers).status_code == 403
        created = server.post(
            "/v1/radius/client",
            headers=headers,
            json={
                "radius_client": {
                    "name": "Rogue",
                    "address": "10.9.9.9",
                    "shared_secret": "a-rogue-shared-secret",
                }
            },
        )
        assert created.status_code == 403
        policy = server.get("/v1/radius/team-policy", headers=headers)
        assert policy.status_code == 403

    def test_root_registers_a_client_and_never_sees_its_secret(
        self, server, model_registry
    ):
        address = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.0/24"
        secret = f"rest-secret-{uuid.uuid4().hex}"
        created = server.post(
            "/v1/radius/client",
            headers=root_headers(),
            json={
                "radius_client": {
                    "name": "Office switches",
                    "address": address,
                    "shared_secret": secret,
                }
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()["radius_client"]
        assert body["address"] == address
        assert "shared_secret" not in body
        assert secret not in created.text

        stored = RadiusClientManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).get(id=body["id"])
        assert decrypt_secret(stored.shared_secret) == secret

    def test_the_shared_secret_is_never_listed(self, server, nas_clients):
        listed = server.get("/v1/radius/client", headers=root_headers())
        assert listed.status_code == 200
        assert all("shared_secret" not in c for c in listed.json()["radius_clients"])
        assert NAS_SECRET not in listed.text

    @pytest.mark.parametrize(
        "body,status",
        [
            ({"address": "10.1.2.3/24", "shared_secret": "x" * 16}, 400),
            ({"address": "not-an-address", "shared_secret": "x" * 16}, 400),
            ({"address": "10.1.2.0/24", "shared_secret": "too-short"}, 400),
            ({"address": NAS_HOST, "shared_secret": "x" * 16}, 409),
        ],
        ids=["host-bits", "garbage", "short-secret", "duplicate"],
    )
    def test_client_registration_is_validated(self, server, nas_clients, body, status):
        response = server.post(
            "/v1/radius/client",
            headers=root_headers(),
            json={"radius_client": {"name": "Bad", **body}},
        )
        assert response.status_code == status, response.text

    # -- answering ----------------------------------------------------------

    def test_correct_password_is_accepted_and_signed(self, port, user):
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        reply = verified_reply(request, exchange(raw, port))
        assert reply.code == AccessAccept

    def test_username_works_as_well_as_email(self, port, user):
        request, raw = access_request(user.username, TEST_PASSWORD, NAS_SECRET)
        assert verified_reply(request, exchange(raw, port)).code == AccessAccept

    def test_wrong_password_is_rejected_and_signed(self, port, user, model_registry):
        request, raw = access_request(user.email, "not-the-password", NAS_SECRET)
        reply = verified_reply(request, exchange(raw, port))
        assert reply.code == AccessReject
        assert failed_logins(model_registry, user.id) == 1

    def test_deleted_user_is_rejected(self, port, user, model_registry):
        from zephyrex.logic.BLL_Auth import UserModel

        UserModel.DB(model_registry.DB.manager.Base).delete(
            requester_id=env("ROOT_ID"), model_registry=model_registry, id=user.id
        )
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        assert verified_reply(request, exchange(raw, port)).code == AccessReject

    def test_unknown_user_is_rejected(self, port):
        request, raw = access_request("nobody@example.com", "whatever", NAS_SECRET)
        assert verified_reply(request, exchange(raw, port)).code == AccessReject

    def test_unknown_nas_is_ignored(self, port, user):
        _, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        assert exchange(raw, port, source=UNKNOWN_HOST, timeout=SILENCE_SECONDS) is None

    def test_wrong_shared_secret_is_ignored(self, port, user):
        _, raw = access_request(user.email, TEST_PASSWORD, "the-wrong-shared-secret")
        assert exchange(raw, port, timeout=SILENCE_SECONDS) is None

    def test_missing_message_authenticator_is_ignored(self, port, user):
        _, raw = access_request(
            user.email, TEST_PASSWORD, NAS_SECRET, message_authenticator=False
        )
        assert exchange(raw, port, timeout=SILENCE_SECONDS) is None

    def test_retransmission_gets_the_same_answer_decided_once(
        self, port, user, model_registry
    ):
        request, raw = access_request(user.email, "not-the-password", NAS_SECRET)
        first = exchange(raw, port)
        again = exchange(raw, port)
        assert verified_reply(request, first).code == AccessReject
        assert again == first
        assert failed_logins(model_registry, user.id) == 1

    def test_replay_after_the_duplicate_window_is_ignored(self, model_registry, user):
        handler = RequestHandler(model_registry)
        handler.duplicates = DuplicateCache(window_seconds=0)
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        source = (NAS_HOST, 40000)
        assert verified_reply(request, handler.handle(raw, source)).code == AccessAccept
        assert handler.handle(raw, source) is None

    def test_eap_is_rejected(self, port, user):
        request, raw = access_request(
            user.email, TEST_PASSWORD, NAS_SECRET, EAP_Message=b"\x02\x01\x00\x05\x01"
        )
        assert verified_reply(request, exchange(raw, port)).code == AccessReject

    def test_proxy_state_is_echoed(self, port, user):
        request, raw = access_request(
            user.email, TEST_PASSWORD, NAS_SECRET, Proxy_State=b"proxied-by-front"
        )
        reply = verified_reply(request, exchange(raw, port))
        assert reply["Proxy-State"] == [b"proxied-by-front"]

    def test_team_policy_assigns_a_vlan(self, server, port, user, vlan_team):
        add_user_to_team(server, user.id, vlan_team.id, env("USER_ROLE_ID"))
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        reply = verified_reply(request, exchange(raw, port))
        assert reply.code == AccessAccept
        assert reply["Tunnel-Type"] == ["VLAN"]
        assert reply["Tunnel-Medium-Type"] == ["IEEE-802"]
        assert reply["Tunnel-Private-Group-Id"] == [str(VLAN)]
        assert reply["Filter-Id"] == ["staff"]

    def test_gated_nas_accepts_only_its_team(self, server, port, user, gate_team):
        request, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
        outsider = verified_reply(request, exchange(raw, port, source=GATED_NAS_HOST))
        assert outsider.code == AccessReject

        add_user_to_team(server, user.id, gate_team.id, env("USER_ROLE_ID"))
        request, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
        member = verified_reply(request, exchange(raw, port, source=GATED_NAS_HOST))
        assert member.code == AccessAccept

    def test_repeated_failures_lock_the_account(self, port, user, model_registry):
        for _ in range(AUTH_LOCKOUT_THRESHOLD):
            request, raw = access_request(user.email, "guess", NAS_SECRET)
            assert verified_reply(request, exchange(raw, port)).code == AccessReject
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        assert verified_reply(request, exchange(raw, port)).code == AccessReject

    def test_a_disabled_client_is_ignored(self, port, user, model_registry):
        host = "127.0.0.5"
        upsert_client(model_registry, "Retired switch", host, NAS_SECRET)
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        answer = exchange(raw, port, source=host)
        assert verified_reply(request, answer).code == AccessAccept

        upsert_client(
            model_registry, "Retired switch", host, NAS_SECRET, is_enabled=False
        )
        _, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        assert exchange(raw, port, source=host, timeout=SILENCE_SECONDS) is None

    def test_a_deleted_client_is_ignored(self, port, user, model_registry):
        """Root reads see deleted rows; the listener must not."""
        host = "127.0.0.6"
        client = upsert_client(model_registry, "Returned AP", host, NAS_SECRET)
        request, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        answer = exchange(raw, port, source=host)
        assert verified_reply(request, answer).code == AccessAccept

        RadiusClientManager(
            model_registry=model_registry, requester_id=env("ROOT_ID")
        ).delete(id=client.id)
        _, raw = access_request(user.email, TEST_PASSWORD, NAS_SECRET)
        assert exchange(raw, port, source=host, timeout=SILENCE_SECONDS) is None

    def test_a_removed_membership_no_longer_admits(
        self, server, port, user, gate_team, model_registry
    ):
        from zephyrex.logic.BLL_Auth import UserTeamModel

        membership = add_user_to_team(
            server, user.id, gate_team.id, env("USER_ROLE_ID")
        )
        request, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
        member = verified_reply(request, exchange(raw, port, source=GATED_NAS_HOST))
        assert member.code == AccessAccept

        UserTeamModel.DB(model_registry.DB.manager.Base).delete(
            requester_id=env("ROOT_ID"), model_registry=model_registry, id=membership.id
        )
        request, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
        removed = verified_reply(request, exchange(raw, port, source=GATED_NAS_HOST))
        assert removed.code == AccessReject

    def test_the_most_specific_client_answers(self, port, user, model_registry):
        """127.0.0.0/24 covers the NAS too, but its /32 entry, with its own
        secret, is the one that answers it."""
        network = "127.0.0.0/24"
        upsert_client(model_registry, "Loopback", network, GATED_NAS_SECRET)
        try:
            _, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
            assert exchange(raw, port, timeout=SILENCE_SECONDS) is None
            request, raw = access_request(user.email, TEST_PASSWORD, GATED_NAS_SECRET)
            answer = exchange(raw, port, source="127.0.0.9")
            assert verified_reply(request, answer).code == AccessAccept
        finally:
            # Leave the rest of the loopback range unknown to later tests.
            upsert_client(
                model_registry, "Loopback", network, GATED_NAS_SECRET, is_enabled=False
            )

    # -- FreeRADIUS's own client -------------------------------------------

    @pytest.mark.skipif(
        shutil.which("radclient") is None, reason="freeradius-utils not installed"
    )
    @pytest.mark.parametrize(
        "password,expected",
        [(TEST_PASSWORD, "Access-Accept"), ("not-the-password", "Access-Reject")],
        ids=["accepted", "rejected"],
    )
    def test_radclient(self, port, user, tmp_path: Path, password, expected):
        """radclient adds a Message-Authenticator and, with ``-b``, refuses a
        reply that lacks one first (its Blast-RADIUS check)."""
        (tmp_path / "dictionary").write_text("# no local attributes\n")
        (tmp_path / "secret").write_text(NAS_SECRET + "\n")
        (tmp_path / "request").write_text(
            f'User-Name = "{user.email}"\nUser-Password = "{password}"\n'
        )
        result = subprocess.run(
            [
                "radclient",
                "-b",
                "-x",
                "-d",
                str(tmp_path),
                "-r",
                "1",
                "-t",
                str(ANSWER_TIMEOUT_SECONDS),
                "-f",
                str(tmp_path / "request"),
                "-S",
                str(tmp_path / "secret"),
                f"{NAS_HOST}:{port}",
                "auth",
            ],
            capture_output=True,
            text=True,
            timeout=RADCLIENT_TIMEOUT_SECONDS,
        )
        assert f"Received {expected}" in result.stdout, result.stdout + result.stderr


class TestWireFormat:
    def test_a_short_datagram_is_malformed(self):
        with pytest.raises(wire.MalformedPacket):
            wire.packet_header(b"\x01\x02")

    def test_a_length_past_the_datagram_is_malformed(self):
        _, raw = access_request("bob", "pw", NAS_SECRET)
        with pytest.raises(wire.MalformedPacket):
            wire.packet_header(raw[:-1])

    def test_padding_past_the_length_is_cut(self):
        _, raw = access_request("bob", "pw", NAS_SECRET)
        assert wire.packet_header(raw + b"\x00\x00")[3] == raw

    def test_two_message_authenticators_are_not_authentic(self):
        request, _ = access_request("bob", "pw", NAS_SECRET)
        raw = request.RequestPacket()
        doubled = raw + bytes([wire.MESSAGE_AUTHENTICATOR, 18]) + bytes(16)
        doubled = doubled[:2] + len(doubled).to_bytes(2, "big") + doubled[4:]
        assert wire.request_is_authentic(raw, NAS_SECRET.encode())
        assert not wire.request_is_authentic(doubled, NAS_SECRET.encode())

    def test_a_password_not_in_blocks_is_malformed(self):
        request, _ = access_request("bob", "pw", NAS_SECRET)
        request["User-Password"] = b"short"
        with pytest.raises(wire.MalformedPacket):
            wire.user_password(request)
