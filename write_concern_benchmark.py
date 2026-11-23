import time
from pymongo import MongoClient, WriteConcern
from pymongo.errors import PyMongoError

# Connect to the 3-node replica set from Chapter 8
URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"
NUM_DOCS = 1000

def benchmark_write_concern(w_val, j_val, label):
    """
    Inserts 1,000 small documents using the specified write concern (w and j)
    and returns total elapsed time (seconds) and average latency per write (ms).
    """
    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    db = client["cti_write_concern_lab"]
    # Recreate clean collection for fair benchmark comparison
    coll_name = f"bench_{label.replace(':', '_').replace(',', '_').replace('=', '_')}"
    db.drop_collection(coll_name)
    
    # Configure write concern (Note: w='all' translates to w=3 for a 3-node data replica set)
    wc = WriteConcern(w=w_val, j=j_val, wtimeout=10000)
    collection = db.get_collection(coll_name, write_concern=wc)

    # Warm-up connection & primary election check
    client.admin.command("ping")

    start_time = time.perf_counter()
    for i in range(NUM_DOCS):
        collection.insert_one({
            "seq": i,
            "write_concern": label,
            "journal": j_val,
            "payload": f"benchmark_event_{i}"
        })
    elapsed_sec = time.perf_counter() - start_time
    avg_latency_ms = (elapsed_sec / NUM_DOCS) * 1000.0
    client.close()
    return elapsed_sec, avg_latency_ms

if __name__ == "__main__":
    # Core Lab 9.1 scenarios + Activity 9.1 (j=False vs j=True matrix)
    # Note: In PyMongo, w="all" is represented by w=3 (total data-bearing nodes) or custom write concern mode.
    scenarios = [
        (1, False, "w:1, j:false"),
        (1, True,  "w:1, j:true"),
        ("majority", False, "w:majority, j:false"),
        ("majority", True,  "w:majority, j:true"),
        (3, False, "w:all (w:3), j:false"),
        (3, True,  "w:all (w:3), j:true"),
    ]

    print(f"Running Lab 9.1 & Activity 9.1 Write Concern Benchmark ({NUM_DOCS} single-doc inserts per run)...\n")
    print(f"{'Write Concern':<24} | {'Total Time (s)':<15} | {'Avg Latency (ms/op)':<20}")
    print("-" * 65)

    results = []
    for w_val, j_val, label in scenarios:
        try:
            total_s, avg_ms = benchmark_write_concern(w_val, j_val, label)
            results.append((label, total_s, avg_ms))
            print(f"{label:<24} | {total_s:<15.4f} | {avg_ms:<20.3f}")
        except PyMongoError as exc:
            print(f"{label:<24} | FAILED: {exc}")

    if len(results) >= 3:
        w1_time = results[0][1]
        wmaj_time = results[2][1]
        wall_time = results[4][1]
        print("\nSummary Ratios (j:false):")
        print(f"  w:majority / w:1 = {wmaj_time / w1_time:.2f}x slower")
        print(f"  w:all      / w:1 = {wall_time / w1_time:.2f}x slower")
