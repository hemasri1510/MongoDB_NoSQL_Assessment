"""Unit Tests for Lab 16.1 Polyglot Enrollment & Lab 16.2 FraudQueryService."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from lab16_1_polyglot_enrollment_models import MongoStudentDocument, PolyglotEnrollmentCoordinator
from lab16_2_fraud_query_service import CircuitBreaker, CircuitState, FraudQueryService


def test_mongo_student_document_serialization():
    doc = MongoStudentDocument(
        student_id="STU101",
        full_name="Aarav Nair",
        email="aarav@university.edu",
        program="B.Tech CSE (AI & ML)",
        semester=5,
    ).to_bson()
    assert doc["_id"] == "STU101"
    assert doc["semester"] == 5
    assert "updated_at" in doc


def test_polyglot_enrollment_coordinator_success():
    mongo_coll = MagicMock()
    cassandra_session = MagicMock()
    neo4j_driver = MagicMock()

    mock_session = MagicMock()
    mock_session.run.return_value.single.return_value = {"student": "STU101", "enrolled_course": "CS301"}
    neo4j_driver.session.return_value.__enter__.return_value = mock_session

    coord = PolyglotEnrollmentCoordinator(mongo_coll, cassandra_session, neo4j_driver)
    res = coord.enroll_student("STU101", "Aarav Nair", "CS301", "2026-FALL")

    assert res["status"] == "ENROLLED"
    mongo_coll.update_one.assert_called_once()
    cassandra_session.execute.assert_called_once()


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker("test_backend", failure_threshold=2, recovery_timeout_sec=60)

    def failing_op():
        raise ConnectionError("Backend unreachable")

    assert cb.call(failing_op, fallback="FALLBACK") == "FALLBACK"
    assert cb.state == CircuitState.CLOSED
    assert cb.call(failing_op, fallback="FALLBACK") == "FALLBACK"
    assert cb.state == CircuitState.OPEN


def test_fraud_query_service_high_risk_and_degraded_neo4j():
    mongo_db = MagicMock()
    mongo_db.accounts.find_one.return_value = {"_id": "ACC99", "kyc_verified": False}

    cassandra_session = MagicMock()
    cassandra_session.execute.return_value = [SimpleNamespace(amount=12000.0) for _ in range(6)]

    neo4j_driver = MagicMock()
    neo4j_driver.session.side_effect = RuntimeError("Neo4j timeout")

    svc = FraudQueryService(mongo_db, cassandra_session, neo4j_driver)
    res = svc.evaluate_risk("ACC99")

    assert res["account_id"] == "ACC99"
    assert res["risk_score"] == 65
    assert res["decision"] == "REVIEW"
    assert res["signals"]["graph"]["degraded"] is True
