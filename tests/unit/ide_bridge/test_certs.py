import datetime
import ssl
from pathlib import Path
from typing import Final

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from litellm.ide_bridge.certs import (
    BridgeCertificates,
    CertificatePair,
    generate_ca,
    issue_leaf,
    leaf_is_valid,
    load_ca_key,
    load_or_create_ca,
)

NOW: Final = datetime.datetime(2025, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
NEXT_YEAR: Final = NOW + datetime.timedelta(days=400)
IN_TWO_YEARS: Final = NOW + datetime.timedelta(days=800)


def _public_key(cert_pem: bytes) -> rsa.RSAPublicKey:
    cert = x509.load_pem_x509_certificate(cert_pem)
    public_key = cert.public_key()
    assert isinstance(public_key, rsa.RSAPublicKey)
    return public_key


class TestGenerateCa:
    def test_ca_is_self_signed_with_ca_constraint(self):
        ca = generate_ca(NOW)

        cert = x509.load_pem_x509_certificate(ca.cert_pem)
        assert cert.subject == cert.issuer
        basic_constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert basic_constraints.value.ca is True

    def test_ca_key_is_loadable_rsa_private_key(self):
        ca = generate_ca(NOW)

        key = serialization.load_pem_private_key(ca.key_pem, password=None)
        assert isinstance(key, rsa.RSAPrivateKey)


class TestIssueLeaf:
    def test_leaf_carries_hostname_san_and_is_signed_by_the_ca(self):
        ca = generate_ca(NOW)

        leaf = issue_leaf(_ca_key(ca), "api.anthropic.com", NOW)

        cert = x509.load_pem_x509_certificate(leaf.cert_pem)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        assert san.value.get_values_for_type(x509.DNSName) == ["api.anthropic.com"]
        ca_cert = x509.load_pem_x509_certificate(ca.cert_pem)
        ca_public_key = ca_cert.public_key()
        assert isinstance(ca_public_key, rsa.RSAPublicKey)
        ca_public_key.verify(cert.signature, cert.tbs_certificate_bytes, asym_padding.PKCS1v15(), hashes.SHA256())
        extended_usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert ExtendedKeyUsageOID.SERVER_AUTH in extended_usage.value

    def test_leaf_validity_follows_the_passed_clock(self):
        ca = generate_ca(NOW)

        leaf = issue_leaf(_ca_key(ca), "api.anthropic.com", NOW)

        cert = x509.load_pem_x509_certificate(leaf.cert_pem)
        assert cert.not_valid_before_utc == NOW
        assert cert.not_valid_after_utc == NEXT_YEAR - datetime.timedelta(days=35)


def _ca_key(ca: CertificatePair) -> rsa.RSAPrivateKey:
    return load_ca_key(ca.key_pem)


class TestLeafIsValid:
    def test_fresh_leaf_is_valid(self):
        ca = generate_ca(NOW)
        leaf = issue_leaf(_ca_key(ca), "api.anthropic.com", NOW)

        assert leaf_is_valid(leaf.cert_pem, NOW + datetime.timedelta(days=10))

    def test_leaf_past_the_renewal_margin_is_reissued(self):
        ca = generate_ca(NOW)
        leaf = issue_leaf(_ca_key(ca), "api.anthropic.com", NOW)

        assert not leaf_is_valid(leaf.cert_pem, NEXT_YEAR - datetime.timedelta(days=1))

    def test_garbage_pem_is_invalid(self):
        assert not leaf_is_valid(b"not a pem", NOW)


class TestBridgeCertificates:
    def test_first_request_writes_leaf_then_reuses_the_same_files(self, tmp_path: Path):
        ca = generate_ca(NOW)
        certificates = BridgeCertificates(ca.cert_pem, _ca_key(ca), tmp_path / "leaves")

        first = certificates.leaf_paths("api.anthropic.com", NOW)
        second = certificates.leaf_paths("api.anthropic.com", NOW + datetime.timedelta(days=10))

        assert first == second
        assert first.cert.exists() and first.key.exists()

    def test_expired_leaf_is_reissued_in_place(self, tmp_path: Path):
        ca = generate_ca(NOW)
        certificates = BridgeCertificates(ca.cert_pem, _ca_key(ca), tmp_path / "leaves")

        stale = certificates.leaf_paths("api.anthropic.com", NOW)
        fresh = certificates.leaf_paths("api.anthropic.com", IN_TWO_YEARS)

        assert stale == fresh
        reissued = x509.load_pem_x509_certificate(fresh.cert.read_bytes())
        assert reissued.not_valid_before_utc >= IN_TWO_YEARS

    def test_hostile_hostname_cannot_escape_the_leaves_dir(self, tmp_path: Path):
        ca = generate_ca(NOW)
        certificates = BridgeCertificates(ca.cert_pem, _ca_key(ca), tmp_path / "leaves")

        paths = certificates.leaf_paths("../../etc/passwd", NOW)

        assert paths.cert.parent == tmp_path / "leaves"

    def test_ssl_context_for_serves_a_leaf_for_the_host(self, tmp_path: Path):
        ca = generate_ca(NOW)
        certificates = BridgeCertificates(ca.cert_pem, _ca_key(ca), tmp_path / "leaves")

        context = certificates.ssl_context_for("api.anthropic.com")

        assert isinstance(context, ssl.SSLContext)

    def test_base_server_context_installs_an_sni_callback(self, tmp_path: Path):
        ca = generate_ca(NOW)
        certificates = BridgeCertificates(ca.cert_pem, _ca_key(ca), tmp_path / "leaves")

        context = certificates.base_server_context("api.anthropic.com")

        assert context.sni_callback is not None


class TestLoadOrCreateCa:
    def test_creates_then_reuses_persisted_ca(self, tmp_path: Path):
        cert_path = tmp_path / "ca.crt"
        key_path = tmp_path / "ca.key"

        first = load_or_create_ca(cert_path, key_path, NOW)
        second = load_or_create_ca(cert_path, key_path, NEXT_YEAR)

        assert first == second
        assert cert_path.exists() and key_path.exists()


class TestLoadCaKey:
    def test_rejects_non_rsa_key_material(self):
        ec_key_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

        with pytest.raises(ValueError, match="not an RSA key"):
            load_ca_key(ec_key_pem)
