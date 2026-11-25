"""
Lab 6.1 - Index Design for a Query Workload

Objective:
Design and validate indexes for a realistic query workload.

Tasks:
1. Create products collection with 50,000 documents.
2. Create five representative queries.
3. Run explain() before indexes.
4. Create indexes.
5. Run explain() after indexes.
6. Compare query performance.
"""

import sys
import os
import random

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from config.connection import get_db, reset_collection, banner


# ============================================================
# 1. GENERATE 50,000 PRODUCTS
# ============================================================

def generate_products(count=50000):

    categories = [
        "Electronics",
        "Clothing",
        "Books",
        "Home",
        "Sports"
    ]

    available_tags = [
        "new",
        "sale",
        "popular",
        "premium",
        "budget",
        "wireless",
        "online",
        "trending",
        "discount",
        "best-seller"
    ]

    products = []

    for i in range(count):

        product = {
            "name": f"Product {i}",
            "category": random.choice(categories),
            "price": round(random.uniform(100, 50000), 2),
            "rating": round(random.uniform(1, 5), 1),
            "stock": random.randint(0, 1000),
            "tags": random.sample(
                available_tags,
                random.randint(1, 4)
            )
        }

        products.append(product)

    return products


# ============================================================
# 2. FIND INDEX NAME FROM QUERY PLAN
# ============================================================

def find_index_name(obj):

    if isinstance(obj, dict):

        if "indexName" in obj:
            return obj["indexName"]

        for value in obj.values():

            result = find_index_name(value)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = find_index_name(item)

            if result:
                return result

    return None


# ============================================================
# 3. CHECK WHETHER IXSCAN OR COLLSCAN EXISTS
# ============================================================

def find_stage(obj):

    if isinstance(obj, dict):

        if "stage" in obj:
            stage = obj["stage"]

            if stage in [
                "IXSCAN",
                "COLLSCAN",
                "FETCH",
                "SORT",
                "TEXT_MATCH"
            ]:
                return stage

        for value in obj.values():

            result = find_stage(value)

            if result:
                return result

    elif isinstance(obj, list):

        for item in obj:

            result = find_stage(item)

            if result:
                return result

    return None


# ============================================================
# 4. RUN EXPLAIN
# ============================================================

def run_explain(collection, query, label, sort=None):

    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)

    # MongoDB explain command
    find_command = {
        "find": collection.name,
        "filter": query
    }

    # Add sort if required
    if sort is not None:
        find_command["sort"] = dict(sort)

    # Run explain directly through MongoDB
    plan = collection.database.command(
        "explain",
        find_command,
        verbosity="executionStats"
    )

    # --------------------------------------------------------
    # Query planner
    # --------------------------------------------------------

    query_planner = plan.get(
        "queryPlanner",
        {}
    )

    winning_plan = query_planner.get(
        "winningPlan",
        {}
    )

    # --------------------------------------------------------
    # Execution statistics
    # --------------------------------------------------------

    execution_stats = plan.get(
        "executionStats",
        {}
    )

    # --------------------------------------------------------
    # Stage
    # --------------------------------------------------------

    stage = find_stage(winning_plan)

    if stage is None:
        stage = "UNKNOWN"

    print("\nWinning Stage:", stage)

    # --------------------------------------------------------
    # Index usage
    # --------------------------------------------------------

    index_name = find_index_name(winning_plan)

    plan_string = str(winning_plan)

    if "IXSCAN" in plan_string:

        print("Index Used: YES")

        if index_name:
            print("Index Name:", index_name)

    elif "COLLSCAN" in plan_string:

        print("Index Used: NO")
        print("Operation: Collection Scan")

    else:

        print("Index Used: UNKNOWN")

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    docs_examined = execution_stats.get(
        "totalDocsExamined",
        "N/A"
    )

    keys_examined = execution_stats.get(
        "totalKeysExamined",
        "N/A"
    )

    returned = execution_stats.get(
        "nReturned",
        "N/A"
    )

    execution_time = execution_stats.get(
        "executionTimeMillis",
        "N/A"
    )

    print(
        "Documents Examined:",
        docs_examined
    )

    print(
        "Keys Examined:",
        keys_examined
    )

    print(
        "Documents Returned:",
        returned
    )

    print(
        "Execution Time (ms):",
        execution_time
    )

    return plan


# ============================================================
# 5. DISPLAY INDEXES
# ============================================================

def display_indexes(collection):

    print("\n" + "=" * 70)
    print("INDEX SUMMARY")
    print("=" * 70)

    for index in collection.list_indexes():

        print(
            f"Name: {index['name']:<25} "
            f"Keys: {dict(index['key'])}"
        )


# ============================================================
# 6. MAIN PROGRAM
# ============================================================

def main():

    # --------------------------------------------------------
    # Banner
    # --------------------------------------------------------

    banner(
        "Lab 6.1: Index Design for a Query Workload"
    )

    # --------------------------------------------------------
    # Connect to MongoDB
    # --------------------------------------------------------

    print("\nConnecting to MongoDB...")

    db = get_db(
        "nosql_labs"
    )

    print(
        "[OK] Connected to MongoDB."
    )

    # --------------------------------------------------------
    # Reset products collection
    # --------------------------------------------------------

    col = reset_collection(
        "nosql_labs",
        "products"
    )

    print(
        "[OK] Products collection reset."
    )

    # --------------------------------------------------------
    # Generate products
    # --------------------------------------------------------

    print(
        "\nGenerating 50,000 product documents..."
    )

    products = generate_products(
        50000
    )

    print(
        "Inserting documents..."
    )

    col.insert_many(
        products
    )

    total = col.count_documents({})

    print(
        f"[OK] Inserted {total} product documents."
    )

    # ========================================================
    # FIVE QUERIES
    # ========================================================

    # --------------------------------------------------------
    # QUERY 1
    # Category + price sorting
    # --------------------------------------------------------

    query1 = {
        "category": "Electronics"
    }

    sort1 = [
        ("price", 1)
    ]

    # --------------------------------------------------------
    # QUERY 2
    # Price range
    # --------------------------------------------------------

    query2 = {
        "price": {
            "$gte": 1000,
            "$lte": 10000
        }
    }

    # --------------------------------------------------------
    # QUERY 3
    # Tag search
    # --------------------------------------------------------

    query3 = {
        "tags": "wireless"
    }

    # --------------------------------------------------------
    # QUERY 4
    # Name search
    # --------------------------------------------------------

    query4 = {
        "name": {
            "$regex": "^Product 1"
        }
    }

    # --------------------------------------------------------
    # QUERY 5
    # Category + rating
    # --------------------------------------------------------

    query5 = {
        "category": "Clothing",
        "rating": {
            "$gte": 4
        }
    }

    # ========================================================
    # EXPLAIN WITHOUT INDEXES
    # ========================================================

    banner(
        "EXPLAIN: WITHOUT INDEXES"
    )

    print(
        "\nRunning Query 1..."
    )

    run_explain(
        col,
        query1,
        "QUERY 1: Electronics products sorted by price",
        sort1
    )

    print(
        "\nRunning Query 2..."
    )

    run_explain(
        col,
        query2,
        "QUERY 2: Price between 1,000 and 10,000"
    )

    print(
        "\nRunning Query 3..."
    )

    run_explain(
        col,
        query3,
        "QUERY 3: Products with wireless tag"
    )

    print(
        "\nRunning Query 4..."
    )

    run_explain(
        col,
        query4,
        "QUERY 4: Search product name"
    )

    print(
        "\nRunning Query 5..."
    )

    run_explain(
        col,
        query5,
        "QUERY 5: Clothing products with rating >= 4"
    )

    # ========================================================
    # CREATE INDEXES
    # ========================================================

    banner(
        "CREATING INDEXES"
    )

    # --------------------------------------------------------
    # Index 1
    # Category + price
    # --------------------------------------------------------

    result = col.create_index(
        [
            ("category", 1),
            ("price", 1)
        ],
        name="idx_category_price"
    )

    print(
        "[OK] Created:",
        result
    )

    # --------------------------------------------------------
    # Index 2
    # Price
    # --------------------------------------------------------

    result = col.create_index(
        [
            ("price", 1)
        ],
        name="idx_price"
    )

    print(
        "[OK] Created:",
        result
    )

    # --------------------------------------------------------
    # Index 3
    # Tags
    # --------------------------------------------------------

    result = col.create_index(
        [
            ("tags", 1)
        ],
        name="idx_tags"
    )

    print(
        "[OK] Created:",
        result
    )

    # --------------------------------------------------------
    # Index 4
    # Name
    # --------------------------------------------------------

    result = col.create_index(
        [
            ("name", 1)
        ],
        name="idx_name"
    )

    print(
        "[OK] Created:",
        result
    )

    # --------------------------------------------------------
    # Index 5
    # Category + rating
    # --------------------------------------------------------

    result = col.create_index(
        [
            ("category", 1),
            ("rating", -1)
        ],
        name="idx_category_rating"
    )

    print(
        "[OK] Created:",
        result
    )

    print(
        "\n[OK] All indexes created successfully."
    )

    # ========================================================
    # DISPLAY INDEXES
    # ========================================================

    display_indexes(
        col
    )

    # ========================================================
    # EXPLAIN WITH INDEXES
    # ========================================================

    banner(
        "EXPLAIN: WITH INDEXES"
    )

    print(
        "\nRunning Query 1..."
    )

    run_explain(
        col,
        query1,
        "QUERY 1: Electronics products sorted by price",
        sort1
    )

    print(
        "\nRunning Query 2..."
    )

    run_explain(
        col,
        query2,
        "QUERY 2: Price between 1,000 and 10,000"
    )

    print(
        "\nRunning Query 3..."
    )

    run_explain(
        col,
        query3,
        "QUERY 3: Products with wireless tag"
    )

    print(
        "\nRunning Query 4..."
    )

    run_explain(
        col,
        query4,
        "QUERY 4: Search product name"
    )

    print(
        "\nRunning Query 5..."
    )

    run_explain(
        col,
        query5,
        "QUERY 5: Clothing products with rating >= 4"
    )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    banner(
        "LAB 6.1 COMPLETE"
    )

    print("""
Index Design Summary
--------------------

Query 1:
Find Electronics products and sort by price
Index: idx_category_price

Query 2:
Find products between price 1,000 and 10,000
Index: idx_price

Query 3:
Find products containing wireless tag
Index: idx_tags

Query 4:
Search product name
Index: idx_name

Query 5:
Find Clothing products with rating >= 4
Index: idx_category_rating

Before indexes:
MongoDB generally uses COLLSCAN and examines many documents.

After indexes:
MongoDB can use IXSCAN and examine index keys instead of
scanning the complete collection for suitable queries.

Use the values of:
- Documents Examined
- Keys Examined
- Execution Time

to compare the query performance.
""")

    print(
        "=" * 70
    )
    print(
        "Lab 6.1 finished successfully!"
    )
    print(
        "=" * 70
    )


# ============================================================
# START PROGRAM
# ============================================================

if __name__ == "__main__":
    main()