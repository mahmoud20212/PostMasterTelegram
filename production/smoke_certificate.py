"""Generate a test CA/certificate INSIDE an explicitly isolated smoke container."""
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from production.smoke_tasks import require_smoke
from production.tls import signal_reload


def main():
    require_smoke()
    root = Path("/etc/letsencrypt/smoke-ca")
    live = Path("/etc/letsencrypt/live/onepost-ip")
    root.mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if (root / "key.pem").exists():
        ca_key = serialization.load_pem_private_key((root / "key.pem").read_bytes(), password=None)
        ca = x509.load_pem_x509_certificate((root / "ca.pem").read_bytes())
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OnePost isolated smoke CA")])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .sign(ca_key, hashes.SHA256()))
        (root / "key.pem").write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (root / "key.pem").chmod(0o600)
        (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "smoke")]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(os.environ["PUBLIC_IP"]))]), False)
        .sign(ca_key, hashes.SHA256()))
    (live / "privkey.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (live / "privkey.pem").chmod(0o600)
    (live / "fullchain.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    signal_reload()
    # Only the PUBLIC test CA is emitted; private keys stay in the isolated volume.
    print(ca.public_bytes(serialization.Encoding.PEM).decode(), end="")


if __name__ == "__main__":
    main()
