"""Local CA and per-hostname leaf certificates for the IDE bridge.

TLS-terminating interception needs a certificate the captured client trusts,
issued for whatever hostname it connects to. A local root CA is installed into
the OS trust store once; a leaf certificate is then minted per SNI hostname on
first contact and cached on disk. The CA key never leaves the data directory.
"""

import datetime
import hashlib
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_SUBJECT: Final[str] = "LiteLLM IDE Bridge CA"
CA_VALIDITY_DAYS: Final[int] = 3650
LEAF_VALIDITY_DAYS: Final[int] = 365
LEAF_RENEWAL_MARGIN_DAYS: Final[int] = 7
KEY_SIZE: Final[int] = 2048


@dataclass(frozen=True, slots=True)
class CertificatePair:
    cert_pem: bytes
    key_pem: bytes


@dataclass(frozen=True, slots=True)
class LeafPaths:
    cert: Path
    key: Path


def _key_usage(ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not ca,
        content_commitment=False,
        key_encipherment=not ca,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def generate_ca(now: datetime.datetime) -> CertificatePair:
    key: Final = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    name: Final = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_SUBJECT)])
    certificate: Final = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .sign(key, hashes.SHA256())
    )
    return CertificatePair(
        cert_pem=certificate.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def load_ca_key(key_pem: bytes) -> rsa.RSAPrivateKey:
    loaded: Final = serialization.load_pem_private_key(key_pem, password=None)
    if isinstance(loaded, rsa.RSAPrivateKey):
        return loaded
    raise ValueError("bridge CA key on disk is not an RSA key")


def issue_leaf(ca_key: rsa.RSAPrivateKey, hostname: str, now: datetime.datetime) -> CertificatePair:
    key: Final = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    certificate: Final = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_SUBJECT)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=LEAF_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return CertificatePair(
        cert_pem=certificate.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def leaf_is_valid(cert_pem: bytes, now: datetime.datetime) -> bool:
    try:
        certificate: Final = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        return False
    return (
        certificate.not_valid_before_utc
        <= now
        < certificate.not_valid_after_utc - datetime.timedelta(days=LEAF_RENEWAL_MARGIN_DAYS)
    )


class BridgeCertificates:
    """CA material plus an on-disk leaf cache keyed by hostname.

    Leaf files are named by a hash of the hostname so hostile SNI values can
    never escape the data directory through a path traversal.
    """

    def __init__(self, ca_cert_pem: bytes, ca_key: rsa.RSAPrivateKey, leaves_dir: Path) -> None:
        self.ca_cert_pem = ca_cert_pem
        self.ca_key = ca_key
        self.leaves_dir = leaves_dir

    def leaf_paths(self, hostname: str, now: datetime.datetime) -> LeafPaths:
        digest: Final = hashlib.sha256(hostname.encode()).hexdigest()[:16]
        paths: Final[LeafPaths] = LeafPaths(
            cert=self.leaves_dir / f"{digest}.crt",
            key=self.leaves_dir / f"{digest}.key",
        )
        if paths.cert.exists() and paths.key.exists():
            cert_pem: Final = paths.cert.read_bytes()
            if leaf_is_valid(cert_pem, now):
                return paths
        return self._write_leaf(hostname, paths, now)

    def _write_leaf(self, hostname: str, paths: LeafPaths, now: datetime.datetime) -> LeafPaths:
        leaf: Final[CertificatePair] = issue_leaf(self.ca_key, hostname, now)
        self.leaves_dir.mkdir(parents=True, exist_ok=True)
        paths.cert.write_bytes(leaf.cert_pem)
        paths.key.write_bytes(leaf.key_pem)
        paths.key.chmod(0o600)
        return paths

    def ssl_context_for(self, hostname: str) -> ssl.SSLContext:
        paths: Final[LeafPaths] = self.leaf_paths(hostname, datetime.datetime.now(datetime.UTC))
        context: Final = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(paths.cert), keyfile=str(paths.key))
        return context

    def base_server_context(self, default_hostname: str) -> ssl.SSLContext:
        """Server context whose SNI callback swaps in the per-host leaf context.

        The base context carries a default leaf so SNI-less clients still
        complete a handshake instead of hanging until timeout.
        """
        context: Final = self.ssl_context_for(default_hostname)

        def on_sni(sslobj: ssl.SSLObject, server_name: str, _initial: ssl.SSLContext) -> int | None:
            sslobj.context = self.ssl_context_for(server_name)  # rebind-ok: ssl SNI swap is in-place by API
            return None

        context.sni_callback = on_sni
        return context


def load_or_create_ca(ca_cert_path: Path, ca_key_path: Path, now: datetime.datetime) -> CertificatePair:
    if ca_cert_path.exists() and ca_key_path.exists():
        return CertificatePair(cert_pem=ca_cert_path.read_bytes(), key_pem=ca_key_path.read_bytes())
    ca: Final[CertificatePair] = generate_ca(now)
    ca_cert_path.parent.mkdir(parents=True, exist_ok=True)
    ca_cert_path.write_bytes(ca.cert_pem)
    ca_key_path.write_bytes(ca.key_pem)
    ca_key_path.chmod(0o600)
    return ca
