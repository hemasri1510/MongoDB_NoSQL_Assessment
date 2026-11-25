import time
from pymongo import MongoClient, ReadPreference
from pymongo.errors import PyMongoError

# Connect to the 3-node replica set with automatic failover & retryReads
URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"

client = MongoClient(
    URI,
    read_preference=ReadPreference.PRIMARY_PREFERRED,
    retryReads=True,
    serverSelectionTimeoutMS=3000
)

db = client["cti_replicated"]
collection = db["threat_alerts"]

print("Connected to Replica Set rs0. Starting continuous read loop...")
print("Run 'rs.stepDown(60)' on the PRIMARY in mongosh to observe automatic reconnection!\n")

while True:
    try:
        # Execute a read and inspect which replica set member served it
        doc = collection.find_one({}, {"_id": 0})
        hello = db.command("hello")
        connected_host = hello.get("me", "unknown")
        is_primary = hello.get("isWritablePrimary", False)
        role = "PRIMARY" if is_primary else "SECONDARY"

        print(f"[{time.strftime('%H:%M:%S')}] Served by: {connected_host} ({role}) | Sample Doc: {doc}")
    except PyMongoError as err:
        print(f"[{time.strftime('%H:%M:%S')}] Election in progress / reconnecting... ({type(err).__name__})")

    time.sleep(1)
