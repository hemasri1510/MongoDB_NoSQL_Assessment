import argparse
import datetime
import json
import os
import time
import urllib.request
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def send_webhook_alert(webhook_url: str, title: str, message: str, metrics: dict):
    """Activity 14.1: Sends threshold breach alerts to a Slack/Discord webhook URL."""
    if not webhook_url:
        return
    alert_payload = {
        "text": f"*{title}*\n{message}\n```{json.dumps(metrics, indent=2)}```",
        "content": f"**{title}**\n{message}\n```json\n{json.dumps(metrics, indent=2)}\n```",
    }
    try:
        if HAS_REQUESTS:
            resp = requests.post(webhook_url, json=alert_payload, timeout=5)
            resp.raise_for_status()
        else:
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(alert_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
    except Exception as exc:
        print(f"[WEBHOOK ERROR] Failed to deliver alert to {webhook_url}: {exc}")


def collect_metrics(client: MongoClient) -> dict:
    """Retrieves connections, opcounters, WiredTiger cache stats, and replica set status."""
    server_status = client.admin.command("serverStatus")

    # 1. Connections
    conns = server_status.get("connections", {})
    curr_conns = conns.get("current", 0)
    avail_conns = conns.get("available", 0)

    # 2. Opcounters
    ops = server_status.get("opcounters", {})
    op_summary = {
        "insert": ops.get("insert", 0),
        "query": ops.get("query", 0),
        "update": ops.get("update", 0),
        "delete": ops.get("delete", 0),
        "command": ops.get("command", 0),
    }

    # 3. WiredTiger Cache Statistics
    wt_cache = server_status.get("wiredTiger", {}).get("cache", {})
    bytes_in_cache = wt_cache.get("bytes currently in the cache", 0)
    max_bytes = wt_cache.get("maximum bytes configured", 1)
    bytes_read_into = wt_cache.get("bytes read into cache", 0)
    pages_requested = wt_cache.get("pages requested from the cache", 0)
    pages_read_into = wt_cache.get("pages read into cache", 0)

    if pages_requested > 0:
        cache_hit_ratio = max(0.0, (pages_requested - pages_read_into) / pages_requested)
    else:
        total_read = bytes_in_cache + bytes_read_into
        cache_hit_ratio = (bytes_in_cache / total_read) if total_read > 0 else 1.0

    # 4. Replica Set Status & Max Replication Lag
    rs_info = "Standalone / Not Applicable"
    max_lag_sec = 0.0
    try:
        rs_status = client.admin.command("replSetGetStatus")
        members = rs_status.get("members", [])
        primary_optime = None
        for m in members:
            if m.get("stateStr") == "PRIMARY":
                opt = m.get("optimeDate")
                if isinstance(opt, datetime.datetime):
                    primary_optime = opt
                break

        member_summaries = []
        for m in members:
            name = m.get("name", "unknown")
            state = m.get("stateStr", "UNKNOWN")
            lag = 0.0
            if state == "SECONDARY" and primary_optime and isinstance(m.get("optimeDate"), datetime.datetime):
                lag = max(0.0, (primary_optime - m["optimeDate"]).total_seconds())
                max_lag_sec = max(max_lag_sec, lag)
            member_summaries.append(f"{name}={state}(lag:{lag:.1f}s)")
        rs_info = " | ".join(member_summaries)
    except PyMongoError:
        rs_info = "Standalone (no replSetGetStatus)"

    return {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "connections_current": curr_conns,
        "connections_available": avail_conns,
        "opcounters": op_summary,
        "wt_used_mb": round(bytes_in_cache / (1024 * 1024), 2),
        "wt_max_mb": round(max_bytes / (1024 * 1024), 2),
        "cache_hit_ratio": round(cache_hit_ratio, 4),
        "max_replication_lag_sec": round(max_lag_sec, 2),
        "replica_set_status": rs_info,
    }


def format_metrics_table(m: dict) -> str:
    ops = m["opcounters"]
    ops_str = f"ins:{ops['insert']} qry:{ops['query']} upd:{ops['update']} del:{ops['delete']} cmd:{ops['command']}"
    border = "+" + "-" * 28 + "+" + "-" * 64 + "+"
    rows = [
        border,
        f"| {'Timestamp':<26} | {m['timestamp']:<62} |",
        border,
        f"| {'Connections (Curr / Avail)':<26} | {str(m['connections_current']) + ' / ' + str(m['connections_available']):<62} |",
        f"| {'Opcounters':<26} | {ops_str:<62} |",
        f"| {'WiredTiger Cache (MB)':<26} | {str(m['wt_used_mb']) + ' MB / ' + str(m['wt_max_mb']) + ' MB':<62} |",
        f"| {'Cache Hit Ratio':<26} | {m['cache_hit_ratio'] * 100:>6.2f}%{'':<55} |",
        f"| {'Max Replication Lag (s)':<26} | {m['max_replication_lag_sec']:>6.2f} s{'':<54} |",
        f"| {'Replica Set Status':<26} | {m['replica_set_status'][:62]:<62} |",
        border,
    ]
    return "\n".join(rows)


def run_monitoring_loop(uri: str, log_file: str, interval_sec: int, webhook_url: str, once: bool = False):
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    print(f"Connected to MongoDB. Logging metrics every {interval_sec}s to '{log_file}'...")
    try:
        while True:
            metrics = collect_metrics(client)
            table_output = format_metrics_table(metrics)
            print("\n" + table_output)

            warnings = []
            if metrics["cache_hit_ratio"] < 0.80:
                warnings.append(
                    f"WARNING: WiredTiger cache hit ratio dropped to {metrics['cache_hit_ratio']*100:.2f}% (< 80.00% threshold)!"
                )
            if metrics["max_replication_lag_sec"] > 10.0:
                warnings.append(
                    f"WARNING: Replication lag reached {metrics['max_replication_lag_sec']:.2f}s (> 10.00s threshold)!"
                )

            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(table_output + "\n")
                for w in warnings:
                    print(w)
                    lf.write(w + "\n")
                lf.write("\n")

            if warnings and webhook_url:
                send_webhook_alert(
                    webhook_url,
                    title="MongoDB Monitoring Threshold Breach",
                    message="\n".join(warnings),
                    metrics=metrics,
                )

            if once:
                break
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 14.1: MongoDB Monitoring Dashboard & Alerting")
    parser.add_argument("--uri", default="mongodb://localhost:27017/", help="MongoDB connection URI")
    parser.add_argument("--log-file", default="metrics.log", help="Output log file path (default: metrics.log)")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds (default: 30)")
    parser.add_argument("--webhook-url", default=os.environ.get("ALERT_WEBHOOK_URL", ""), help="Slack/Discord webhook URL")
    parser.add_argument("--once", action="store_true", help="Run a single polling cycle and exit")
    args = parser.parse_args()

    run_monitoring_loop(args.uri, args.log_file, args.interval, args.webhook_url, args.once)
