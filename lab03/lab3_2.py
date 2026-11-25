from pymongo import MongoClient


# ==========================================
# CONNECT TO MONGODB
# ==========================================
client = MongoClient("mongodb://localhost:27017")
db = client["nosql_labs"]

print("=" * 60)
print("Lab 3.2: Migrate from Referenced to Embedded Design")
print("=" * 60)


# ==========================================
# PART A: REFERENCED DESIGN
# ==========================================
print("\n=== PART A: Referenced Design ===")

# Delete old collections if they exist
if "posts" in db.list_collection_names():
    db.drop_collection("posts")

if "comments" in db.list_collection_names():
    db.drop_collection("comments")

posts_col = db["posts"]
comments_col = db["comments"]


# ==========================================
# 1. INSERT 5 POSTS
# ==========================================
posts = [
    {
        "title": "Why NoSQL Matters for AI",
        "author": "Arjun",
        "tags": ["nosql", "ai"],
        "created": "2024-01-15",
        "photo": "https://images.unsplash.com/photo-1555949963-ff9fe0c870eb",
        "video": "https://www.youtube.com/watch?v=aircAruvnKk"
    },

    {
        "title": "MongoDB Aggregation Tips",
        "author": "Sneha",
        "tags": ["mongodb", "aggregation"],
        "created": "2024-01-20"
    },

    {
        "title": "Vector Search Deep Dive",
        "author": "Kavya",
        "tags": ["vector", "ai", "search"],
        "created": "2024-02-01"
    },

    {
        "title": "CAP Theorem Explained",
        "author": "Arjun",
        "tags": ["cap", "distributed"],
        "created": "2024-02-10"
    },

    {
        "title": "Graph Databases for Fraud",
        "author": "Rahul",
        "tags": ["neo4j", "fraud", "graph"],
        "created": "2024-02-15"
    }
]

post_result = posts_col.insert_many(posts)

post_ids = post_result.inserted_ids

print(f"[OK] Inserted {len(post_ids)} posts.")


# ==========================================
# 2. INSERT 10 COMMENTS
# ==========================================
comments = [
    {
        "post_id": post_ids[0],
        "text": "Great introduction to NoSQL!",
        "author": "Reader1"
    },
    {
        "post_id": post_ids[0],
        "text": "Very useful for AI applications.",
        "author": "Reader2"
    },

    {
        "post_id": post_ids[1],
        "text": "The aggregation examples are helpful.",
        "author": "Reader3"
    },
    {
        "post_id": post_ids[1],
        "text": "Can you explain $facet next?",
        "author": "Reader4"
    },

    {
        "post_id": post_ids[2],
        "text": "HNSW vs IVF comparison would be useful.",
        "author": "Reader5"
    },
    {
        "post_id": post_ids[2],
        "text": "Excellent explanation of vector search.",
        "author": "Reader6"
    },

    {
        "post_id": post_ids[3],
        "text": "CAP theorem is important for interviews.",
        "author": "Reader7"
    },
    {
        "post_id": post_ids[3],
        "text": "Good explanation with examples.",
        "author": "Reader8"
    },

    {
        "post_id": post_ids[4],
        "text": "Neo4j and graph databases are interesting.",
        "author": "Reader9"
    },
    {
        "post_id": post_ids[4],
        "text": "A real-world fraud case study would help.",
        "author": "Reader10"
    }
]

comments_col.insert_many(comments)

print(f"[OK] Inserted {len(comments)} comments.")


# ==========================================
# 3. REFERENCED DESIGN USING $lookup
# ==========================================
print("\n=== Referenced: Post + Comments using $lookup ===")

pipeline = [
    {
        "$match": {
            "_id": post_ids[0]
        }
    },
    {
        "$lookup": {
            "from": "comments",
            "localField": "_id",
            "foreignField": "post_id",
            "as": "comments"
        }
    }
]

result = list(posts_col.aggregate(pipeline))

if result:
    post = result[0]

    print(f"\nPost: {post['title']}")
    print(f"Author: {post['author']}")
    print(f"Number of comments: {len(post['comments'])}")

    print("\nComments:")

    for comment in post["comments"]:
        print(
            f"  - {comment['author']}: "
            f"{comment['text']}"
        )


# ==========================================
# PART B: EMBEDDED DESIGN
# ==========================================
print("\n=== PART B: Embedded Design ===")

if "posts_embedded" in db.list_collection_names():
    db.drop_collection("posts_embedded")

embedded_col = db["posts_embedded"]


# ==========================================
# 4. MIGRATE REFERENCED DATA
# ==========================================
print("\nMigrating referenced data to embedded data...")

embedded_posts = []

for post in posts_col.find():

    # Find comments belonging to this post
    post_comments = list(
        comments_col.find(
            {"post_id": post["_id"]},
            {
                "_id": 0,
                "post_id": 0
            }
        )
    )

    # Create embedded document
    embedded_post = {
        "original_post_id": post["_id"],
        "title": post["title"],
        "author": post["author"],
        "tags": post["tags"],
        "created": post["created"],
        "comments": post_comments
    }

    # Preserve photo if present
    if "photo" in post:
        embedded_post["photo"] = post["photo"]

    # Preserve video if present
    if "video" in post:
        embedded_post["video"] = post["video"]

    embedded_posts.append(embedded_post)


embedded_col.insert_many(embedded_posts)

print(
    f"[OK] Migrated {len(embedded_posts)} "
    "posts to embedded design."
)


# ==========================================
# 5. DISPLAY EMBEDDED POST
# ==========================================
print("\n=== Embedded: Post + Comments ===")

embedded_post = embedded_col.find_one(
    {"title": "Why NoSQL Matters for AI"}
)

print(f"\nPost: {embedded_post['title']}")
print(f"Author: {embedded_post['author']}")
print(f"Number of comments: {len(embedded_post['comments'])}")

print("\nComments:")

for comment in embedded_post["comments"]:
    print(
        f"  - {comment['author']}: "
        f"{comment['text']}"
    )


# ==========================================
# 6. DISPLAY PHOTO AND VIDEO
# ==========================================
print("\n=== Media in Blog Post ===")

if "photo" in embedded_post:
    print(f"Photo: {embedded_post['photo']}")

if "video" in embedded_post:
    print(f"Video: {embedded_post['video']}")


# ==========================================
# 7. QUERY COMPARISON
# ==========================================
print("\n=== Query Comparison ===")

print("""
Referenced Design:
    1 query using aggregation with $lookup
    Posts and comments are stored separately.

Embedded Design:
    1 simple find() query
    Post and comments are stored in the same document.
""")

print("Referenced: 1 query + $lookup")
print("Embedded:   1 simple find()")


# ==========================================
# 8. DESIGN COMPARISON TABLE
# ==========================================
print("\n=== Design Comparison ===")

print(
    f"{'Aspect':<25}"
    f"{'Referenced':<30}"
    f"{'Embedded':<30}"
)

print("-" * 85)

print(
    f"{'Storage':<25}"
    f"{'Posts + Comments':<30}"
    f"{'Comments inside Post':<30}"
)

print(
    f"{'Query':<25}"
    f"{'$lookup required':<30}"
    f"{'Simple find()':<30}"
)

print(
    f"{'Data duplication':<25}"
    f"{'Low':<30}"
    f"{'Possible':<30}"
)

print(
    f"{'Read complexity':<25}"
    f"{'Higher':<30}"
    f"{'Lower':<30}"
)

print(
    f"{'Best for':<25}"
    f"{'Large/independent data':<30}"
    f"{'Small related data':<30}"
)


# ==========================================
# COMPLETE
# ==========================================
print("\n" + "=" * 60)
print("Lab 3.2 Complete")
print("=" * 60)

client.close()
