# SPDX-License-Identifier: AGPL-3.0-or-later
"""The RADIUS client against a real FreeRADIUS (UDP and RadSec), and against
a bare UDP responder for the replies no correct server sends."""

import hashlib
import hmac
import socket
import tempfile
from pathlib import Path
from typing import Iterator, Optional, Tuple

import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    TransientExternalError,
)
from zephyrex.extensions.radius_consumer.RADIUSClient import (
    RADIUS_PORT,
    RADSEC_SECRET,
    Endpoint,
    RADIUSClient,
    ServiceSettings,
    TLSSettings,
    build_access_request,
)
from zephyrex.extensions.radius_consumer.RADIUSTestServers import (
    LOOPBACK,
    OTP_CODE,
    OTP_FIRST_FACTOR,
    OTP_PROMPT,
    OTP_USER,
    Certificates,
    ForgeryThenRelay,
    FreeRADIUS,
    HandCraftedResponder,
    accept_with_forged_message_authenticator,
    accept_without_message_authenticator,
    free_port,
    freeradius_available,
    issue_certificates,
    silent_udp_port,
)

SECRET = "a-long-shared-secret-for-the-tests-7f3a"
USERS = {"alice": "wonderland", "bob": "builder"}
TIMEOUT_SECONDS = 2.0

pytestmark = pytest.mark.skipif(
    not freeradius_available(), reason="FreeRADIUS is not installed"
)


@pytest.fixture(scope="module")
def certificates() -> Certificates:
    return issue_certificates()


@pytest.fixture(scope="module")
def freeradius(tmp_path_factory, certificates) -> Iterator[FreeRADIUS]:
    with FreeRADIUS(
        tmp_path_factory.mktemp("freeradius"), SECRET, USERS, certificates
    ) as server:
        yield server


def udp_service(*ports: int, secret: str = SECRET, retries: int = 0) -> ServiceSettings:
    return ServiceSettings(
        endpoints=tuple(Endpoint(LOOPBACK, port) for port in ports),
        transport="udp",
        secret=secret.encode(),
        timeout_seconds=TIMEOUT_SECONDS,
        retries=retries,
        nas_identifier="zephyrex-test",
    )


def radsec_service(
    port: int, certificates: Certificates, server_name: Optional[str] = None
) -> ServiceSettings:
    return ServiceSettings(
        endpoints=(Endpoint(LOOPBACK, port),),
        transport="radsec",
        secret=RADSEC_SECRET.encode(),
        timeout_seconds=TIMEOUT_SECONDS,
        retries=0,
        tls=TLSSettings(
            ca_pem=certificates.ca_pem,
            client_cert_pem=certificates.client_cert_pem,
            client_key_pem=certificates.client_key_pem,
            server_name=server_name,
        ),
    )


def _attributes(raw: bytes) -> Iterator[Tuple[int, int, bytes]]:
    offset = 20
    while offset < len(raw):
        kind, length = raw[offset], raw[offset + 1]
        yield offset, kind, raw[offset + 2 : offset + length]
        offset += length


class TestTheRequest:
    def test_message_authenticator_comes_first_and_verifies(self):
        request = build_access_request(SECRET.encode(), "alice", "wonderland")
        raw = request.raw
        first_offset, first_kind, first_value = next(_attributes(raw))
        assert first_kind == 80 and len(first_value) == 16
        zeroed = raw[: first_offset + 2] + bytes(16) + raw[first_offset + 18 :]
        expected = hmac.new(SECRET.encode(), zeroed, hashlib.md5).digest()
        assert hmac.compare_digest(expected, first_value)

    def test_the_password_is_hidden_as_rfc_2865_says(self):
        password = "wonderland"
        request = build_access_request(SECRET.encode(), "alice", password)
        assert password.encode() not in request.raw
        hidden = next(v for _, k, v in _attributes(request.raw) if k == 2)
        # RFC 2865 5.2: c(1) = p(1) xor MD5(S + RA), padded to 16 octets.
        pad = hashlib.md5(SECRET.encode() + request.authenticator).digest()
        revealed = bytes(a ^ b for a, b in zip(hidden, pad)).rstrip(b"\x00")
        assert revealed == password.encode()

    def test_each_request_has_a_fresh_authenticator(self):
        one = build_access_request(SECRET.encode(), "alice", "x")
        two = build_access_request(SECRET.encode(), "alice", "x")
        assert one.authenticator != two.authenticator

    @pytest.mark.parametrize(
        "username,password",
        [
            ("", "x"),
            ("a" * 254, "x"),
            ("alice", ""),
            ("alice", "p" * 129),
            ("a\x00", "x"),
        ],
    )
    def test_out_of_range_credentials_are_refused(self, username, password):
        with pytest.raises(InvalidInputExternalError):
            build_access_request(SECRET.encode(), username, password)


class TestEndpoints:
    @pytest.mark.parametrize(
        "entry,expected",
        [
            ("radius.example.com", Endpoint("radius.example.com", 1812)),
            ("10.0.0.5:18120", Endpoint("10.0.0.5", 18120)),
            ("[2001:db8::1]:2083", Endpoint("2001:db8::1", 2083)),
            ("2001:db8::1", Endpoint("2001:db8::1", 1812)),
        ],
    )
    def test_entries_parse(self, entry, expected):
        assert Endpoint.parse(entry, RADIUS_PORT) == expected

    @pytest.mark.parametrize(
        "entry", ["", "host:0", "host:70000", "host:port", "[::1", "bad host"]
    )
    def test_bad_entries_are_refused(self, entry):
        with pytest.raises(InvalidInputExternalError):
            Endpoint.parse(entry, RADIUS_PORT)


class TestOverUDP:
    def test_the_right_password_is_accepted(self, freeradius):
        reply = RADIUSClient(udp_service(freeradius.udp_port)).authenticate(
            "alice", "wonderland"
        )
        assert reply.outcome == "accept"
        assert reply.reply_message == "Welcome, alice"
        assert reply.endpoint_index == 0
        assert freeradius.received('NAS-Identifier = "zephyrex-test"')

    def test_a_wrong_password_is_rejected(self, freeradius):
        reply = RADIUSClient(udp_service(freeradius.udp_port)).authenticate(
            "alice", "not-it"
        )
        assert reply.outcome == "reject"

    def test_an_unknown_user_is_rejected(self, freeradius):
        reply = RADIUSClient(udp_service(freeradius.udp_port)).authenticate(
            "mallory", "anything"
        )
        assert reply.outcome == "reject"

    def test_freeradius_drops_a_request_signed_with_another_secret(self, freeradius):
        """FreeRADIUS checks the request's Message-Authenticator and drops
        the request: no answer, so the sign-in fails as unreachable."""
        client = RADIUSClient(udp_service(freeradius.udp_port, secret="wrong" * 8))
        with pytest.raises(TransientExternalError):
            client.authenticate("alice", "wonderland")
        assert freeradius.received("invalid Message-Authenticator")

    def test_a_reply_signed_with_another_secret_is_refused(self):
        """The server signs with its secret, which is not ours: the
        Response Authenticator does not verify."""

        def other_secret(request: bytes, _: bytes) -> bytes:
            return accept_without_message_authenticator(request, b"not-our-secret")

        with HandCraftedResponder(SECRET.encode(), other_secret) as server:
            client = RADIUSClient(udp_service(server.port))
            with pytest.raises(AuthExternalError) as refused:
                client.authenticate("alice", "wonderland")
        assert "Response Authenticator" in str(refused.value)

    def test_a_reply_without_message_authenticator_is_refused(self):
        """Blast-RADIUS: a reply's Response Authenticator alone (MD5) can be
        forged, so an Accept without Message-Authenticator signs no one in,
        even when the Response Authenticator is right."""
        with HandCraftedResponder(
            SECRET.encode(), accept_without_message_authenticator
        ) as server:
            client = RADIUSClient(udp_service(server.port))
            with pytest.raises(AuthExternalError) as refused:
                client.authenticate("alice", "wonderland")
            assert len(server.requests) == 1
        assert "without Message-Authenticator" in str(refused.value)

    def test_a_forged_message_authenticator_is_refused(self):
        with HandCraftedResponder(
            SECRET.encode(), accept_with_forged_message_authenticator
        ) as server:
            client = RADIUSClient(udp_service(server.port))
            with pytest.raises(AuthExternalError) as refused:
                client.authenticate("alice", "wonderland")
        assert "Message-Authenticator that does not verify" in str(refused.value)

    def test_a_server_that_times_out_fails_over_to_the_next(self, freeradius):
        with silent_udp_port() as silent:
            client = RADIUSClient(udp_service(silent, freeradius.udp_port))
            reply = client.authenticate("alice", "wonderland")
        assert (reply.outcome, reply.endpoint_index) == ("accept", 1)

    def test_a_timeout_is_retransmitted_before_failing_over(self):
        with silent_udp_port() as silent:
            client = RADIUSClient(udp_service(silent, retries=1))
            with pytest.raises(TransientExternalError) as unanswered:
                client.authenticate("alice", "wonderland")
        assert "timed out" in str(unanswered.value)

    def test_a_refused_port_fails_over_to_the_next(self, freeradius):
        closed = free_port(socket.SOCK_DGRAM)
        client = RADIUSClient(udp_service(closed, freeradius.udp_port))
        reply = client.authenticate("bob", "builder")
        assert (reply.outcome, reply.endpoint_index) == ("accept", 1)

    def test_a_forged_reply_does_not_stop_the_wait_for_the_real_one(self, freeradius):
        """A forged Accept arrives first, then FreeRADIUS's real Reject: the
        forgery is discarded and the real reply counts."""
        with ForgeryThenRelay(SECRET.encode(), freeradius.udp_port) as responder:
            reply = RADIUSClient(udp_service(responder.port)).authenticate(
                "alice", "not-it"
            )
        assert reply.outcome == "reject"


class TestChallenge:
    def test_a_challenge_then_its_answer_signs_in(self, freeradius):
        client = RADIUSClient(udp_service(freeradius.udp_port))
        challenge = client.authenticate(OTP_USER, OTP_FIRST_FACTOR)
        assert challenge.outcome == "challenge"
        assert challenge.reply_message == OTP_PROMPT
        assert challenge.state
        answer = client.authenticate(
            OTP_USER,
            OTP_CODE,
            state=challenge.state,
            endpoint_index=challenge.endpoint_index,
        )
        assert answer.outcome == "accept"

    def test_a_wrong_code_is_rejected(self, freeradius):
        client = RADIUSClient(udp_service(freeradius.udp_port))
        challenge = client.authenticate(OTP_USER, OTP_FIRST_FACTOR)
        answer = client.authenticate(
            OTP_USER, "000000", state=challenge.state, endpoint_index=0
        )
        assert answer.outcome == "reject"

    def test_the_answer_goes_only_to_the_server_that_asked(self, freeradius):
        """The State belongs to the server that issued it; another one
        (here, the silent first) never sees the answer."""
        with silent_udp_port() as silent:
            client = RADIUSClient(udp_service(silent, freeradius.udp_port))
            challenge = client.authenticate(OTP_USER, OTP_FIRST_FACTOR)
            assert challenge.endpoint_index == 1
            answer = client.authenticate(
                OTP_USER, OTP_CODE, state=challenge.state, endpoint_index=1
            )
        assert answer.outcome == "accept"


class TestOverTLS:
    def test_the_right_password_is_accepted(self, freeradius, certificates):
        client = RADIUSClient(radsec_service(freeradius.radsec_port, certificates))
        reply = client.authenticate("alice", "wonderland")
        assert reply.outcome == "accept"
        assert reply.reply_message == "Welcome, alice"

    def test_a_wrong_password_is_rejected(self, freeradius, certificates):
        client = RADIUSClient(radsec_service(freeradius.radsec_port, certificates))
        assert client.authenticate("alice", "nope").outcome == "reject"

    def test_a_challenge_works_over_tls(self, freeradius, certificates):
        client = RADIUSClient(radsec_service(freeradius.radsec_port, certificates))
        challenge = client.authenticate(OTP_USER, OTP_FIRST_FACTOR)
        assert challenge.outcome == "challenge"
        answer = client.authenticate(
            OTP_USER, OTP_CODE, state=challenge.state, endpoint_index=0
        )
        assert answer.outcome == "accept"

    def test_a_server_certificate_from_another_ca_is_refused(
        self, freeradius, certificates
    ):
        """We trust another CA: the server's certificate does not verify,
        and nothing is sent."""
        stranger = issue_certificates("another-ca")
        mixed = Certificates(
            ca_pem=stranger.ca_pem,
            server_cert_pem=certificates.server_cert_pem,
            server_key_pem=certificates.server_key_pem,
            client_cert_pem=certificates.client_cert_pem,
            client_key_pem=certificates.client_key_pem,
        )
        client = RADIUSClient(radsec_service(freeradius.radsec_port, mixed))
        with pytest.raises(AuthExternalError) as refused:
            client.authenticate("alice", "wonderland")
        assert "certificate" in str(refused.value)

    def test_a_server_certificate_for_another_name_is_refused(
        self, freeradius, certificates
    ):
        client = RADIUSClient(
            radsec_service(
                freeradius.radsec_port, certificates, server_name="radius.example"
            )
        )
        with pytest.raises(AuthExternalError):
            client.authenticate("alice", "wonderland")

    def test_a_client_certificate_the_server_does_not_trust_is_refused(
        self, freeradius, certificates
    ):
        """Mutual authentication: our certificate is from a CA the server
        does not trust, so it ends the handshake."""
        stranger = issue_certificates("another-ca")
        untrusted_client = Certificates(
            ca_pem=certificates.ca_pem,
            server_cert_pem=certificates.server_cert_pem,
            server_key_pem=certificates.server_key_pem,
            client_cert_pem=stranger.client_cert_pem,
            client_key_pem=stranger.client_key_pem,
        )
        client = RADIUSClient(radsec_service(freeradius.radsec_port, untrusted_client))
        with pytest.raises(AuthExternalError):
            client.authenticate("alice", "wonderland")

    def test_unloadable_certificates_are_refused(self, certificates):
        broken = TLSSettings(
            ca_pem=certificates.ca_pem,
            client_cert_pem="not a certificate",
            client_key_pem=certificates.client_key_pem,
        )
        with pytest.raises(InvalidInputExternalError):
            broken.check()


def test_no_key_file_outlives_loading_the_certificates(certificates):
    """The client's key is written for the TLS library, then removed."""
    before = set(Path(tempfile.gettempdir()).glob("radsec-*"))
    TLSSettings(
        ca_pem=certificates.ca_pem,
        client_cert_pem=certificates.client_cert_pem,
        client_key_pem=certificates.client_key_pem,
    ).check()
    assert set(Path(tempfile.gettempdir()).glob("radsec-*")) == before
