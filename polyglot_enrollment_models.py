"""
Lab 16.1 & Activity 16.1: University Course Enrollment Polyglot Modeling
Connected directly to the Lab 16 datasets:
  - lab16_polyglot_persistence/datasets/university_students_mongo.json (MongoDB Document Model)
  - lab16_polyglot_persistence/datasets/cassandra_enrollments_by_course.csv (Cassandra Query-Driven Table)
  - lab16_polyglot_persistence/datasets/university_courses.json (Neo4j Course & Prerequisite Graph)

Implements:
1. Schema definitions & equivalent queries across MongoDB, Cassandra, and Neo4j:
   - Find all courses for a student (e.g., STU2026001 / Aarav Iyer)
   - Find all students in a course (e.g., AI315 / CS402)
   - Find students who share a course with a target student
2. Complexity & performance characteristics comparison across all 3 paradigms.
3. Activity 16.1 Hybrid Architecture:
   - MongoDB -> Operational student & course state
   - Cassandra -> High-write append-only enrollment audit log
   - Neo4j -> Multi-hop prerequisite chain validation
"""

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_DATASET_DIR = Path(__file__).parent / "lab16_polyglot_persistence" / "datasets"


SCHEMAS = {
    "mongodb": {
        "students": {
            "_id": "STU2026001",
            "full_name": "Aarav Iyer",
            "email": "aarav.iyer1@university.edu.in",
            "program": "B.Tech CSE",
            "semester": 7,
            "completed_courses": [{"course_code": "CS101", "grade": "O", "term": "2026-SPRING"}],
            "enrolled_courses": [{"course_code": "AI315", "term": "2026-FALL", "status": "ACTIVE"}],
        },
        "courses": {
            "_id": "AI315",
            "title": "Deep Learning & Neural Architectures",
            "credits": 4,
            "department": "AI & ML",
            "prereqs": ["AI210", "CS204"],
            "capacity": 60,
        },
    },
    "cassandra": {
        "student_courses": (
            "CREATE TABLE IF NOT EXISTS university_enrollment.student_courses ("
            "  student_id TEXT,"
            "  course_code TEXT,"
            "  student_name TEXT,"
            "  term_id TEXT,"
            "  enrolled_at TIMESTAMP,"
            "  PRIMARY KEY (student_id, course_code)"
            ");"
        ),
        "course_students": (
            "CREATE TABLE IF NOT EXISTS university_enrollment.course_students ("
            "  term_id TEXT,"
            "  course_code TEXT,"
            "  enrolled_at TIMESTAMP,"
            "  student_id TEXT,"
            "  student_name TEXT,"
            "  status TEXT,"
            "  PRIMARY KEY ((term_id, course_code), enrolled_at, student_id)"
            ") WITH CLUSTERING ORDER BY (enrolled_at DESC, student_id ASC);"
        ),
    },
    "neo4j": {
        "nodes_and_edges": [
            "MERGE (s:Student {student_id: 'STU2026001', full_name: 'Aarav Iyer'})",
            "MERGE (c:Course {code: 'AI315', title: 'Deep Learning & Neural Architectures'})",
            "MERGE (p:Course {code: 'AI210'})-[:PREREQUISITE_OF]->(c)",
            "MERGE (s)-[:ENROLLED_IN {term: '2026-FALL'}]->(c)",
        ]
    },
}


class PolyglotEnrollmentDatasetEngine:
    """Loads Lab 16 dataset files and executes equivalent queries across MongoDB, Cassandra, and Neo4j models."""

    def __init__(self, dataset_dir: Path = DEFAULT_DATASET_DIR):
        self.dataset_dir = Path(dataset_dir)
        self.students_path = self.dataset_dir / "university_students_mongo.json"
        self.courses_path = self.dataset_dir / "university_courses.json"
        self.cassandra_csv_path = self.dataset_dir / "cassandra_enrollments_by_course.csv"

        self.mongo_students: List[Dict[str, Any]] = json.loads(self.students_path.read_text(encoding="utf-8"))
        self.courses: List[Dict[str, Any]] = json.loads(self.courses_path.read_text(encoding="utf-8"))
        with self.cassandra_csv_path.open(encoding="utf-8") as f:
            self.cassandra_rows: List[Dict[str, str]] = list(csv.DictReader(f))

        # Build Cassandra partition tables: student_courses & course_students
        self.cass_student_courses: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        self.cass_course_students: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for row in self.cassandra_rows:
            self.cass_student_courses[row["student_id"]].append(row)
            self.cass_course_students[row["course_code"]].append(row)

        # Build Neo4j Property Graph adjacency
        self.neo_student_to_courses: Dict[str, set] = defaultdict(set)
        self.neo_course_to_students: Dict[str, set] = defaultdict(set)
        self.neo_prereqs: Dict[str, List[str]] = {c["code"]: list(c.get("prereqs", [])) for c in self.courses}
        self.student_names: Dict[str, str] = {s["_id"]: s["full_name"] for s in self.mongo_students}

        for s in self.mongo_students:
            sid = s["_id"]
            for enr in s.get("enrolled_courses", []):
                cc = enr["course_code"]
                self.neo_student_to_courses[sid].add(cc)
                self.neo_course_to_students[cc].add(sid)

    def resolve_student(self, student_identifier: str) -> Dict[str, Any]:
        for s in self.mongo_students:
            if s["_id"] == student_identifier or s["full_name"].lower() == student_identifier.lower():
                return s
        raise ValueError(f"Student not found in dataset: {student_identifier}")

    def query_courses_for_student(self, student_identifier: str = "STU2026001") -> Dict[str, Any]:
        stu = self.resolve_student(student_identifier)
        sid = stu["_id"]
        name = stu["full_name"]

        mongo_active = [c["course_code"] for c in stu.get("enrolled_courses", [])]
        mongo_completed = [c["course_code"] for c in stu.get("completed_courses", [])]
        cass_courses = sorted({r["course_code"] for r in self.cass_student_courses.get(sid, [])})
        neo_courses = sorted(self.neo_student_to_courses.get(sid, set()))

        return {
            "student_id": sid,
            "student_name": name,
            "program": stu["program"],
            "mongodb": {
                "query": f"db.students.findOne({{ _id: '{sid}' }}, {{ enrolled_courses: 1, completed_courses: 1 }})",
                "active_courses": mongo_active,
                "completed_courses": mongo_completed,
            },
            "cassandra": {
                "query": f"SELECT course_code, term_id FROM student_courses WHERE student_id = '{sid}';",
                "courses": cass_courses,
            },
            "neo4j": {
                "query": f"MATCH (s:Student {{student_id: '{sid}'}})-[:ENROLLED_IN]->(c:Course) RETURN c.code",
                "courses": neo_courses,
            },
        }

    def query_students_in_course(self, course_code: str = "AI315") -> Dict[str, Any]:
        mongo_list = [
            {"student_id": s["_id"], "full_name": s["full_name"]}
            for s in self.mongo_students
            if any(e["course_code"] == course_code for e in s.get("enrolled_courses", []))
        ]
        # Deduplicate Cassandra partition rows by student_id
        cass_seen = {}
        for r in self.cass_course_students.get(course_code, []):
            cass_seen[r["student_id"]] = {"student_id": r["student_id"], "student_name": r["student_name"]}
        cass_list = list(cass_seen.values())

        neo_list = [
            {"student_id": sid, "full_name": self.student_names.get(sid, sid)}
            for sid in sorted(self.neo_course_to_students.get(course_code, set()))
        ]

        return {
            "course_code": course_code,
            "mongodb": {
                "query": f"db.students.find({{ 'enrolled_courses.course_code': '{course_code}' }}, {{ full_name: 1 }})",
                "count": len(mongo_list),
                "students": mongo_list,
            },
            "cassandra": {
                "query": f"SELECT student_id, student_name FROM course_students WHERE term_id='2026-FALL' AND course_code='{course_code}';",
                "count": len(cass_list),
                "students": cass_list,
            },
            "neo4j": {
                "query": f"MATCH (s:Student)-[:ENROLLED_IN]->(c:Course {{code: '{course_code}'}}) RETURN s.student_id, s.full_name",
                "count": len(neo_list),
                "students": neo_list,
            },
        }

    def query_students_sharing_course(self, student_identifier: str = "STU2026001") -> Dict[str, Any]:
        stu = self.resolve_student(student_identifier)
        sid = stu["_id"]
        name = stu["full_name"]
        my_courses = self.neo_student_to_courses.get(sid, set())

        peers: Dict[str, Dict[str, Any]] = {}
        for cc in sorted(my_courses):
            for other_sid in sorted(self.neo_course_to_students.get(cc, set())):
                if other_sid == sid:
                    continue
                if other_sid not in peers:
                    peers[other_sid] = {
                        "student_id": other_sid,
                        "full_name": self.student_names.get(other_sid, other_sid),
                        "shared_courses": [],
                    }
                peers[other_sid]["shared_courses"].append(cc)

        peer_list = list(peers.values())
        return {
            "target_student": {"student_id": sid, "full_name": name, "active_courses": sorted(my_courses)},
            "shared_peer_count": len(peer_list),
            "peers": peer_list,
            "equivalent_queries": {
                "mongodb": f"db.students.aggregate([{{$match:{{_id:'{sid}'}}}},{{$lookup:{{from:'students',localField:'enrolled_courses.course_code',foreignField:'enrolled_courses.course_code',as:'peers'}}}}])",
                "cassandra": f"-- 2-step app join: SELECT course_code FROM student_courses WHERE student_id='{sid}' -> IN query on course_students",
                "neo4j": f"MATCH (s1:Student {{student_id:'{sid}'}})-[:ENROLLED_IN]->(c:Course)<-[:ENROLLED_IN]-(s2:Student) WHERE s2.student_id <> '{sid}' RETURN DISTINCT s2.full_name, collect(c.code)",
            },
        }

    def check_prerequisite_chain(self, student_identifier: str, target_course: str) -> Dict[str, Any]:
        """Activity 16.1: Multi-hop Neo4j prerequisite chain traversal against student's completed courses."""
        stu = self.resolve_student(student_identifier)
        completed = {c["course_code"] for c in stu.get("completed_courses", [])}

        # BFS/DFS multi-hop prerequisite chain
        chain = []
        visited = set()
        stack = list(self.neo_prereqs.get(target_course, []))
        while stack:
            curr = stack.pop(0)
            if curr not in visited:
                visited.add(curr)
                chain.append(curr)
                stack.extend(self.neo_prereqs.get(curr, []))

        missing = [c for c in chain if c not in completed]
        return {
            "student_id": stu["_id"],
            "student_name": stu["full_name"],
            "target_course": target_course,
            "full_prerequisite_chain": chain,
            "missing_prerequisites": missing,
            "eligible_to_enroll": len(missing) == 0,
        }


def run_local_enrollment_demo():
    engine = PolyglotEnrollmentDatasetEngine()
    print(f"Loaded Lab 16 dataset: {len(engine.mongo_students)} students, {len(engine.courses)} courses, {len(engine.cassandra_rows)} Cassandra rows.")
    print("\n1. Courses for STU2026001 (Aarav Iyer):")
    print(json.dumps(engine.query_courses_for_student("STU2026001"), indent=2))
    print("\n2. Students in AI315:")
    print(json.dumps(engine.query_students_in_course("AI315"), indent=2))
    print("\n3. Students Sharing a Course with STU2026001:")
    print(json.dumps(engine.query_students_sharing_course("STU2026001"), indent=2))
    print("\nActivity 16.1 Prerequisite Chain Check (STU2026002 -> AI315):")
    print(json.dumps(engine.check_prerequisite_chain("STU2026002", "AI315"), indent=2))


if __name__ == "__main__":
    run_local_enrollment_demo()
