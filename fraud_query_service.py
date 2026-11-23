"""
Lab 16.2: Build a Hybrid Query Service (FraudQueryService)
Combines:
- MongoDB (PyMongo MongoClient with maxPoolSize=10) for flexible transaction lookups (`get_transaction`)
- Cassandra (cassandra.cluster.Cluster session with executor_threads) for time-ordered events (`get_user_profile`)
- Neo4j (neo4j.GraphDatabase.driver with max_connection_pool_size) for multi-hop graph traversal (`detect_fraud_rings`)
- Per-backend Circuit Breaker: opens after 3 consecutive failures, skips calls while open, retries after 30 seconds.

Latency Benchmarks (Reference Documentation):
- MongoDB lookups (get_transaction): ~2 to 5 ms
- Cassandra time-ordered queries (get_user_profile): ~3 to 8 ms
- Neo4j multi-hop graph traversals (detect_fraud_rings): ~10 ms (1 hop) up to ~500 ms (3 hops) depending on graph density
"""

import time
from typing import Any, Callable, Dict, List, Optional

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover
    MongoClient = None  # type: ignore

try:
    from cassandra.cluster import Cluster
except ImportError:  # pragma: no cover
    Cluster = None  # type: ignore

try:
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover
    GraphDatabase = None  # type: ignore


LATENCY_DOCS = {
    "mongodb_get_transaction_ms": "2 to 5 ms (indexed lookup on txn_id)",
    "cassandra_get_user_profile_ms": "3 to 8 ms (single-partition range scan ordered by event_time DESC LIMIT 10)",
    "neo4j_detect_fraud_rings_ms": "10 ms (1 hop) to 500 ms (3 hops) depending on graph density & fan-out",
}


class CircuitBreaker:
    """Simple per-backend circuit breaker opening after `failure_threshold` consecutive failures."""

    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout_sec: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.consecutive_failures = 0
        self.state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self.opened_at: Optional[float] = None

    def allow_request(self, now: Optional[float] = None) -> bool:
        current_time = time.monotonic() if now is None else now
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if self.opened_at is not None and (current_time - self.opened_at) >= self.recovery_timeout_sec:
                self.state = "HALF_OPEN"
                return True
            return False
        # HALF_OPEN allows a trial request
        return True

    def record_success(self):
        self.consecutive_failures = 0
        self.state = "CLOSED"
        self.opened_at = None

    def record_failure(self, now: Optional[float] = None):
        current_time = time.monotonic() if now is None else now
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold or self.state == "HALF_OPEN":
            self.state = "OPEN"
            self.opened_at = current_time


class FraudQueryService:
    """Polyglot persistence query service across MongoDB, Cassandra, and Neo4j."""

    def __init__(
        self,
        mongo_uri: str = "mongodb://localhost:27017/?maxPoolSize=10",
        mongo_db: str = "fraud_detection",
        cassandra_hosts: Optional[List[str]] = None,
        cassandra_keyspace: str = "fraud_events",
        cassandra_executor_threads: int = 4,
        neo4j_uri: str = "bolt://localhost:7687",
        neo4j_auth: tuple = ("neo4j", "password"),
        neo4j_max_pool_size: int = 10,
        circuit_failure_threshold: int = 3,
        circuit_recovery_sec: float = 30.0,
        _clients: Optional[Dict[str, Any]] = None,
    ):
        injected = _clients or {}

        # 1. MongoDB connection pool (maxPoolSize=10)
        if "mongo_client" in injected:
            self.mongo_client = injected["mongo_client"]
        elif MongoClient is not None:
            self.mongo_client = MongoClient(mongo_uri, maxPoolSize=10)
        else:
            self.mongo_client = None
        self.mongo_db = self.mongo_client[mongo_db] if self.mongo_client is not None else None

        # 2. Cassandra connection pool via Cluster executor_threads
        if "cassandra_session" in injected:
            self.cassandra_cluster = injected.get("cassandra_cluster")
            self.cassandra_session = injected["cassandra_session"]
        elif Cluster is not None:
            hosts = cassandra_hosts or ["127.0.0.1"]
            self.cassandra_cluster = Cluster(contact_points=hosts, executor_threads=cassandra_executor_threads)
            self.cassandra_session = self.cassandra_cluster.connect(cassandra_keyspace)
        else:
            self.cassandra_cluster = None
            self.cassandra_session = None

        # 3. Neo4j driver connection pool (max_connection_pool_size=10)
        if "neo4j_driver" in injected:
            self.neo4j_driver = injected["neo4j_driver"]
        elif GraphDatabase is not None:
            self.neo4j_driver = GraphDatabase.driver(
                neo4j_uri,
                auth=neo4j_auth,
                max_connection_pool_size=neo4j_max_pool_size,
            )
        else:
            self.neo4j_driver = None

        self.breakers = {
            "mongodb": CircuitBreaker("mongodb", circuit_failure_threshold, circuit_recovery_sec),
            "cassandra": CircuitBreaker("cassandra", circuit_failure_threshold, circuit_recovery_sec),
            "neo4j": CircuitBreaker("neo4j", circuit_failure_threshold, circuit_recovery_sec),
        }

    def _call_with_breaker(self, backend: str, fn: Callable[[], Any], fallback: Any) -> Any:
        breaker = self.breakers[backend]
        if not breaker.allow_request():
            return fallback
        try:
            result = fn()
            breaker.record_success()
            return result
        except Exception:
            breaker.record_failure()
            return fallback

    def get_transaction(self, txn_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves transaction document from MongoDB: db.transactions.find_one({'txn_id': txn_id})."""
        def _op():
            if self.mongo_db is None:
                raise RuntimeError("MongoDB not configured")
            doc = self.mongo_db["transactions"].find_one({"txn_id": txn_id})
            if isinstance(doc, dict):
                clean = dict(doc)
                clean.pop("_id", None)
                return clean
            return None

        return self._call_with_breaker("mongodb", _op, fallback=None)

    def get_user_profile(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves recent time-ordered user events from Cassandra:
        SELECT * FROM user_profiles WHERE user_id = ? ORDER BY event_time DESC LIMIT 10
        """
        cql = "SELECT * FROM user_profiles WHERE user_id = ? ORDER BY event_time DESC LIMIT 10"

        def _op():
            if self.cassandra_session is None:
                raise RuntimeError("Cassandra session not configured")
            prepared = getattr(self.cassandra_session, "prepare", None)
            if callable(prepared):
                stmt = self.cassandra_session.prepare(cql)
                rows = self.cassandra_session.execute(stmt, [user_id])
            else:
                rows = self.cassandra_session.execute(cql, [user_id])
            return [dict(r._asdict()) if hasattr(r, "_asdict") else dict(r) for r in rows]

        return self._call_with_breaker("cassandra", _op, fallback=[])

    def detect_fraud_rings(self, user_id: str, max_hops: int = 3) -> List[Dict[str, Any]]:
        """
        Traverses money transfer relationships up to `max_hops` in Neo4j:
        MATCH path = (u:User {id: $uid})-[:TRANSFERRED_TO*1..3]-(connected:User) RETURN path LIMIT 50
        """
        bounded_hops = max(1, min(int(max_hops), 5))
        cypher = (
            f"MATCH path = (u:User {{id: $uid}})-[:TRANSFERRED_TO*1..{bounded_hops}]-(connected:User) "
            "RETURN path LIMIT 50"
        )

        def _op():
            if self.neo4j_driver is None:
                raise RuntimeError("Neo4j driver not configured")
            with self.neo4j_driver.session() as session:
                result = session.run(cypher, {"uid": user_id})
                records = []
                for record in result:
                    if isinstance(record, dict):
                        records.append(record)
                    elif hasattr(record, "data"):
                        records.append(record.data())
                    else:
                        records.append({"path": record})
                return records

        return self._call_with_breaker("neo4j", _op, fallback=[])

    def analyze_fraud_workflow(self, txn_id: str, user_id: str, max_hops: int = 3) -> Dict[str, Any]:
        """Merges transaction details, recent user event history, and multi-hop transfer rings."""
        txn = self.get_transaction(txn_id)
        recent_events = self.get_user_profile(user_id)
        fraud_rings = self.detect_fraud_rings(user_id, max_hops=max_hops)

        risk_score = txn.get("risk_score", 0.0) if isinstance(txn, dict) else 0.0
        ring_count = len(fraud_rings)
        elevated_risk = bool(risk_score >= 0.75 or ring_count >= 3 or len(recent_events) >= 8)

        return {
            "txn_id": txn_id,
            "user_id": user_id,
            "transaction": txn,
            "recent_events": recent_events,
            "fraud_rings": fraud_rings,
            "summary": {
                "recent_event_count": len(recent_events),
                "connected_ring_paths": ring_count,
                "elevated_fraud_alert": elevated_risk,
                "circuit_states": {k: v.state for k, v in self.breakers.items()},
            },
        }

    def close(self):
        if self.mongo_client is not None and hasattr(self.mongo_client, "close"):
            self.mongo_client.close()
        if self.cassandra_cluster is not None and hasattr(self.cassandra_cluster, "shutdown"):
            self.cassandra_cluster.shutdown()
        if self.neo4j_driver is not None and hasattr(self.neo4j_driver, "close"):
            self.neo4j_driver.close()


# ==================== DATASET-BACKED HYBRID FRAUD RUNNER (LAB 16.2) ====================
import csv
import json
from pathlib import Path
from types import SimpleNamespace


class DatasetBackedFraudQueryService(FraudQueryService):
    """
    Connects FraudQueryService directly to the Lab 16 fraud dataset files:
      - lab16_polyglot_persistence/datasets/fraud_accounts_mongo.json
      - lab16_polyglot_persistence/datasets/cassandra_tx_by_account.csv
      - lab16_polyglot_persistence/datasets/neo4j_fraud_graph.json
    """

    def __init__(self, dataset_dir: Optional[Path] = None, **kwargs):
        base = Path(dataset_dir) if dataset_dir else Path(__file__).parent / "lab16_polyglot_persistence" / "datasets"
        self.dataset_dir = base

        accounts_list = json.loads((base / "fraud_accounts_mongo.json").read_text(encoding="utf-8"))
        self.accounts_by_id = {a["_id"]: a for a in accounts_list}

        with (base / "cassandra_tx_by_account.csv").open(encoding="utf-8") as f:
            self.tx_rows = list(csv.DictReader(f))
        self.tx_by_id = {r["tx_id"]: r for r in self.tx_rows}
        self.tx_by_account: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.tx_rows:
            enriched = {
                "user_id": r["account_id"],
                "txn_id": r["tx_id"],
                "event_time": r["tx_ts"],
                "amount": float(r["amount"]),
                "currency": r["currency"],
                "merchant": r["merchant"],
                "channel": r["channel"],
            }
            self.tx_by_account.setdefault(r["account_id"], []).append(enriched)
        for acc_id in self.tx_by_account:
            self.tx_by_account[acc_id].sort(key=lambda x: x["event_time"], reverse=True)

        graph_raw = json.loads((base / "neo4j_fraud_graph.json").read_text(encoding="utf-8"))
        self.device_links = graph_raw.get("device_links", [])
        self.p2p_transfers = graph_raw.get("p2p_transfers", [])

        # Build lightweight adapter clients so FraudQueryService circuit breakers & methods work seamlessly
        adapter_clients = self._build_adapter_clients()
        super().__init__(_clients=adapter_clients, **kwargs)

    def _build_adapter_clients(self) -> Dict[str, Any]:
        parent = self

        class _TxCollection:
            def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                txn_id = query.get("txn_id")
                row = parent.tx_by_id.get(txn_id)
                if not row:
                    return None
                acc = parent.accounts_by_id.get(row["account_id"], {})
                amt = float(row["amount"])
                risk_score = 0.92 if (acc.get("flagged") or amt >= 15000) else (0.45 if amt >= 4000 else 0.12)
                return {
                    "txn_id": row["tx_id"],
                    "user_id": row["account_id"],
                    "holder_name": acc.get("holder_name", "Unknown"),
                    "city": acc.get("city", "Unknown"),
                    "amount": amt,
                    "currency": row["currency"],
                    "merchant": row["merchant"],
                    "channel": row["channel"],
                    "timestamp": row["tx_ts"],
                    "risk_score": risk_score,
                    "flagged_account": acc.get("flagged", False),
                }

        class _CassandraAdapter:
            def execute(self, cql: str, params: List[Any]):
                uid = params[0]
                return [SimpleNamespace(_asdict=lambda item=ev: item) for ev in parent.tx_by_account.get(uid, [])[:10]]

        class _Neo4jSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return False

            def run(self, cypher: str, params: Dict[str, Any]):
                start_uid = params["uid"]
                # BFS up to 3 hops across p2p_transfers + shared devices
                adj: Dict[str, List[Dict[str, Any]]] = {}
                for tr in parent.p2p_transfers:
                    u, v = tr["from_account"], tr["to_account"]
                    adj.setdefault(u, []).append({"to": v, "rel": "TRANSFERRED_TO", "amount_inr": tr["total_transferred_inr"]})
                    adj.setdefault(v, []).append({"to": u, "rel": "TRANSFERRED_TO", "amount_inr": tr["total_transferred_inr"]})

                # Also connect accounts sharing the same device_id
                dev_map: Dict[str, List[str]] = {}
                for dl in parent.device_links:
                    dev_map.setdefault(dl["device_id"], []).append(dl["account_id"])
                for dev_id, accs in dev_map.items():
                    if len(accs) > 1:
                        for a in accs:
                            for b in accs:
                                if a != b:
                                    adj.setdefault(a, []).append({"to": b, "rel": f"SHARES_DEVICE({dev_id})", "amount_inr": 0})

                paths = []
                queue = [([start_uid], [])]
                visited_paths = set()
                while queue and len(paths) < 50:
                    nodes, edges = queue.pop(0)
                    if len(edges) >= 3:
                        continue
                    curr = nodes[-1]
                    for edge in adj.get(curr, []):
                        nxt = edge["to"]
                        if nxt in nodes:
                            continue
                        new_nodes = nodes + [nxt]
                        new_edges = edges + [edge["rel"]]
                        sig = "->".join(new_nodes)
                        if sig not in visited_paths:
                            visited_paths.add(sig)
                            paths.append({"path": new_nodes, "hops": len(new_edges), "relations": new_edges})
                            queue.append((new_nodes, new_edges))
                return paths

        class _Neo4jDriver:
            def session(self):
                return _Neo4jSession()

        return {
            "mongo_client": {"fraud_detection": {"transactions": _TxCollection()}},
            "cassandra_session": _CassandraAdapter(),
            "neo4j_driver": _Neo4jDriver(),
        }


if __name__ == "__main__":
    svc = DatasetBackedFraudQueryService()
    sample_tx = svc.tx_rows[0]["tx_id"]
    print("=== Lab 16.2 Dataset-Backed FraudQueryService Demo ===")
    print("Clean TX analysis:", json.dumps(svc.analyze_fraud_workflow(sample_tx, "ACC1001", max_hops=3), indent=2))
    print("\nFlagged Ring Account analysis (TX-ACC1031-001 / ACC1031):")
    print(json.dumps(svc.analyze_fraud_workflow("TX-ACC1031-001", "ACC1031", max_hops=3), indent=2))
