#!/usr/bin/env bash
# ============================================================================
# LAB 12.2: Generate Self-Signed CA, Server & Client TLS Certificates (OpenSSL)
# ============================================================================
set -e

CERT_DIR="./tls_certs"
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

echo "=== 1. Generating Root CA Key & Self-Signed Certificate (CN=MongoDB-CA) ==="
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt -subj "/CN=MongoDB-CA"

echo "=== 2. Generating Server Key, CSR & CA-Signed Server Certificate (SAN: localhost, 127.0.0.1) ==="
openssl genrsa -out server.key 4096
openssl req -new -key server.key -out server.csr -subj "/CN=mongodb"
openssl x509 -req -days 365 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial   -out server.crt -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1")

# Combine server key and certificate into server.pem for mongod --tlsCertificateKeyFile
cat server.key server.crt > server.pem

echo "=== 3. Generating Client Key, CSR & CA-Signed Client Certificate (for PyMongo mTLS) ==="
openssl genrsa -out client.key 4096
openssl req -new -key client.key -out client.csr -subj "/CN=pymongo-client"
openssl x509 -req -days 365 -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial   -out client.crt

cat client.key client.crt > client.pem
chmod 600 ca.key server.key server.pem client.key client.pem

echo ""
echo "Certificates generated in $(pwd):"
ls -lh ca.crt server.pem client.pem
echo ""
echo "Start MongoDB with TLS enabled:"
echo "  mongod --auth --tlsMode requireTLS --tlsCertificateKeyFile $(pwd)/server.pem --tlsCAFile $(pwd)/ca.crt --dbpath /data/db --bind_ip_all --port 27017"
echo ""
echo "Verify unencrypted connection is rejected:"
echo "  mongosh --host localhost --port 27017   # Fails with SSL/network error"
