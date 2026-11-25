#!/usr/bin/env bash
# Lab 9.2: Automated Docker Network Partition Experiment
# Prerequisites: 3-node replica set (mongo1, mongo2, mongo3) running from docker-compose.yml

set -e

# Detect the Docker bridge network attached to mongo1
NET_NAME=$(docker inspect mongo1 -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
echo "Detected Replica Set Docker Network: $NET_NAME"

echo "=== [T+0s] Baseline: Checking containers ==="
docker ps --filter "name=mongo" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"

echo ""
echo "=== [T+2s] Phase 1: Disconnecting 1 Secondary (mongo3) ==="
docker network disconnect "$NET_NAME" mongo3
echo "mongo3 disconnected! Majority (mongo1 + mongo2 = 2/3) still intact."
echo "-> Both w:1 and w:majority inserts will SUCCEED."
sleep 15

echo ""
echo "=== [T+17s] Phase 2: Disconnecting 2nd Secondary (mongo2) ==="
docker network disconnect "$NET_NAME" mongo2
echo "mongo2 disconnected! Only mongo1 remains isolated (1/3 votes -> NO MAJORITY)."
echo "-> w:majority will TIMEOUT; mongo1 steps down to SECONDARY (read-only) after heartbeat loss (~10s)."
sleep 15

echo ""
echo "=== [T+32s] Phase 3: Healing Partition (Reconnecting mongo2 and mongo3) ==="
docker network connect "$NET_NAME" mongo2
docker network connect "$NET_NAME" mongo3
echo "Both secondaries reconnected! Election & oplog catch-up in progress..."
sleep 8

echo ""
echo "=== [T+40s] Final Cluster Status ==="
docker exec mongo1 mongosh --quiet --port 27017 --eval 'rs.status().members.map(m => ({name: m.name, state: m.stateStr, health: m.health}))'
