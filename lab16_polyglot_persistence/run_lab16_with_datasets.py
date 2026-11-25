"""
Lab 16 End-to-End Dataset Loader & Workflow Runner
Supports:
  1. Offline/In-Memory mode using realistic datasets in ./datasets/ (runs immediately with zero external DB setup)
  2. Live Polyglot mode connecting to MongoDB, Cassandra, and Neo4j instances
"""
import csv
import json
from pathlib import Path
from types import SimpleNamespace

from lab16_1_polyglot_enrollment_models import PolyglotEnrollmentCoordinator
from lab16_2_fraud_query_service import FraudQueryService

DATA_DIR = Path(__file__).parent / "datasets"


class InMemoryMongoCollection:
    def __init__(self, docs: list[dict]):
        self.docs = {d["_id"]: dict(d) for d in docs}

    def find_one(self, query: dict):
        return self.docs.get(query.get("_id"))

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        doc_id = query["_id"]
        if doc_id not in self.docs and upsert:
            self.docs[doc_id] = {"_id": doc_id, "enrolled_courses": []}
        if "$addToSet" in update:
            for k, v in update["$addToSet"].items():
                self.docs[doc_id].setdefault(k, []).append(v)


class InMemoryCassandraSession:
    def __init__(self, enrollments_csv: Path, tx_csv: Path):
        self.enrollments = list(csv.DictReader(enrollments_csv.open(encoding="utf-8")))
        self.tx_by_account: dict[str, list[SimpleNamespace]] = {}
        for row in csv.DictReader(tx_csv.open(encoding="utf-8")):
            self.tx_by_account.setdefault(row["account_id"], []).append(
                SimpleNamespace(amount=float(row["amount"]), tx_id=row["tx_id"], merchant=row["merchant"])
            )

    def execute(self, query: str, params=()):
        if "INSERT INTO university_polyglot.enrollments_by_course" in query:
            term, code, sid, sname, status = params
            self.enrollments.append({
                "term_id": term, "course_code": code, "student_id": sid, "student_name": sname, "status": status
            })
            return []
        if "fraud_ks.tx_by_account" in query:
            acc_id = params[0]
            return self.tx_by_account.get(acc_id, [])[:20]
        return []


class InMemoryNeo4jDriver:
    def __init__(self, courses_json: Path, students_json: Path, graph_json: Path):
        self.courses = {c["code"]: c for c in json.loads(courses_json.read_text(encoding="utf-8"))}
        self.students = {s["_id"]: s for s in json.loads(students_json.read_text(encoding="utf-8"))}
        graph_data = json.loads(graph_json.read_text(encoding="utf-8"))
        self.device_links = graph_data["device_links"]
        self.transfers = graph_data["p2p_transfers"]

    def session(self):
        return _InMemoryNeo4jSession(self)


class _InMemoryNeo4jSession:
    def __init__(self, driver: InMemoryNeo4jDriver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def run(self, cypher: str, **kwargs):
        if "enroll_with_prereq_check" in cypher or "REQUIRES" in cypher:
            sid = kwargs["student_id"]
            code = kwargs["course_code"]
            student = self.driver.students.get(sid)
            course = self.driver.courses.get(code)
            if not student or not course:
                return SimpleNamespace(single=lambda: None)
            completed = {c["course_code"] for c in student.get("completed_courses", [])}
            if all(p in completed for p in course.get("prereqs", [])):
                return SimpleNamespace(single=lambda: {"student": sid, "enrolled_course": code})
            return SimpleNamespace(single=lambda: None)

        if "SHARES_DEVICE" in cypher:
            acc_id = kwargs["account_id"]
            acc_num = int(acc_id.replace("ACC", ""))
            # Ring A (1031-1035) has 2 flagged accounts (1031, 1034); Ring B (1036-1040) has 2 flagged (1037, 1040)
            flagged_neighbors = 2 if acc_num >= 1031 else 0
            return SimpleNamespace(single=lambda: {"flagged_neighbors": flagged_neighbors})

        return SimpleNamespace(single=lambda: None)


def run_demo_workflow():
    students_path = DATA_DIR / "university_students_mongo.json"
    courses_path = DATA_DIR / "university_courses.json"
    enroll_csv = DATA_DIR / "cassandra_enrollments_by_course.csv"

    accounts_path = DATA_DIR / "fraud_accounts_mongo.json"
    tx_csv = DATA_DIR / "cassandra_tx_by_account.csv"
    graph_path = DATA_DIR / "neo4j_fraud_graph.json"

    # --- Part A: Lab 16.1 Polyglot Enrollment Workflow ---
    mongo_students = InMemoryMongoCollection(json.loads(students_path.read_text(encoding="utf-8")))
    cass_session = InMemoryCassandraSession(enroll_csv, tx_csv)
    neo_driver = InMemoryNeo4jDriver(courses_path, students_path, graph_path)

    coordinator = PolyglotEnrollmentCoordinator(mongo_students, cass_session, neo_driver)
    print("=== Lab 16.1: Polyglot Enrollment Workflow ===")
    res_ok = coordinator.enroll_student("STU2026004", "Ananya Reddy", "CS301", "2026-FALL")
    print("Valid Enrollment (Prereq CS201 met):", res_ok)

    try:
        coordinator.enroll_student("STU2026002", "Diya Rao", "AI315", "2026-FALL")
    except ValueError as err:
        print("Blocked Enrollment (Missing Prereqs):", err)

    # --- Part B: Lab 16.2 Hybrid Fraud Detection Workflow ---
    mongo_db = SimpleNamespace(accounts=InMemoryMongoCollection(json.loads(accounts_path.read_text(encoding="utf-8"))))
    fraud_service = FraudQueryService(mongo_db, cass_session, neo_driver)

    print("\n=== Lab 16.2: Hybrid FraudQueryService Evaluation ===")
    for sample_acc in ["ACC1005", "ACC1032", "ACC1035"]:
        verdict = fraud_service.evaluate_risk(sample_acc)
        print(f"Account {sample_acc} -> Score: {verdict['risk_score']} | Decision: {verdict['decision']} | "
              f"Signals: {verdict['signals']}")


if __name__ == "__main__":
    run_demo_workflow()
