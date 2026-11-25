#!/usr/bin/env bash
# Lab 12: Generate Self-Signed Root CA and Server PEM Certificate for MongoDB TLS
set -euo pipefail

mkdir -p certs && cd certs
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 365 -out ca.pem -subj "/CN=MongoDB-Lab-CA"

openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=localhost"
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out server.crt -days 365 -sha256

cat server.crt server.key > mongodb.pem
echo "Generated certs/ca.pem and certs/mongodb.pem"
