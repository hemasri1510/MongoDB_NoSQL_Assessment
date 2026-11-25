from pymongo import MongoClient
from pprint import pprint


# -----------------------------
# 1. Connect to MongoDB
# -----------------------------
client = MongoClient("mongodb://localhost:27017")
db = client["nosql_labs"]


# -----------------------------
# 2. Create courses collection
# -----------------------------
if "courses" in db.list_collection_names():
    db.drop_collection("courses")

validator = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "title",
            "code",
            "instructor",
            "schedule"
        ],
        "properties": {
            "title": {
                "bsonType": "string",
                "description": "Course title is required"
            },
            "code": {
                "bsonType": "string",
                "pattern": "^[A-Z]{2,4}[0-9]{3}$",
                "description": "Course code must be like CS401"
            },
            "instructor": {
                "bsonType": "string",
                "description": "Instructor is required"
            },
            "schedule": {
                "bsonType": "object",
                "required": [
                    "days",
                    "times",
                    "room"
                ],
                "properties": {
                    "days": {
                        "bsonType": "array",
                        "items": {
                            "bsonType": "string"
                        }
                    },
                    "times": {
                        "bsonType": "string"
                    },
                    "room": {
                        "bsonType": "string"
                    }
                }
            },
            "students": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": [
                        "name",
                        "id",
                        "grade"
                    ],
                    "properties": {
                        "name": {
                            "bsonType": "string"
                        },
                        "id": {
                            "bsonType": "string"
                        },
                        "grade": {
                            "bsonType": [
                                "string",
                                "null"
                            ]
                        }
                    }
                }
            }
        }
    }
}


db.create_collection(
    "courses",
    validator=validator
)

courses = db["courses"]

print("[OK] courses collection created with schema validation.")


# -----------------------------
# 3. Insert 3 course documents
# -----------------------------
course_data = [
    {
        "title": "Database Management Systems",
        "code": "CS401",
        "instructor": "Dr. Priya Sharma",
        "schedule": {
            "days": ["Monday", "Wednesday"],
            "times": "10:00-11:30",
            "room": "LH-201"
        },
        "students": [
            {
                "name": "Arjun Mehta",
                "id": "CS001",
                "grade": "A"
            },
            {
                "name": "Sneha Reddy",
                "id": "CS002",
                "grade": "B+"
            },
            {
                "name": "Rahul Kumar",
                "id": "CS003",
                "grade": "A-"
            }
        ]
    },

    {
        "title": "Machine Learning",
        "code": "AI301",
        "instructor": "Dr. Anil Gupta",
        "schedule": {
            "days": ["Tuesday", "Thursday"],
            "times": "14:00-15:30",
            "room": "LH-305"
        },
        "students": [
            {
                "name": "Arjun Mehta",
                "id": "CS001",
                "grade": "A"
            },
            {
                "name": "Kavya Nair",
                "id": "CS004",
                "grade": "A+"
            },
            {
                "name": "Vikram Singh",
                "id": "CS005",
                "grade": "B+"
            }
        ]
    },

    {
        "title": "Computer Networks",
        "code": "CS402",
        "instructor": "Dr. Priya Sharma",
        "schedule": {
            "days": ["Monday", "Friday"],
            "times": "09:00-10:00",
            "room": "LH-102"
        },
        "students": [
            {
                "name": "Sneha Reddy",
                "id": "CS002",
                "grade": "A-"
            },
            {
                "name": "Rahul Kumar",
                "id": "CS003",
                "grade": "B+"
            },
            {
                "name": "Priya Das",
                "id": "CS006",
                "grade": "A"
            }
        ]
    }
]


courses.insert_many(course_data)

print("[OK] Inserted 3 course documents.")


# -----------------------------
# 4. Query courses meeting Monday
# -----------------------------
print("\n=== Courses Meeting on Monday ===")

monday_courses = courses.find(
    {"schedule.days": "Monday"},
    {
        "_id": 0,
        "title": 1,
        "code": 1,
        "schedule": 1
    }
)

for course in monday_courses:
    print(
        f"{course['code']} - {course['title']} "
        f"({', '.join(course['schedule']['days'])})"
    )


# -----------------------------
# 5. Find students taught by
#    a specific instructor
# -----------------------------
instructor_name = "Dr. Priya Sharma"

print(
    f"\n=== Students Enrolled in Courses "
    f"Taught by {instructor_name} ==="
)

result = courses.find(
    {"instructor": instructor_name},
    {
        "_id": 0,
        "title": 1,
        "code": 1,
        "students": 1
    }
)

for course in result:
    print(f"\n{course['code']} - {course['title']}")

    for student in course["students"]:
        print(
            f"  {student['name']} "
            f"(ID: {student['id']}, Grade: {student['grade']})"
        )


# -----------------------------
# 6. Test schema validation
# -----------------------------
print("\n=== Testing Schema Validation ===")

try:
    courses.insert_one({
        "title": "Invalid Course"
    })

    print("[ERROR] Invalid document was accepted.")

except Exception:
    print("[OK] Invalid document rejected by schema validation.")


print("\nLab 3.1 Complete!")

client.close()