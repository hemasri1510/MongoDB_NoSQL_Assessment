import time
from pymongo import MongoClient, ReadPreference
from pymongo.errors import PyMongoError

# Authenticated Replica Set URI for Lab 13.1 Docker Compose cluster
URI = (
    "mongodb://admin:ProdAdminPassword%402026@localhost:27017,localhost:27018,localhost:27019/"
    "?replicaSet=rs0&authSource=admin"
)

def run_authenticated_failover_reader():
    client = MongoClient(
        URI,
        read_preference=ReadPreference.PRIMARY_PREFERRED,
        retryReads=True,
        retryWrites=True,
        serverSelectionTimeoutMS=3000,
    )
    db = client["cti_prod_lab13"]
    coll = db["heartbeat_events"]

    # Seed initial document
    coll.update_one(
        {"_id": "cluster-health"},
        {"$set": {"status": "ACTIVE", "updated_at": time.time()}},
        upsert=True,
    )

    print("Connected to authenticated Replica Set rs0 (keyFile enabled).")
    print("Trigger failover in another terminal: docker exec -it prod-mongo1 mongosh -u admin -p ProdAdminPassword@2026 --eval 'rs.stepDown(60)'
")

    try:
        while True:
            try:
                doc = coll.find_one({"_id": "cluster-health"})
                hello = db.command("hello")
                me = hello.get("me", "unknown")
                role = "PRIMARY" if hello.get("isWritablePrimary") else "SECONDARY"
                print(f"[{time.strftime('%H:%M:%S')}] Read OK from {me} ({role}) -> {doc}")
            except PyMongoError as exc:
                print(f"[{time.strftime('%H:%M:%S')}] Election in progress / reconnecting... ({type(exc).__name__})")
            time.sleep(1)
    except KeyboardInterrupt:
        print("
Stopped reader.")
    finally:
        client.close()

if __name__ == "__main__":
    run_authenticated_failover_reader()
