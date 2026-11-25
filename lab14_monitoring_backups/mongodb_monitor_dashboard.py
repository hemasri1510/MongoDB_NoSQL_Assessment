"""Lab 14.1: Prometheus Exporter for MongoDB Operational Telemetry."""
import time
from prometheus_client import Gauge, start_http_server
from pymongo import MongoClient

ACTIVE_CONNS = Gauge("mongodb_active_connections", "Current active client connections")
CACHE_DIRTY_BYTES = Gauge("mongodb_wt_dirty_bytes", "WiredTiger tracked dirty bytes")
OPLOG_WINDOW_HOURS = Gauge("mongodb_oplog_window_hours", "Estimated oplog retention window in hours")


def collect_metrics(client: MongoClient):
    status = client.admin.command("serverStatus")
    ACTIVE_CONNS.set(status["connections"]["current"])
    CACHE_DIRTY_BYTES.set(status["wiredTiger"]["cache"]["tracked dirty bytes in the cache"])

    oplog = client.local.oplog.rs
    first = oplog.find_one(sort=[("$natural", 1)])
    last = oplog.find_one(sort=[("$natural", -1)])
    if first and last:
        window_sec = last["ts"].time - first["ts"].time
        OPLOG_WINDOW_HOURS.set(round(window_sec / 3600.0, 2))


if __name__ == "__main__":
    client = MongoClient("mongodb://localhost:27017")
    start_http_server(9216)
    while True:
        collect_metrics(client)
        time.sleep(10)
