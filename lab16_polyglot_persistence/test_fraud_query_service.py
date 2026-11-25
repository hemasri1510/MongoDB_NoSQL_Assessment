"""
Unit & Integration Tests for Lab 16.2 FraudQueryService using unittest.mock.
Run:
  python -m unittest discover -s . -p "test_*.py" -v
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fraud_query_service import FraudQueryService


class TestFraudQueryService(unittest.TestCase):
    def setUp(self):
        self.mock_mongo_db = MagicMock()
        self.mock_mongo_client = {"fraud_detection": self.mock_mongo_db}

        self.mock_cassandra_session = MagicMock()
        self.mock_cassandra_cluster = MagicMock()

        self.mock_neo4j_session = MagicMock()
        self.mock_neo4j_ctx = MagicMock()
        self.mock_neo4j_ctx.__enter__.return_value = self.mock_neo4j_session
        self.mock_neo4j_ctx.__exit__.return_value = False
        self.mock_neo4j_driver = MagicMock()
        self.mock_neo4j_driver.session.return_value = self.mock_neo4j_ctx

        self.service = FraudQueryService(
            _clients={
                "mongo_client": self.mock_mongo_client,
                "cassandra_cluster": self.mock_cassandra_cluster,
                "cassandra_session": self.mock_cassandra_session,
                "neo4j_driver": self.mock_neo4j_driver,
            }
        )

    def test_get_transaction_from_mongodb(self):
        self.mock_mongo_db["transactions"].find_one.return_value = {
            "_id": "mongo-oid-1",
            "txn_id": "TXN-9001",
            "user_id": "USR-42",
            "amount": 185000.0,
            "merchant": "Global Wire Hub",
            "timestamp": "2026-11-21T12:00:00Z",
            "risk_score": 0.91,
        }

        doc = self.service.get_transaction("TXN-9001")
        self.assertIsNotNone(doc)
        self.assertEqual(doc["txn_id"], "TXN-9001")
        self.assertEqual(doc["risk_score"], 0.91)
        self.assertNotIn("_id", doc)
        self.mock_mongo_db["transactions"].find_one.assert_called_once_with({"txn_id": "TXN-9001"})

    def test_get_user_profile_ordered_from_cassandra(self):
        mock_rows = [
            SimpleNamespace(_asdict=lambda: {"user_id": "USR-42", "event_time": "2026-11-21T12:01:00Z", "action": "LOGIN"}),
            SimpleNamespace(_asdict=lambda: {"user_id": "USR-42", "event_time": "2026-11-21T11:55:00Z", "action": "TRANSFER"}),
        ]
        self.mock_cassandra_session.execute.return_value = mock_rows

        events = self.service.get_user_profile("USR-42")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["action"], "LOGIN")
        cql_arg = self.mock_cassandra_session.prepare.call_args[0][0]
        self.assertIn("ORDER BY event_time DESC LIMIT 10", cql_arg)

    def test_detect_fraud_rings_multihop_neo4j(self):
        rec1 = MagicMock()
        rec1.data.return_value = {"path": ["USR-42", "USR-88", "USR-99"]}
        self.mock_neo4j_session.run.return_value = [rec1]

        rings = self.service.detect_fraud_rings("USR-42", max_hops=3)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0]["path"], ["USR-42", "USR-88", "USR-99"])
        cypher_arg = self.mock_neo4j_session.run.call_args[0][0]
        self.assertIn("[:TRANSFERRED_TO*1..3]", cypher_arg)

    def test_circuit_breaker_opens_after_three_failures_and_recovers(self):
        self.mock_mongo_db["transactions"].find_one.side_effect = RuntimeError("Mongo timeout")

        for _ in range(3):
            self.assertIsNone(self.service.get_transaction("TXN-ERR"))
        self.assertEqual(self.service.breakers["mongodb"].state, "OPEN")

        # 4th call should short-circuit without invoking driver
        calls_before = self.mock_mongo_db["transactions"].find_one.call_count
        self.assertIsNone(self.service.get_transaction("TXN-ERR"))
        self.assertEqual(self.mock_mongo_db["transactions"].find_one.call_count, calls_before)

        # Simulate 30s recovery window elapsing -> HALF_OPEN -> success -> CLOSED
        self.service.breakers["mongodb"].opened_at -= 31.0
        self.mock_mongo_db["transactions"].find_one.side_effect = None
        self.mock_mongo_db["transactions"].find_one.return_value = {"txn_id": "TXN-RECOVERED", "risk_score": 0.2}

        recovered = self.service.get_transaction("TXN-RECOVERED")
        self.assertEqual(recovered["txn_id"], "TXN-RECOVERED")
        self.assertEqual(self.service.breakers["mongodb"].state, "CLOSED")

    def test_integration_workflow_merges_polyglot_results(self):
        self.mock_mongo_db["transactions"].find_one.return_value = {
            "txn_id": "TXN-555",
            "user_id": "USR-77",
            "amount": 250000.0,
            "merchant": "Offshore Exchange",
            "timestamp": "2026-11-21T12:05:00Z",
            "risk_score": 0.95,
        }
        self.mock_cassandra_session.execute.return_value = [
            SimpleNamespace(_asdict=lambda: {"user_id": "USR-77", "event_time": "2026-11-21T12:05:00Z", "event": "HIGH_VALUE_TX"})
        ]
        r1 = MagicMock()
        r1.data.return_value = {"path": ["USR-77", "USR-11"]}
        r2 = MagicMock()
        r2.data.return_value = {"path": ["USR-77", "USR-11", "USR-12"]}
        r3 = MagicMock()
        r3.data.return_value = {"path": ["USR-77", "USR-11", "USR-12", "USR-13"]}
        self.mock_neo4j_session.run.return_value = [r1, r2, r3]

        merged = self.service.analyze_fraud_workflow("TXN-555", "USR-77", max_hops=3)
        self.assertEqual(merged["transaction"]["txn_id"], "TXN-555")
        self.assertEqual(merged["summary"]["recent_event_count"], 1)
        self.assertEqual(merged["summary"]["connected_ring_paths"], 3)
        self.assertTrue(merged["summary"]["elevated_fraud_alert"])


if __name__ == "__main__":
    unittest.main()
