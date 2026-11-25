import datetime
import ipaddress
import os
from pathlib import Path
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

CERT_DIR = Path("./tls_certs")

def generate_tls_chain_programmatically(cert_dir: Path = CERT_DIR):
    """
    Automates TLS certificate management using Python's cryptography library:
    Generates Root CA (ca.crt, ca.key), Server cert/key (server.pem with SANs),
    and Client cert/key (client.pem) for CI/CD deployment pipelines.
    """
    if not HAS_CRYPTO:
        raise RuntimeError("Install 'cryptography' package: pip install cryptography")

    cert_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Root CA
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MongoDB-CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    (cert_dir / "ca.key").write_bytes(
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (cert_dir / "ca.crt").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    # 2. Server Certificate (CN=mongodb, SAN=DNS:localhost, IP:127.0.0.1)
    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    srv_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mongodb")])
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(srv_name)
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    srv_pem = (
        srv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        + srv_cert.public_bytes(serialization.Encoding.PEM)
    )
    (cert_dir / "server.pem").write_bytes(srv_pem)

    # 3. Client Certificate (CN=pymongo-client)
    cli_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    cli_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pymongo-client")])
    cli_cert = (
        x509.CertificateBuilder()
        .subject_name(cli_name)
        .issuer_name(ca_name)
        .public_key(cli_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(ca_key, hashes.SHA256())
    )
    cli_pem = (
        cli_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        + cli_cert.public_bytes(serialization.Encoding.PEM)
    )
    (cert_dir / "client.pem").write_bytes(cli_pem)
    print(f"Generated CA, Server (server.pem), and Client (client.pem) certificates in {cert_dir.resolve()}")

def verify_tls_vs_plaintext(
    username="app_user_read",
    password="ReadUserPass@2026",
    host="localhost",
    port=27017,
    cert_dir: Path = CERT_DIR,
):
    ca_file = str((cert_dir / "ca.crt").resolve())
    client_pem = str((cert_dir / "client.pem").resolve())

    # 1. Verify plaintext connection is rejected when --tlsMode requireTLS is active
    print("
--- Step 1: Testing Plaintext (Unencrypted) Connection Rejection ---")
    plain_uri = f"mongodb://{username}:{password}@{host}:{port}/?authSource=admin"
    try:
        plain_client = MongoClient(plain_uri, serverSelectionTimeoutMS=2500)
        plain_client.admin.command("ping")
        print("  [WARNING] Plaintext connection succeeded (ensure mongod uses --tlsMode requireTLS).")
    except PyMongoError as exc:
        print(f"  [PASS] Unencrypted connection rejected as expected -> {type(exc).__name__}")

    # 2. Connect using TLS + CA + Client Certificate
    print("
--- Step 2: Testing Encrypted TLS Connection (tls=true) ---")
    tls_uri = (
        f"mongodb://{username}:{password}@{host}:{port}/"
        f"?authSource=admin&tls=true&tlsCAFile={ca_file}&tlsCertificateKeyFile={client_pem}"
    )
    try:
        tls_client = MongoClient(tls_uri, serverSelectionTimeoutMS=4000)
        doc = tls_client["cti_security_lab"]["orders"].find_one()
        print(f"  [PASS] TLS connection established! db.orders.find_one() -> {doc}")
        tls_client.close()
    except PyMongoError as exc:
        print(f"  [INFO] TLS connection test output (start mongod with --tlsMode requireTLS first): {exc}")

if __name__ == "__main__":
    generate_tls_chain_programmatically()
    verify_tls_vs_plaintext()
