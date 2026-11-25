"""
Lab 16.1: University Course Enrollment Polyglot Persistence Models
Demonstrates hybrid storage design across:
  1. MongoDB (Document Store): Rich student profiles, nested transcripts & preferences
  2. Apache Cassandra (Wide-Column Store): Query-driven enrollment rosters & audit logs
  3. Neo4j (Graph Store): Course prerequisites, advisor networks & peer recommendations
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# 1. MongoDB Document Model (Entity-Centric, Rich Nested Hierarchy)
# ---------------------------------------------------------------------------
@dataclass
class MongoStudentDocument:
    student_id: str
    full_name: str
    email: str
    program: str
    semester: int
    enrolled_courses: list[dict[str, Any]] = field(default_factory=list)
    completed_courses: list[dict[str, Any]] = field(default_factory=list)

    def to_bson(self) -> dict[str, Any]:
        return {
            "_id": self.student_id,
            "full_name": self.full_name,
            "email": self.email,
            "program": self.program,
            "semester": self.semester,
            "enrolled_courses": self.enrolled_courses,
            "completed_courses": self.completed_courses,
            "updated_at": datetime.now(timezone.utc),
        }


# ---------------------------------------------------------------------------
# 2. Cassandra Query-Driven Table Models (Partitioned for High-Write & Roster Reads)
# ---------------------------------------------------------------------------
CASSANDRA_SCHEMA_CQL = """
CREATE KEYSPACE IF NOT EXISTS university_polyglot
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 3};

-- Query Q1: Fetch all students enrolled in a specific course & term
CREATE TABLE IF NOT EXISTS university_polyglot.enrollments_by_course (
    term_id text,
    course_code text,
    enrolled_at timestamp,
    student_id text,
    student_name text,
    status text,
    PRIMARY KEY ((term_id, course_code), enrolled_at, student_id)
) WITH CLUSTERING ORDER BY (enrolled_at DESC, student_id ASC);

-- Query Q2: Time-series enrollment audit events per student
CREATE TABLE IF NOT EXISTS university_polyglot.student_enrollment_events (
    student_id text,
    event_ts timeuuid,
    course_code text,
    action text,
    PRIMARY KEY ((student_id), event_ts)
) WITH CLUSTERING ORDER BY (event_ts DESC);
"""


# ---------------------------------------------------------------------------
# 3. Neo4j Graph Model (Prerequisite Traversal & Peer Course Recommendations)
# ---------------------------------------------------------------------------
NEO4J_CYPHER_QUERIES = {
    "create_constraints": """
        CREATE CONSTRAINT student_id_unique IF NOT EXISTS FOR (s:Student) REQUIRE s.student_id IS UNIQUE;
        CREATE CONSTRAINT course_code_unique IF NOT EXISTS FOR (c:Course) REQUIRE c.code IS UNIQUE;
    """,
    "enroll_with_prereq_check": """
        MATCH (s:Student {student_id: $student_id}), (target:Course {code: $course_code})
        OPTIONAL MATCH (target)-[:REQUIRES]->(prereq:Course)
        WITH s, target, collect(prereq) AS prereqs
        WHERE ALL(p IN prereqs WHERE (s)-[:COMPLETED]->(p))
        MERGE (s)-[r:ENROLLED_IN {term: $term}]->(target)
        RETURN s.student_id AS student, target.code AS enrolled_course
    """,
    "recommend_courses": """
        MATCH (s:Student {student_id: $student_id})-[:COMPLETED]->(:Course)<-[:COMPLETED]-(peer:Student)
        MATCH (peer)-[:ENROLLED_IN]->(rec:Course)
        WHERE NOT (s)-[:COMPLETED|ENROLLED_IN]->(rec)
        RETURN rec.code AS course_code, rec.title AS title, count(DISTINCT peer) AS peer_score
        ORDER BY peer_score DESC LIMIT 5
    """,
}


class PolyglotEnrollmentCoordinator:
    """Coordinates writes/reads across MongoDB, Cassandra, and Neo4j adapters."""

    def __init__(self, mongo_coll, cassandra_session, neo4j_driver):
        self.mongo_coll = mongo_coll
        self.cassandra = cassandra_session
        self.neo4j = neo4j_driver

    def enroll_student(self, student_id: str, student_name: str, course_code: str, term: str) -> dict:
        # 1. Verify prerequisites & create graph relationship in Neo4j
        with self.neo4j.session() as session:
            result = session.run(
                NEO4J_CYPHER_QUERIES["enroll_with_prereq_check"],
                student_id=student_id,
                course_code=course_code,
                term=term,
            ).single()
            if not result:
                raise ValueError(f"Prerequisites not met for {student_id} -> {course_code}")

        # 2. Update rich student profile document in MongoDB
        self.mongo_coll.update_one(
            {"_id": student_id},
            {"$addToSet": {"enrolled_courses": {"course_code": course_code, "term": term}}},
            upsert=True,
        )

        # 3. Append roster row in Cassandra
        self.cassandra.execute(
            """
            INSERT INTO university_polyglot.enrollments_by_course
            (term_id, course_code, enrolled_at, student_id, student_name, status)
            VALUES (%s, %s, toTimestamp(now()), %s, %s, %s)
            """,
            (term, course_code, student_id, student_name, "ACTIVE"),
        )
        return {"status": "ENROLLED", "student_id": student_id, "course_code": course_code, "term": term}
