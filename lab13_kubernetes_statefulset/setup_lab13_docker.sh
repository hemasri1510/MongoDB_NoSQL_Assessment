#!/usr/bin/env bash
# ============================================================================
# LAB 13.1: Generate Keyfile, Start Production Replica Set & Initiate rs0
# ============================================================================
set -e

echo "=== 1. Generating 756-byte Base64 Replica Set Keyfile ==="
openssl rand -base64 756 > keyfile
chmod 400 keyfile
# Ensure UID 999 (mongodb user inside official mongo image) can read keyfile on Linux
chown 999:999 keyfile 2>/dev/null || true

echo "=== 2. Starting 3-Node Replica Set + Prometheus Exporter Sidecar ==="
docker-compose -f docker-compose.lab13.yml up -d
sleep 6

echo "=== 3. Initiating Replica Set rs0 ==="
docker exec -it prod-mongo1 mongosh -u admin -p ProdAdminPassword@2026 --authenticationDatabase admin --eval '
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27017", priority: 2 },
    { _id: 1, host: "mongo2:27017", priority: 1 },
    { _id: 2, host: "mongo3:27017", priority: 1 }
  ]
});
'
sleep 5
docker exec -it prod-mongo1 mongosh -u admin -p ProdAdminPassword@2026 --authenticationDatabase admin --eval 'rs.status().members.map(m => ({name: m.name, stateStr: m.stateStr, health: m.health}))'
echo ""
echo "Prometheus metrics endpoint ready at: http://localhost:9216/metrics"
