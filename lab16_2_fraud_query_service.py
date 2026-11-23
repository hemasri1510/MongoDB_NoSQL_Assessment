"""
Lab 16.2: Hybrid FraudQueryService with Connection Pooling & Circuit Breakers
Combines:
  - MongoDB (Account profile & device metadata)
  - Cassandra (High-velocity transaction history)
  - Neo4j (Fraud ring / shared-entity graph distance)
Protected by per-backend Circuit Breakers for graceful degradation and SLA enforcement.
"""
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from enum import Enum
from threading import Lock
from typing import Any, Callable


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout_sec: float = 15.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = Lock()

    def call(self, fn: Callable[..., Any], *args, fallback: Any = None, **kwargs) -> Any:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if time.monotonic() - self.last_failure_time >= self.recovery_timeout_sec:
                    self.state = CircuitState.HALF_OPEN
                else:
                    return fallback

        try:
            result = fn(*args, **kwargs)
        except Exception:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.monotonic()
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
            return fallback

        with self._lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
        return result


class FraudQueryService:
    """Aggregates multi-database fraud signals concurrently under strict latency budgets."""

    def __init__(self, mongo_db, cassandra_session, neo4j_driver, max_workers: int = 6):
        self.mongo_db = mongo_db
        self.cassandra = cassandra_session
        self.neo4j = neo4j_driver
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

        self.cb_mongo = CircuitBreaker("mongodb_profile")
        self.cb_cassandra = CircuitBreaker("cassandra_velocity")
        self.cb_neo4j = CircuitBreaker("neo4j_ring")

    def _fetch_account_profile(self, account_id: str) -> dict:
        doc = self.mongo_db.accounts.find_one({"_id": account_id})
        return doc or {"_id": account_id, "kyc_verified": False, "risk_tier": "UNKNOWN"}

    def _fetch_recent_tx_velocity(self, account_id: str) -> dict:
        rows = list(
            self.cassandra.execute(
                "SELECT amount FROM fraud_ks.tx_by_account WHERE account_id = %s LIMIT 20",
                (account_id,),
            )
        )
        total_amount = sum(getattr(r, "amount", 0.0) for r in rows)
        return {"recent_tx_count": len(rows), "recent_volume": round(total_amount, 2)}

    def _fetch_graph_ring_hops(self, account_id: str) -> dict:
        with self.neo4j.session() as session:
            rec = session.run(
                """
                MATCH (a:Account {id: $account_id})-[:SHARES_DEVICE|TRANSFERRED_TO*1..3]-(f:Account {flagged: true})
                RETURN count(DISTINCT f) AS flagged_neighbors
                """,
                account_id=account_id,
            ).single()
            return {"flagged_neighbors": rec["flagged_neighbors"] if rec else 0}

    def evaluate_risk(self, account_id: str, timeout_sec: float = 0.25) -> dict[str, Any]:
        t0 = time.perf_counter()

        f_mongo = self.executor.submit(
            self.cb_mongo.call,
            self._fetch_account_profile,
            account_id,
            fallback={"_id": account_id, "kyc_verified": True, "degraded": True},
        )
        f_cass = self.executor.submit(
            self.cb_cassandra.call,
            self._fetch_recent_tx_velocity,
            account_id,
            fallback={"recent_tx_count": 0, "recent_volume": 0.0, "degraded": True},
        )
        f_neo = self.executor.submit(
            self.cb_neo4j.call,
            self._fetch_graph_ring_hops,
            account_id,
            fallback={"flagged_neighbors": 0, "degraded": True},
        )

        def safe_result(fut, default):
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                return {**default, "timed_out": True}

        profile = safe_result(f_mongo, {"kyc_verified": True})
        velocity = safe_result(f_cass, {"recent_tx_count": 0, "recent_volume": 0.0})
        graph = safe_result(f_neo, {"flagged_neighbors": 0})

        score = 0
        if not profile.get("kyc_verified", True):
            score += 30
        if velocity.get("recent_tx_count", 0) >= 10 or velocity.get("recent_volume", 0.0) > 50000:
            score += 35
        if graph.get("flagged_neighbors", 0) > 0:
            score += min(35, graph["flagged_neighbors"] * 15)

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "account_id": account_id,
            "risk_score": min(score, 100),
            "decision": "BLOCK" if score >= 70 else ("REVIEW" if score >= 40 else "ALLOW"),
            "signals": {"profile": profile, "velocity": velocity, "graph": graph},
            "circuit_states": {
                "mongo": self.cb_mongo.state.value,
                "cassandra": self.cb_cassandra.state.value,
                "neo4j": self.cb_neo4j.state.value,
            },
            "latency_ms": elapsed_ms,
        }
