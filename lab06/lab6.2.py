"""
Lab 6.2 - Compare Index Strategies

Objective:
Compare the performance impact of different index configurations.

Tasks:
1. Measure insert time with no indexes except _id.
2. Create 5 indexes and measure insert time again.
3. Calculate percentage slowdown.
4. Keep only the most critical index.
5. Run five representative queries and compare performance.
6. Analyze the trade-off between query performance and write throughput.
"""

import sys
import os
import time
import random

# Add project root to Python path
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from config.connection import get_db, reset_collection, banner


# ============================================================
# GENERATE DOCUMENTS
# ============================================================

def generate_documents(start, count):
    """
    Generate product/sensor documents.

    start:
        Starting document number.

    count:
        Number of documents to generate.
    """

    statuses = [
        "active",
        "inactive",
        "warning",
        "error"
    ]

    documents = []

    for i in range(start, start + count):

        document = {
            "sensor_id": f"SENSOR-{i % 1000:04d}",
            "reading": round(random.uniform(0, 1000), 2),
            "status": random.choice(statuses),
            "temperature": round(random.uniform(15, 45), 2),
            "humidity": round(random.uniform(20, 90), 2)
        }

        documents.append(document)

    return documents


# ============================================================
# BENCHMARK INSERTS
# ============================================================

def benchmark_inserts(collection, start, count, label):
    """
    Insert documents and measure elapsed time.
    """

    documents = generate_documents(
        start,
        count
    )

    print(
        f"\nInserting {count:,} documents..."
    )

    start_time = time.perf_counter()

    collection.insert_many(
        documents
    )

    elapsed = (
        time.perf_counter()
        - start_time
    )

    rate = count / elapsed

    print(
        f"  {label}: "
        f"{count:,} inserts in "
        f"{elapsed:.3f}s = "
        f"{rate:,.0f} docs/sec"
    )

    return elapsed


# ============================================================
# BENCHMARK READS
# ============================================================

def benchmark_reads(
    collection,
    query,
    iterations,
    label,
    projection=None,
    sort=None
):
    """
    Run a query multiple times and measure performance.
    """

    start_time = time.perf_counter()

    for _ in range(iterations):

        cursor = collection.find(
            query,
            projection
        )

        if sort is not None:

            cursor = cursor.sort(
                sort
            )

        list(
            cursor.limit(10)
        )

    elapsed = (
        time.perf_counter()
        - start_time
    )

    per_query = (
        elapsed / iterations
    ) * 1000

    print(
        f"  {label}: "
        f"{iterations:,} reads in "
        f"{elapsed:.3f}s = "
        f"{per_query:.3f} ms/query"
    )

    return elapsed


# ============================================================
# SHOW INDEXES
# ============================================================

def show_indexes(collection, label):

    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)

    for index in collection.list_indexes():

        print(
            f"  {index['name']:<30}"
            f" {dict(index['key'])}"
        )


# ============================================================
# RUN FIVE REPRESENTATIVE QUERIES
# ============================================================

def run_five_queries(collection, iterations=300):

    print("\n" + "=" * 70)
    print("FIVE REPRESENTATIVE QUERIES")
    print("=" * 70)

    # --------------------------------------------------------
    # Query 1
    # Find by sensor_id
    # --------------------------------------------------------

    query1 = {
        "sensor_id": "SENSOR-0050"
    }

    t1 = benchmark_reads(
        collection,
        query1,
        iterations,
        "Query 1 - sensor_id"
    )

    # --------------------------------------------------------
    # Query 2
    # Find readings above 500
    # --------------------------------------------------------

    query2 = {
        "reading": {
            "$gt": 500
        }
    }

    t2 = benchmark_reads(
        collection,
        query2,
        iterations,
        "Query 2 - reading > 500"
    )

    # --------------------------------------------------------
    # Query 3
    # Find active sensors
    # --------------------------------------------------------

    query3 = {
        "status": "active"
    }

    t3 = benchmark_reads(
        collection,
        query3,
        iterations,
        "Query 3 - status = active"
    )

    # --------------------------------------------------------
    # Query 4
    # Sensor + reading
    # --------------------------------------------------------

    query4 = {
        "sensor_id": "SENSOR-0050",
        "reading": {
            "$gt": 500
        }
    }

    t4 = benchmark_reads(
        collection,
        query4,
        iterations,
        "Query 4 - sensor + reading"
    )

    # --------------------------------------------------------
    # Query 5
    # Sensor + status
    # --------------------------------------------------------

    query5 = {
        "sensor_id": "SENSOR-0050",
        "status": "active"
    }

    t5 = benchmark_reads(
        collection,
        query5,
        iterations,
        "Query 5 - sensor + status"
    )

    return [
        t1,
        t2,
        t3,
        t4,
        t5
    ]


# ============================================================
# COMPARE READ PERFORMANCE
# ============================================================

def compare_reads(
    collection_before,
    collection_after,
    iterations=300
):
    """
    Compare read performance before and after
    reducing the number of indexes.
    """

    print("\n" + "=" * 70)
    print("READ PERFORMANCE COMPARISON")
    print("=" * 70)

    queries = [
        (
            "Query 1 - sensor_id",
            {
                "sensor_id": "SENSOR-0050"
            }
        ),
        (
            "Query 2 - reading > 500",
            {
                "reading": {
                    "$gt": 500
                }
            }
        ),
        (
            "Query 3 - status = active",
            {
                "status": "active"
            }
        ),
        (
            "Query 4 - sensor + reading",
            {
                "sensor_id": "SENSOR-0050",
                "reading": {
                    "$gt": 500
                }
            }
        ),
        (
            "Query 5 - sensor + status",
            {
                "sensor_id": "SENSOR-0050",
                "status": "active"
            }
        )
    ]

    results = []

    for label, query in queries:

        print(
            f"\n{label}"
        )

        # Before dropping indexes
        start = time.perf_counter()

        for _ in range(iterations):

            list(
                collection_before.find(
                    query
                ).limit(10)
            )

        before_time = (
            time.perf_counter()
            - start
        )

        # After dropping indexes
        start = time.perf_counter()

        for _ in range(iterations):

            list(
                collection_after.find(
                    query
                ).limit(10)
            )

        after_time = (
            time.perf_counter()
            - start
        )

        before_ms = (
            before_time / iterations
        ) * 1000

        after_ms = (
            after_time / iterations
        ) * 1000

        print(
            f"  Before: {before_ms:.3f} ms/query"
        )

        print(
            f"  After : {after_ms:.3f} ms/query"
        )

        if after_time > 0:

            change = (
                (after_time - before_time)
                / before_time
            ) * 100

            print(
                f"  Change: {change:+.1f}%"
            )

        results.append(
            (
                label,
                before_ms,
                after_ms
            )
        )

    return results


# ============================================================
# MAIN
# ============================================================

def main():

    banner(
        "Lab 6.2: Compare Index Strategies"
    )

    # --------------------------------------------------------
    # CONNECT TO DATABASE
    # --------------------------------------------------------

    print(
        "\nConnecting to MongoDB..."
    )

    db = get_db(
        "nosql_labs"
    )

    print(
        "[OK] Connected to MongoDB."
    )

    # ========================================================
    # TEST 1
    # INSERT WITH ZERO INDEXES
    # ========================================================

    banner(
        "TEST 1: INSERT PERFORMANCE"
    )

    print(
        "\nCreating collection with only _id index..."
    )

    col_0 = reset_collection(
        "nosql_labs",
        "perf_no_index"
    )

    show_indexes(
        col_0,
        "Indexes before insertion - 0 additional indexes"
    )

    t0 = benchmark_inserts(
        col_0,
        0,
        10000,
        "0 indexes"
    )

    # ========================================================
    # TEST 2
    # INSERT WITH FIVE INDEXES
    # ========================================================

    print(
        "\nCreating collection with 5 indexes..."
    )

    col_5 = reset_collection(
        "nosql_labs",
        "perf_5_indexes"
    )

    # --------------------------------------------------------
    # Create five indexes
    # --------------------------------------------------------

    col_5.create_index(
        [
            ("sensor_id", 1)
        ],
        name="idx_sensor"
    )

    col_5.create_index(
        [
            ("reading", 1)
        ],
        name="idx_reading"
    )

    col_5.create_index(
        [
            ("status", 1)
        ],
        name="idx_status"
    )

    col_5.create_index(
        [
            ("sensor_id", 1),
            ("reading", -1)
        ],
        name="idx_sensor_reading"
    )

    col_5.create_index(
        [
            ("sensor_id", 1),
            ("status", 1)
        ],
        name="idx_sensor_status"
    )

    show_indexes(
        col_5,
        "Indexes before insertion - 5 additional indexes"
    )

    t5 = benchmark_inserts(
        col_5,
        10000,
        10000,
        "5 indexes"
    )

    # ========================================================
    # CALCULATE WRITE SLOWDOWN
    # ========================================================

    print("\n" + "=" * 70)
    print("WRITE PERFORMANCE COMPARISON")
    print("=" * 70)

    print(
        f"\n0 indexes : {t0:.3f} seconds"
    )

    print(
        f"5 indexes : {t5:.3f} seconds"
    )

    if t0 > 0:

        slowdown = (
            (t5 - t0)
            / t0
        ) * 100

        print(
            f"\nWrite slowdown with 5 indexes: "
            f"{slowdown:+.2f}%"
        )

    # ========================================================
    # FIVE REPRESENTATIVE QUERIES
    # ========================================================

    print(
        "\nRunning five representative queries "
        "with all five indexes..."
    )

    five_index_results = run_five_queries(
        col_5,
        iterations=300
    )

    # ========================================================
    # DROP FOUR INDEXES
    # KEEP CRITICAL INDEX
    # ========================================================

    banner(
        "REDUCING INDEXES"
    )

    print(
        "\nKeeping only the most critical index:"
    )

    print(
        "  idx_sensor"
    )

    print(
        "\nDropping:"
    )

    print(
        "  idx_reading"
    )

    print(
        "  idx_status"
    )

    print(
        "  idx_sensor_reading"
    )

    print(
        "  idx_sensor_status"
    )

    col_5.drop_index(
        "idx_reading"
    )

    col_5.drop_index(
        "idx_status"
    )

    col_5.drop_index(
        "idx_sensor_reading"
    )

    col_5.drop_index(
        "idx_sensor_status"
    )

    show_indexes(
        col_5,
        "Indexes after dropping four indexes"
    )

    # ========================================================
    # RUN FIVE QUERIES AGAIN
    # ========================================================

    print(
        "\nRunning the five representative queries "
        "after reducing indexes..."
    )

    reduced_index_results = run_five_queries(
        col_5,
        iterations=300
    )

    # ========================================================
    # COMPARE QUERY RESULTS
    # ========================================================

    print("\n" + "=" * 70)
    print("QUERY PERFORMANCE COMPARISON")
    print("=" * 70)

    query_names = [
        "Query 1 - sensor_id",
        "Query 2 - reading > 500",
        "Query 3 - status = active",
        "Query 4 - sensor + reading",
        "Query 5 - sensor + status"
    ]

    for i in range(5):

        name = query_names[i]

        before = (
            five_index_results[i]
            * 1000
            / 300
        )

        after = (
            reduced_index_results[i]
            * 1000
            / 300
        )

        print(
            f"\n{name}"
        )

        print(
            f"  With 5 indexes : "
            f"{before:.3f} ms/query"
        )

        print(
            f"  With 1 index   : "
            f"{after:.3f} ms/query"
        )

        if before > 0:

            change = (
                (after - before)
                / before
            ) * 100

            print(
                f"  Change         : "
                f"{change:+.2f}%"
            )

    # ========================================================
    # ESR RULE
    # ========================================================

    banner(
        "ESR RULE: EQUALITY - SORT - RANGE"
    )

    print(
        """
The ESR Rule helps determine the order of fields
in a compound MongoDB index.

E = Equality
S = Sort
R = Range

Example query:

{
    "status": "active",
    "sensor_id": {"$regex": "^SENSOR-0"}
}

.sort({
    "reading": -1
})

A possible ESR-style index is:

{
    status: 1,
    reading: -1,
    sensor_id: 1
}

Equality:
    status = "active"

Sort:
    reading = descending

Range:
    sensor_id prefix search
"""
    )

    # ========================================================
    # FINAL ANALYSIS
    # ========================================================

    banner(
        "LAB 6.2 ANALYSIS"
    )

    print(
        """
Trade-off between Query Performance and Write Throughput
----------------------------------------------------------

1. Indexes improve read performance because MongoDB can
   locate matching documents without scanning the entire
   collection.

2. Every additional index requires maintenance whenever a
   document is inserted, updated, or deleted.

3. Therefore, collections with many indexes can have slower
   write performance.

4. With fewer indexes, write throughput can improve because
   MongoDB has fewer index structures to update.

5. Removing an index can make queries that depended on that
   index slower.

6. The goal is to create indexes for important and frequently
   executed queries without creating unnecessary indexes.

7. Index design should therefore balance:
      - Read performance
      - Write performance
      - Storage requirements
      - Query frequency
      - Maintenance cost

Conclusion:
Indexes improve query performance but introduce write and
storage overhead. A good MongoDB index strategy should keep
indexes that support important workloads while avoiding
unnecessary indexes.
"""
    )

    banner(
        "Lab 6.2 Complete"
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()