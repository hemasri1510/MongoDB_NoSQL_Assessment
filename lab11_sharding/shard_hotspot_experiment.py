import datetime
from bson import ObjectId
from pymongo import MongoClient

# Connects to mongos router exposed on localhost:27117 (from docker-compose.sharded.yml)
# Fallback: change port to 27017 if your mongos is mapped to 27017
MONGOS_URI = "mongodb://localhost:27117/"
NUM_DOCS = 10000

def run_hotspot_vs_hashed_experiment():
    client = MongoClient(MONGOS_URI, serverSelectionTimeoutMS=5000)
    db = client["cti_sharding_lab"]

    hotspot_coll = db["hotspot_data"]
    even_coll = db["even_data"]

    # Clear existing test documents while preserving shard metadata
    hotspot_coll.delete_many({})
    even_coll.delete_many({})

    print(f"Inserting {NUM_DOCS:,} documents into db.hotspot_data (shard key: {{ts: 1}})...")
    base_ts = datetime.datetime(2025, 1, 1, 0, 0, 0)
    hotspot_batch = [
        {
            "ts": base_ts + datetime.timedelta(seconds=i * 30),
            "event_type": "login_attempt",
            "seq": i
        }
        for i in range(NUM_DOCS)
    ]
    hotspot_coll.insert_many(hotspot_batch, ordered=False)

    print(f"Inserting {NUM_DOCS:,} documents into db.even_data (shard key: {{user_id: 'hashed'}})...")
    even_batch = [
        {
            "user_id": ObjectId(),
            "event_type": "login_attempt",
            "seq": i
        }
        for i in range(NUM_DOCS)
    ]
    even_coll.insert_many(even_batch, ordered=False)

    # Compare per-shard document counts and data sizes via collStats
    print("\n=== Per-Shard Distribution (collStats) ===")
    for coll_name in ["hotspot_data", "even_data"]:
        stats = db.command("collStats", coll_name)
        shards = stats.get("shards", {})
        print(f"\nCollection: {coll_name} (Total Docs: {stats.get('count', 0):,})")
        for shard_name, s_info in shards.items():
            cnt = s_info.get("count", 0)
            size_kb = s_info.get("size", 0) / 1024.0
            pct = (cnt / max(stats.get("count", 1), 1)) * 100
            print(f"  {shard_name:<18} -> Docs: {cnt:>6,} ({pct:5.1f}%) | Data Size: {size_kb:8.2f} KB")

    # Compare range query targeting vs scatter-gather using explain()
    print("\n=== Range Query Targeting vs Scatter-Gather (explain) ===")
    exp_hotspot = db.command(
        "explain",
        {
            "find": "hotspot_data",
            "filter": {
                "ts": {
                    "$gte": datetime.datetime(2025, 1, 1),
                    "$lt": datetime.datetime(2025, 12, 31)
                }
            }
        },
        verbosity="queryPlanner"
    )
    hs_shards = exp_hotspot.get("queryPlanner", {}).get("winningPlan", {}).get("shards", [])
    print(f"hotspot_data range query on {{ts}} -> Shards targeted: {len(hs_shards)} {[s.get('shardName') for s in hs_shards]} (Targeted Ranged Query)")

    min_oid = ObjectId.from_datetime(datetime.datetime(2025, 1, 1))
    max_oid = ObjectId.from_datetime(datetime.datetime(2026, 1, 1))
    exp_even = db.command(
        "explain",
        {
            "find": "even_data",
            "filter": {
                "user_id": {"$gte": min_oid, "$lt": max_oid}
            }
        },
        verbosity="queryPlanner"
    )
    ev_shards = exp_even.get("queryPlanner", {}).get("winningPlan", {}).get("shards", [])
    print(f"even_data range query on {{user_id}}  -> Shards targeted: {len(ev_shards)} {[s.get('shardName') for s in ev_shards]} (Scatter-Gather Broadcast)")

    client.close()

if __name__ == "__main__":
    run_hotspot_vs_hashed_experiment()
