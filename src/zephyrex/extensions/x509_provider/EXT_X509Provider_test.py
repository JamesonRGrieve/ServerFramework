# SPDX-License-Identifier: AGPL-3.0-or-later
"""The x509 provider in a running app: root generates and imports CAs, users
enroll CSRs made with ``cryptography`` through the real routes and
abilities, the certificates are path-validated against the CA the server
publishes, and the published CRL is checked for the serials revoked.

No consumer extension is involved: the relying party here is
``cryptography``'s own client-certificate verifier."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from cryptography.x509.verification import PolicyBuilder, Store
from fastapi import HTTPException

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ExternalErrors import InvalidInputExternalError
from zephyrex.extensions.x509_provider.BLL_X509Provider import (
    X509IssuedCertificateManager,
)
from zephyrex.extensions.x509_provider.CertificateAuthority import Authority
from zephyrex.extensions.x509_provider.EXT_X509Provider import EXT_X509Provider
from zephyrex.extensions.x509_provider.PRV_X509CertificateAuthority import (
    KEY_SETTING,
    AuthorityMissing,
    PRV_X509CertificateAuthority,
)
from zephyrex.lib.Environment import env, refresh_settings
from zephyrex.logic.BLL_Providers import ProviderInstanceSettingModel
from zephyrex.testing.factories import create_user, generate_test_email

# Minted and verified tokens must agree on issuer and audience whichever
# module runs first (see EP_Conversations_test).
os.environ["JWT_AUDIENCE"] = "test-aud"
os.environ["JWT_ISSUER"] = "test-iss"
refresh_settings()

PREFIX = "/v1/x509_provider"
BASE_URL = "https://ca.example.test"


def root_headers() -> Dict[str, str]:
    return {"X-API-Key": env("ROOT_API_KEY")}


def auth(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


def csr_pem(
    key: Optional[Any] = None,
    *,
    common_name: str = "root",
    email: str = "root@evil.test",
) -> str:
    """A CSR claiming to be someone it is not."""
    key = key or ec.generate_private_key(ec.SECP256R1())
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(email)]), critical=False
        )
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )


def leaf_of(issued: Dict[str, Any]) -> x509.Certificate:
    return x509.load_pem_x509_certificate(
        issued["certificate"]["certificate_pem"].encode()
    )


def active_ca(server: Any) -> Dict[str, Any]:
    response = server.get(f"{PREFIX}/ca")
    assert response.status_code == 200, response.text
    active = [a for a in response.json()["authorities"] if a["active"]]
    assert len(active) == 1
    return dict(active[0])


def verify_client(ca_pem: str, leaf: x509.Certificate) -> Any:
    verifier = (
        PolicyBuilder()
        .store(Store([x509.load_pem_x509_certificate(ca_pem.encode())]))
        .time(datetime.now(timezone.utc) + timedelta(seconds=5))
        .build_client_verifier()
    )
    return verifier.verify(leaf, [])


class TestX509Provider(ExtensionServerMixin):
    extension_class = EXT_X509Provider

    @pytest.fixture(autouse=True)
    def generous_rate(self, set_env) -> None:
        """The module issues admin_a more than a day's default; the rate
        limit has its own test."""
        set_env("X509_PROVIDER_MAX_ISSUANCES_PER_DAY", "1000")

    def _issue(self, server, user, csr: Optional[str] = None, **fields: Any) -> Any:
        return server.post(
            f"{PREFIX}/certificate/issue",
            json={"csr_pem": csr or csr_pem(), **fields},
            headers=auth(user),
        )

    @pytest.fixture(scope="module")
    def authority(self, server) -> Dict[str, Any]:
        """A CA root generated through the route."""
        response = server.post(
            f"{PREFIX}/ca/generate",
            json={"name": "client-ca", "common_name": "Test Client CA"},
            headers=root_headers(),
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    @pytest.fixture
    def active(self, server, authority) -> Dict[str, Any]:
        """The CA issuing now, as the server publishes it."""
        return active_ca(server)

    @pytest.fixture
    def published(self, set_env) -> None:
        set_env("X509_PROVIDER_BASE_URL", BASE_URL)

    @pytest.fixture
    def issued(self, server, authority, admin_a, published) -> Dict[str, Any]:
        response = self._issue(server, admin_a)
        assert response.status_code == 200, response.text
        return dict(response.json())

    # -- the CA ----------------------------------------------------------------

    def test_without_a_ca_there_is_nothing_to_sign_with(self, set_env):
        """No instance and no environment CA: the provider says what to set
        (the routes answer 503 with this text)."""
        set_env("X509_PROVIDER_CA_CERTIFICATE", "")
        set_env("X509_PROVIDER_CA_KEY", "")
        with pytest.raises(AuthorityMissing, match="X509_PROVIDER_CA_KEY"):
            PRV_X509CertificateAuthority.authority(None)

    def test_an_environment_ca_is_used_and_checked(self, set_env):
        """The secret store's CA, injected as environment values."""
        ca = Authority.generated(
            "Env CA", "ec-p256", timedelta(days=10), datetime.now(timezone.utc)
        )
        set_env("X509_PROVIDER_CA_CERTIFICATE", ca.certificate_pem)
        set_env("X509_PROVIDER_CA_KEY", ca.key_pem())
        assert PRV_X509CertificateAuthority.authority(None).fingerprint == (
            ca.fingerprint
        )
        other = Authority.generated(
            "Other", "ec-p256", timedelta(days=10), datetime.now(timezone.utc)
        )
        set_env("X509_PROVIDER_CA_KEY", other.key_pem())
        with pytest.raises(AuthorityMissing, match="unusable"):
            PRV_X509CertificateAuthority.authority(None)

    def test_a_generated_ca_is_published_without_its_key(self, server, authority):
        assert "key" not in " ".join(authority).lower()
        assert "PRIVATE KEY" not in str(authority)
        assert authority["active"] and authority["enabled"]
        assert authority["subject_dn"] == "CN=Test Client CA"
        assert (
            active_ca(server)["fingerprint_sha256"] == authority["fingerprint_sha256"]
        )
        der = server.get(f"{PREFIX}/ca/{authority['fingerprint_sha256']}")
        assert der.status_code == 200
        assert der.headers["content-type"] == "application/pkix-cert"
        assert x509.load_der_x509_certificate(der.content) == (
            x509.load_pem_x509_certificate(authority["certificate_pem"].encode())
        )

    def test_the_ca_key_is_stored_encrypted_and_write_only(self, server, authority):
        registry = server.app.state.model_registry
        rows = ProviderInstanceSettingModel.DB(registry.DB.manager.Base).list(
            requester_id=env("ROOT_ID"),
            model_registry=registry,
            return_type="dto",
            override_dto=ProviderInstanceSettingModel,
            provider_instance_id=authority["id"],
            key=KEY_SETTING,
        )
        assert [row.write_only for row in rows] == [True]
        assert str(rows[0].value).startswith("fernet:")
        assert "PRIVATE KEY" not in str(rows[0].value)
        assert rows[0].model_dump()["value"] is None

    def test_only_the_administrator_creates_a_ca(self, server, admin_a):
        body = {"name": "rogue", "common_name": "Rogue CA"}
        refused = server.post(f"{PREFIX}/ca/generate", json=body, headers=auth(admin_a))
        assert refused.status_code == 403
        anonymous = server.post(f"{PREFIX}/ca/generate", json=body)
        assert anonymous.status_code == 401
        imported = Authority.generated(
            "Rogue CA", "ec-p256", timedelta(days=10), datetime.now(timezone.utc)
        )
        refused_import = server.post(
            f"{PREFIX}/ca/import",
            json={
                "name": "rogue",
                "certificate_pem": imported.certificate_pem,
                "key_pem": imported.key_pem(),
            },
            headers=auth(admin_a),
        )
        assert refused_import.status_code == 403

    def test_an_imported_ca_issues_until_rotated_and_keeps_its_crl(
        self, server, authority, admin_a
    ):
        """The newest CA issues; a CA rotated out still signs the CRL for
        what it issued. (The rest of the module asks which CA is active.)"""
        external = Authority.generated(
            "Imported CA", "rsa-3072", timedelta(days=30), datetime.now(timezone.utc)
        )
        response = server.post(
            f"{PREFIX}/ca/import",
            json={
                "name": "imported",
                "certificate_pem": external.certificate_pem,
                "key_pem": external.key_pem(),
            },
            headers=root_headers(),
        )
        assert response.status_code == 200, response.text
        imported = response.json()
        assert imported["fingerprint_sha256"] == external.fingerprint
        assert active_ca(server)["fingerprint_sha256"] == external.fingerprint
        again = server.post(
            f"{PREFIX}/ca/import",
            json={
                "name": "imported again",
                "certificate_pem": external.certificate_pem,
                "key_pem": external.key_pem(),
            },
            headers=root_headers(),
        )
        assert again.status_code == 409

        issued = self._issue(server, admin_a)
        assert issued.status_code == 200, issued.text
        leaf = leaf_of(issued.json())
        leaf.verify_directly_issued_by(external.certificate)
        revoked = server.post(
            f"{PREFIX}/certificate/{issued.json()['certificate']['id']}/revoke",
            json={"reason": "superseded"},
            headers=auth(admin_a),
        )
        assert revoked.status_code == 200, revoked.text

        rotated = server.post(
            f"{PREFIX}/ca/generate",
            json={
                "name": "rotated",
                "common_name": "Rotated CA",
                "key_type": "ec-p256",
            },
            headers=root_headers(),
        )
        assert rotated.status_code == 200, rotated.text
        assert active_ca(server)["fingerprint_sha256"] == (
            rotated.json()["fingerprint_sha256"]
        )
        crl = server.get(f"{PREFIX}/crl/{external.fingerprint}")
        assert crl.status_code == 200
        parsed = x509.load_der_x509_crl(crl.content)
        assert parsed.is_signature_valid(external.key.public_key())
        assert parsed.get_revoked_certificate_by_serial_number(leaf.serial_number)

    def test_importing_a_non_ca_or_a_mismatched_key_is_refused(self, server):
        one = Authority.generated(
            "One", "ec-p256", timedelta(days=10), datetime.now(timezone.utc)
        )
        two = Authority.generated(
            "Two", "ec-p256", timedelta(days=10), datetime.now(timezone.utc)
        )
        response = server.post(
            f"{PREFIX}/ca/import",
            json={
                "name": "mismatched",
                "certificate_pem": one.certificate_pem,
                "key_pem": two.key_pem(),
            },
            headers=root_headers(),
        )
        assert response.status_code == 400
        assert "not the CA certificate's key" in response.text
        assert "PRIVATE KEY" not in response.text

    # -- issuance --------------------------------------------------------------

    def test_the_certificate_names_the_account_not_the_csr(
        self, server, active, admin_a, issued
    ):
        leaf = leaf_of(issued)
        client = verify_client(active["certificate_pem"], leaf)
        assert client.chain[-1] == x509.load_pem_x509_certificate(
            issued["chain_pem"].encode()
        )
        cn = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert cn == (admin_a.username or admin_a.email)
        uid = leaf.subject.get_attributes_for_oid(NameOID.USER_ID)[0].value
        assert uid == admin_a.id
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert san.get_values_for_type(x509.RFC822Name) == [admin_a.email]
        assert "root" not in leaf.subject.rfc4514_string().lower()
        assert "evil" not in str(san)
        eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert list(eku) == [ExtendedKeyUsageOID.CLIENT_AUTH]

    def test_the_record_matches_the_certificate(self, issued, admin_a, active):
        leaf = leaf_of(issued)
        record = issued["certificate"]
        assert record["user_id"] == admin_a.id
        assert record["serial_number"] == format(leaf.serial_number, "x")
        assert record["ca_fingerprint_sha256"] == active["fingerprint_sha256"]
        assert record["is_revoked"] is False
        assert record["subject_dn"] == leaf.subject.rfc4514_string()

    def test_the_lifetime_is_the_policys_and_no_longer(
        self, server, authority, admin_a, set_env
    ):
        set_env("X509_PROVIDER_CERT_LIFETIME_DAYS", "7")
        default = self._issue(server, admin_a)
        assert default.status_code == 200, default.text
        leaf = leaf_of(default.json())
        lifetime = leaf.not_valid_after_utc - leaf.not_valid_before_utc
        assert timedelta(days=7) <= lifetime <= timedelta(days=7, minutes=10)
        shorter = self._issue(server, admin_a, lifetime_days=2)
        assert shorter.status_code == 200
        leaf = leaf_of(shorter.json())
        assert leaf.not_valid_after_utc - leaf.not_valid_before_utc < timedelta(days=3)
        longer = self._issue(server, admin_a, lifetime_days=8)
        assert longer.status_code == 400

    def test_publication_urls_point_at_this_ca(self, issued, active):
        leaf = leaf_of(issued)
        fp = active["fingerprint_sha256"]
        points = leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints)
        assert [n.value for n in points.value[0].full_name] == [
            f"{BASE_URL}{PREFIX}/crl/{fp}"
        ]
        access = leaf.extensions.get_extension_for_class(
            x509.AuthorityInformationAccess
        ).value
        assert [d.access_location.value for d in access] == [
            f"{BASE_URL}{PREFIX}/ca/{fp}"
        ]

    @pytest.mark.parametrize(
        "make_csr, reason",
        [
            (lambda: csr_pem(rsa.generate_private_key(65537, 1024)), "RSA-1024"),
            (lambda: csr_pem(ec.generate_private_key(ec.SECP521R1())), "secp521r1"),
            (
                lambda: "-----BEGIN CERTIFICATE REQUEST-----\nAA==\n-----END CERTIFICATE REQUEST-----",
                "not a PEM",
            ),
        ],
        ids=["rsa-1024", "p-521", "garbage"],
    )
    def test_weak_keys_and_bad_csrs_are_refused(
        self, server, authority, admin_a, make_csr, reason
    ):
        response = self._issue(server, admin_a, make_csr())
        assert response.status_code == 400
        assert reason in response.text

    def test_a_csr_with_a_broken_signature_is_refused(self, server, authority, admin_a):
        csr = x509.load_pem_x509_csr(csr_pem().encode())
        der = bytearray(csr.public_bytes(serialization.Encoding.DER))
        der[-1] ^= 0x01
        forged = x509.load_der_x509_csr(bytes(der)).public_bytes(
            serialization.Encoding.PEM
        )
        response = self._issue(server, admin_a, forged.decode())
        assert response.status_code == 400
        assert "signature does not verify" in response.text

    def test_a_user_cannot_issue_for_someone_else(
        self, server, authority, admin_a, user_b
    ):
        response = self._issue(server, user_b, user_id=admin_a.id)
        assert response.status_code == 200, response.text
        assert response.json()["certificate"]["user_id"] == user_b.id
        uid = leaf_of(response.json()).subject.get_attributes_for_oid(NameOID.USER_ID)
        assert uid[0].value == user_b.id

    def test_root_issues_for_a_named_user_who_then_owns_it(
        self, server, authority, user_b
    ):
        response = server.post(
            f"{PREFIX}/certificate/issue",
            json={"csr_pem": csr_pem(), "user_id": user_b.id},
            headers=root_headers(),
        )
        assert response.status_code == 200, response.text
        record = response.json()["certificate"]
        assert record["user_id"] == user_b.id
        seen = server.get(f"{PREFIX}/certificate/{record['id']}", headers=auth(user_b))
        assert seen.status_code == 200
        for_root = server.post(
            f"{PREFIX}/certificate/issue",
            json={"csr_pem": csr_pem()},
            headers=root_headers(),
        )
        assert for_root.status_code == 400

    def test_issuance_is_rate_limited_per_user(self, server, authority, set_env):
        user = create_user(server, email=generate_test_email("x509_rate"))
        set_env("X509_PROVIDER_MAX_ISSUANCES_PER_DAY", "2")
        assert self._issue(server, user).status_code == 200
        assert self._issue(server, user).status_code == 200
        limited = self._issue(server, user)
        assert limited.status_code == 429, limited.text
        assert limited.headers["retry-after"] == str(24 * 3600)
        other = create_user(server, email=generate_test_email("x509_other"))
        assert self._issue(server, other).status_code == 200

    # -- records ---------------------------------------------------------------

    def test_records_are_their_owners(self, server, issued, admin_a, user_b):
        record_id = issued["certificate"]["id"]
        assert (
            server.get(
                f"{PREFIX}/certificate/{record_id}", headers=auth(admin_a)
            ).status_code
            == 200
        )
        assert (
            server.get(
                f"{PREFIX}/certificate/{record_id}", headers=auth(user_b)
            ).status_code
            == 404
        )
        listed = server.get(f"{PREFIX}/certificate", headers=auth(user_b))
        assert listed.status_code == 200
        ids = [r["id"] for r in listed.json()["x509_issued_certificates"]]
        assert record_id not in ids
        refused = server.post(
            f"{PREFIX}/certificate/{record_id}/revoke",
            json={"reason": "key_compromise"},
            headers=auth(user_b),
        )
        assert refused.status_code == 404

    def test_records_are_never_written_directly(self, server, issued, admin_a):
        record = issued["certificate"]
        made = server.post(
            f"{PREFIX}/certificate",
            json={"x509_issued_certificate": {**record, "id": None}},
            headers=auth(admin_a),
        )
        assert made.status_code in (404, 405)
        changed = server.put(
            f"{PREFIX}/certificate/{record['id']}",
            json={"x509_issued_certificate": {"is_revoked": False}},
            headers=root_headers(),
        )
        assert changed.status_code in (404, 405)
        deleted = server.delete(
            f"{PREFIX}/certificate/{record['id']}", headers=root_headers()
        )
        assert deleted.status_code in (404, 405)
        manager = X509IssuedCertificateManager(
            model_registry=server.app.state.model_registry, requester_id=env("ROOT_ID")
        )
        with pytest.raises(HTTPException) as refused:
            manager.delete(record["id"])
        assert refused.value.status_code == 405
        assert manager.get(id=record["id"]).is_revoked is False

    # -- revocation and the CRL ------------------------------------------------

    def _crl(self, server, fp: str) -> x509.CertificateRevocationList:
        response = server.get(f"{PREFIX}/crl/{fp}")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pkix-crl"
        return x509.load_der_x509_crl(response.content)

    def test_the_owner_revokes_and_the_crl_lists_it(
        self, server, active, admin_a, issued
    ):
        fp = active["fingerprint_sha256"]
        ca = x509.load_pem_x509_certificate(active["certificate_pem"].encode())
        kept = self._issue(server, admin_a).json()
        serial = leaf_of(issued).serial_number
        assert (
            self._crl(server, fp).get_revoked_certificate_by_serial_number(serial)
            is None
        )

        response = server.post(
            f"{PREFIX}/certificate/{issued['certificate']['id']}/revoke",
            json={"reason": "key_compromise"},
            headers=auth(admin_a),
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_revoked"] is True
        assert response.json()["revocation_reason"] == "key_compromise"

        crl = self._crl(server, fp)
        assert crl.is_signature_valid(ca.public_key())
        assert crl.issuer == ca.subject
        entry = crl.get_revoked_certificate_by_serial_number(serial)
        assert entry is not None
        reason = entry.extensions.get_extension_for_class(x509.CRLReason).value.reason
        assert reason == x509.ReasonFlags.key_compromise
        assert (
            crl.get_revoked_certificate_by_serial_number(leaf_of(kept).serial_number)
            is None
        )

        again = server.post(
            f"{PREFIX}/certificate/{issued['certificate']['id']}/revoke",
            json={},
            headers=auth(admin_a),
        )
        assert again.status_code == 409

    def test_the_administrator_revokes_anyones(self, server, active, user_b):
        record = self._issue(server, user_b).json()
        response = server.post(
            f"{PREFIX}/certificate/{record['certificate']['id']}/revoke",
            json={"reason": "privilege_withdrawn"},
            headers=root_headers(),
        )
        assert response.status_code == 200, response.text
        crl = self._crl(server, active["fingerprint_sha256"])
        assert (
            crl.get_revoked_certificate_by_serial_number(leaf_of(record).serial_number)
            is not None
        )

    def test_an_unknown_reason_is_refused(self, server, issued, admin_a):
        response = server.post(
            f"{PREFIX}/certificate/{issued['certificate']['id']}/revoke",
            json={"reason": "certificate_hold"},
            headers=auth(admin_a),
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("fp", ["0" * 64, "not-a-fingerprint", "A" * 64])
    def test_an_unknown_ca_has_no_crl(self, server, authority, fp):
        assert server.get(f"{PREFIX}/crl/{fp}").status_code == 404
        assert server.get(f"{PREFIX}/ca/{fp}").status_code == 404

    # -- abilities -------------------------------------------------------------

    async def test_the_abilities_act_for_the_requester(self, server, authority, user_b):
        issued = await EXT_X509Provider.x509_provider_issue_cert(
            requester_id=user_b.id, csr_pem=csr_pem(common_name="admin")
        )
        assert issued["certificate"]["user_id"] == user_b.id
        listed = await EXT_X509Provider.x509_provider_list_certs(requester_id=user_b.id)
        assert issued["certificate"]["id"] in [r["id"] for r in listed]
        assert all(r["user_id"] == user_b.id for r in listed)
        revoked = await EXT_X509Provider.x509_provider_revoke_cert(
            requester_id=user_b.id,
            certificate_id=issued["certificate"]["id"],
            reason="superseded",
        )
        assert revoked["is_revoked"] is True
        crl = await EXT_X509Provider.x509_provider_crl()
        assert crl["ca_fingerprint"] == active_ca(server)["fingerprint_sha256"]
        parsed = x509.load_pem_x509_crl(crl["crl_pem"].encode())
        serial = int(issued["certificate"]["serial_number"], 16)
        assert parsed.get_revoked_certificate_by_serial_number(serial) is not None

    async def test_abilities_need_a_requester_and_a_known_reason(self, authority):
        with pytest.raises(HTTPException) as missing:
            await EXT_X509Provider.x509_provider_issue_cert(
                requester_id="", csr_pem=csr_pem()
            )
        assert missing.value.status_code == 400
        with pytest.raises(InvalidInputExternalError):
            await EXT_X509Provider.x509_provider_revoke_cert(
                requester_id=str(uuid.uuid4()),
                certificate_id="x",
                reason="certificate_hold",
            )

    def test_the_provider_declares_the_key_secret(self):
        assert PRV_X509CertificateAuthority.instance_setting(KEY_SETTING).secret
