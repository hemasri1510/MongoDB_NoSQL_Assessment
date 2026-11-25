"""
Lab 15.2: Data Migration Between Tenant Isolation Strategies
1. Seeds 5 tenants with 100 tasks each (500 total documents) into shared `tasks` collection
   using schema: {id, tenant_id, title, status, priority, created_at}.
2. Migrates each distinct tenant_id into dedicated collection `tasks_tenant_{tenant_id}`
   using MongoDB aggregation `$match` + `$out` (and validates zero cross-tenant leakage).
3. Benchmarks query performance across both isolation models for:
   - Listing tasks for a tenant
   - Creating tasks
   - Cross-tenant analytics (aggregating status/priority counts across all 5 tenants)
"""

import argparse
import datetime
import random
import time
from pymongo import ASCENDING, DESCENDING, MongoClient


def seed_shared_tasks(db, num_tenants: int = 5, tasks_per_tenant: int = 100):
    """Seeds 5 tenants x 100 tasks = 500 documents into the shared `tasks` collection."""
    tasks_col = db["tasks"]
    tasks_col.drop()
    tasks_col.create_index([("tenant_id", ASCENDING), ("created_at", DESCENDING)])
    tasks_col.create_index([("tenant_id", ASCENDING), ("status", ASCENDING)])

    statuses = ["TODO", "IN_PROGRESS", "DONE"]
    priorities = ["LOW", "MEDIUM", "HIGH"]
    base_time = datetime.datetime(2026, 11, 1, 9, 0, 0, tzinfo=datetime.timezone.utc)

    docs = []
    tenants = [f"t{i:02d}" for i in range(1, num_tenants + 1)]
    for tid in tenants:
        for seq in range(1, tasks_per_tenant + 1):
            docs.append({
                "id": f"{tid}-TSK-{seq:04d}",
                "tenant_id": tid,
                "title": f"[{tid.upper()}] Security Remediation Task #{seq}",
                "status": random.choice(statuses),
                "priority": random.choice(priorities),
                "created_at": base_time + datetime.timedelta(minutes=seq),
            })

    tasks_col.insert_many(docs)
    print(f"[SEED OK] Inserted {tasks_col.count_documents({})} total documents across {len(tenants)} tenants in `tasks`.")
    return tenants


def migrate_to_collection_per_tenant(db):
    """
    Migrates each distinct tenant_id from shared `tasks` into `tasks_tenant_{tenant_id}`
    via aggregation pipeline `[$match, $out]`, creates per-collection indexes, and validates zero leakage.
    """
    tasks_col = db["tasks"]
    distinct_tenants = sorted(tasks_col.distinct("tenant_id"))
    print(f"
=== Migrating {len(distinct_tenants)} Tenants to Collection-Per-Tenant ===")

    t0 = time.perf_counter()
    for tid in distinct_tenants:
        target_coll_name = f"tasks_tenant_{tid}"
        db[target_coll_name].drop()
        pipeline = [
            {"$match": {"tenant_id": tid}},
            {"$out": target_coll_name},
        ]
        list(tasks_col.aggregate(pipeline))
        db[target_coll_name].create_index([("created_at", DESCENDING)])
        db[target_coll_name].create_index([("status", ASCENDING)])

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"[MIGRATION COMPLETE] Migrated {len(distinct_tenants)} tenants in {elapsed_ms:.2f} ms.")

    # Validation: verify exact counts and zero cross-tenant leakage
    print("
=== Post-Migration Integrity & Cross-Tenant Leakage Validation ===")
    total_migrated = 0
    for tid in distinct_tenants:
        target_coll = db[f"tasks_tenant_{tid}"]
        expected_count = tasks_col.count_documents({"tenant_id": tid})
        actual_count = target_coll.count_documents({})
        leaked_docs = target_coll.count_documents({"tenant_id": {"$ne": tid}})

        assert actual_count == expected_count, f"Count mismatch for {tid}: {actual_count} != {expected_count}"
        assert leaked_docs == 0, f"CRITICAL LEAKAGE in tasks_tenant_{tid}: {leaked_docs} foreign docs found!"
        total_migrated += actual_count
        print(f"  [PASS] tasks_tenant_{tid:<4} -> count={actual_count} (expected {expected_count}), foreign_docs={leaked_docs}")

    assert total_migrated == tasks_col.count_documents({})
    print(f"[ALL VALIDATIONS PASSED] {total_migrated} documents verified with 0 cross-tenant leakage.")
    return distinct_tenants


def benchmark_strategies(db, tenants: list, iterations: int = 200):
    """
    Compares latency (ms) between Document-Level Isolation (`tasks`) and
    Collection-Per-Tenant (`tasks_tenant_{tid}`) across:
      1. Listing tasks for a tenant
      2. Creating a task
      3. Cross-tenant analytics (aggregating counts across all 5 tenants)
    """
    print(f"
=== Performance Benchmark ({iterations} iterations per operation) ===")

    # 1. List tasks for a tenant
    t0 = time.perf_counter()
    for i in range(iterations):
        tid = tenants[i % len(tenants)]
        list(db["tasks"].find({"tenant_id": tid}).sort("created_at", DESCENDING).limit(50))
    doc_list_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

    t1 = time.perf_counter()
    for i in range(iterations):
        tid = tenants[i % len(tenants)]
        list(db[f"tasks_tenant_{tid}"].find({}).sort("created_at", DESCENDING).limit(50))
    coll_list_ms = ((time.perf_counter() - t1) / iterations) * 1000.0

    # 2. Create task
    now = datetime.datetime.now(datetime.timezone.utc)
    t2 = time.perf_counter()
    for i in range(iterations):
        tid = tenants[i % len(tenants)]
        db["tasks"].insert_one({
            "id": f"BENCH-DOC-{i}",
            "tenant_id": tid,
            "title": "Benchmark Task",
            "status": "TODO",
            "priority": "MEDIUM",
            "created_at": now,
        })
    doc_create_ms = ((time.perf_counter() - t2) / iterations) * 1000.0

    t3 = time.perf_counter()
    for i in range(iterations):
        tid = tenants[i % len(tenants)]
        db[f"tasks_tenant_{tid}"].insert_one({
            "id": f"BENCH-COLL-{i}",
            "tenant_id": tid,
            "title": "Benchmark Task",
            "status": "TODO",
            "priority": "MEDIUM",
            "created_at": now,
        })
    coll_create_ms = ((time.perf_counter() - t3) / iterations) * 1000.0

    # 3. Cross-tenant analytics (all 5 tenants)
    t4 = time.perf_counter()
    for _ in range(iterations):
        list(db["tasks"].aggregate([
            {"$group": {"_id": {"tenant": "$tenant_id", "status": "$status"}, "count": {"$sum": 1}}}
        ]))
    doc_analytics_ms = ((time.perf_counter() - t4) / iterations) * 1000.0

    # Collection-per-tenant cross-tenant analytics requires $unionWith across tenant collections
    union_stages = [
        {"$unionWith": {"coll": f"tasks_tenant_{tid}"}} for tid in tenants[1:]
    ] + [
        {"$group": {"_id": {"tenant": "$tenant_id", "status": "$status"}, "count": {"$sum": 1}}}
    ]
    base_coll = f"tasks_tenant_{tenants[0]}"
    t5 = time.perf_counter()
    for _ in range(iterations):
        list(db[base_coll].aggregate(union_stages))
    coll_analytics_ms = ((time.perf_counter() - t5) / iterations) * 1000.0

    print(f"{'Workload Operation':<30} | {'Document-Level (ms)':<20} | {'Collection-Per-Tenant (ms)':<26}")
    print("-" * 82)
    print(f"{'List Tenant Tasks (limit 50)':<30} | {doc_list_ms:>17.3f} ms | {coll_list_ms:>23.3f} ms")
    print(f"{'Create Single Task':<30} | {doc_create_ms:>17.3f} ms | {coll_create_ms:>23.3f} ms")
    print(f"{'Cross-Tenant Analytics (5x)':<30} | {doc_analytics_ms:>17.3f} ms | {coll_analytics_ms:>23.3f} ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 15.2: Multi-Tenant Isolation Migration & Benchmark")
    parser.add_argument("--uri", default="mongodb://localhost:27017/", help="MongoDB connection URI")
    parser.add_argument("--db", default="cti_tenant_migration", help="Database name")
    args = parser.parse_args()

    client = MongoClient(args.uri)
    database = client[args.db]
    seeded_tenants = seed_shared_tasks(database, num_tenants=5, tasks_per_tenant=100)
    migrated_tenants = migrate_to_collection_per_tenant(database)
    benchmark_strategies(database, migrated_tenants, iterations=200)
    client.close()
