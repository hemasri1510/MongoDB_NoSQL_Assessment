import time
import datetime
from pymongo import MongoClient, WriteConcern, ReadPreference
from pymongo.errors import PyMongoError, WriteConcernError, ServerSelectionTimeoutError

URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"

def get_replica_status(client):
    try:
        status = client.admin.command("replSetGetStatus")
        members_info = []
        primary = None
        for m in status.get("members", []):
            state = m.get("stateStr", "UNKNOWN")
            health = m.get("health", 0)
            name = m.get("name", "?")
            if state == "PRIMARY" and health == 1:
                primary = name
            members_info.append(f"{name}={state}(health:{int(health)})")
        return primary, " | ".join(members_info)
    except PyMongoError as e:
        return None, f"Status error: {type(e).__name__}"

def test_writes_during_partition(phase_label):
    """
    Tests both w:1 and w:majority inserts with a 3-second wtimeout
    to observe which operations succeed vs. time out during network partitions.
    """
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"\n--- [{ts}] Testing Writes: {phase_label} ---")
    client = MongoClient(URI, serverSelectionTimeoutMS=3000, socketTimeoutMS=4000)
    
    primary, summary = get_replica_status(client)
    print(f"Cluster View: {summary}")

    db = client["cti_partition_lab"]

    # 1. Test w:1 write
    try:
        t0 = time.perf_counter()
        coll_w1 = db.get_collection("partition_events", write_concern=WriteConcern(w=1, wtimeout=3000))
        coll_w1.insert_one({"phase": phase_label, "wc": "w:1", "ts": ts})
        dt = (time.perf_counter() - t0) * 1000
        print(f"  [SUCCESS] w:1 insert acknowledged in {dt:.1f} ms")
    except PyMongoError as e:
        print(f"  [FAILED/TIMEOUT] w:1 insert failed -> {type(e).__name__}: {e}")

    # 2. Test w:majority write
    try:
        t0 = time.perf_counter()
        coll_maj = db.get_collection("partition_events", write_concern=WriteConcern(w="majority", wtimeout=3000))
        coll_maj.insert_one({"phase": phase_label, "wc": "w:majority", "ts": ts})
        dt = (time.perf_counter() - t0) * 1000
        print(f"  [SUCCESS] w:majority insert acknowledged in {dt:.1f} ms")
    except WriteConcernError as e:
        print(f"  [TIMEOUT] w:majority timed out (cannot achieve quorum) -> {e.details}")
    except PyMongoError as e:
        print(f"  [FAILED/STEPPED DOWN] w:majority rejected (no writable primary) -> {type(e).__name__}")

    client.close()

if __name__ == "__main__":
    print("Lab 9.2 Network Partition Write Verifier")
    print("Run this script at each stage of the Docker network disconnect experiment:")
    print("  Stage 1: All 3 nodes healthy")
    print("  Stage 2: 1 Secondary disconnected (docker network disconnect <net> mongo3)")
    print("  Stage 3: 2 Secondaries disconnected (docker network disconnect <net> mongo2)")
    print("  Stage 4: Both Secondaries reconnected (docker network connect ...)\n")
    
    phase = input("Enter current experiment stage label (e.g., '1 Secondary Partitioned'): ").strip() or "Manual Check"
    test_writes_during_partition(phase)
