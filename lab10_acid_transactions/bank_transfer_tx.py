import time
import uuid
from pymongo import MongoClient, ReadPreference, WriteConcern
from pymongo.errors import PyMongoError, WriteConcernError

URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"

def seed_accounts(client):
    db = client["cti_banking_lab"]
    accounts = db["accounts"]
    history = db["transfer_history"]
    accounts.drop()
    history.drop()
    accounts.insert_many([
        {"account_id": "ACC-1001", "holder": "Aarav Sharma", "balance": 50000.00, "version": 1},
        {"account_id": "ACC-1002", "holder": "Priya Nair", "balance": 25000.00, "version": 1},
    ])
    print("Seeded accounts: ACC-1001 (Rs 50,000.00), ACC-1002 (Rs 25,000.00)")

def transfer_funds(client, from_acct, to_acct, amount, max_retries=5):
    """
    Executes a multi-document ACID transaction to debit from_acct and credit to_acct,
    recording a transfer_history document. Retries on TransientTransactionError
    with exponential backoff: time.sleep(0.1 * (2 ** attempt)).
    """
    if amount <= 0:
        raise ValueError("Transfer amount must be positive.")

    db = client["cti_banking_lab"]
    accounts = db["accounts"]
    history = db["transfer_history"]

    for attempt in range(max_retries):
        try:
            with client.start_session() as session:
                with session.start_transaction(
                    read_concern=None,
                    write_concern=WriteConcern(w="majority"),
                    read_preference=ReadPreference.PRIMARY
                ):
                    src_doc = accounts.find_one({"account_id": from_acct}, session=session)
                    if not src_doc:
                        raise LookupError(f"Source account {from_acct} not found.")
                    if src_doc["balance"] < amount:
                        raise ValueError(
                            f"Insufficient funds in {from_acct}: available Rs {src_doc['balance']:.2f}, requested Rs {amount:.2f}"
                        )

                    dst_doc = accounts.find_one({"account_id": to_acct}, session=session)
                    if not dst_doc:
                        raise LookupError(f"Destination account {to_acct} not found.")

                    debit_res = accounts.update_one(
                        {"account_id": from_acct, "balance": {"$gte": amount}},
                        {"$inc": {"balance": -amount, "version": 1}},
                        session=session
                    )
                    if debit_res.matched_count == 0:
                        raise ValueError(f"Debit condition failed (insufficient funds or concurrent change) for {from_acct}.")

                    credit_res = accounts.update_one(
                        {"account_id": to_acct},
                        {"$inc": {"balance": amount, "version": 1}},
                        session=session
                    )
                    if credit_res.matched_count == 0:
                        raise LookupError(f"Credit failed: destination {to_acct} not found.")

                    tx_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
                    history.insert_one({
                        "tx_id": tx_id,
                        "from_account": from_acct,
                        "to_account": to_acct,
                        "amount": amount,
                        "status": "COMPLETED",
                        "timestamp": time.time()
                    }, session=session)

                    return {"status": "COMMITTED", "tx_id": tx_id, "amount": amount, "attempt": attempt + 1}

        except PyMongoError as exc:
            if exc.has_error_label("TransientTransactionError") and attempt < max_retries - 1:
                backoff = 0.1 * (2 ** attempt)
                print(f"[Retry {attempt + 1}/{max_retries}] TransientTransactionError detected; sleeping {backoff:.2f}s...")
                time.sleep(backoff)
                continue
            raise

def print_balances(client, label):
    db = client["cti_banking_lab"]
    docs = list(db["accounts"].find({}, {"_id": 0}))
    print(f"
--- {label} ---")
    for d in docs:
        print(f"  {d['account_id']} ({d['holder']}): Rs {d['balance']:.2f} | v{d['version']}")
    print(f"  History count: {db['transfer_history'].count_documents({})}")

if __name__ == "__main__":
    client = MongoClient(URI, retryWrites=True)
    seed_accounts(client)
    print_balances(client, "Initial State")

    # 1. Valid transfer
    res1 = transfer_funds(client, "ACC-1001", "ACC-1002", 12500.00)
    print("
Valid Transfer Result:", res1)
    print_balances(client, "After Valid Transfer of Rs 12,500.00")

    # 2. Insufficient funds transfer -> must roll back atomically
    try:
        transfer_funds(client, "ACC-1002", "ACC-1001", 999999.00)
    except ValueError as err:
        print("
Expected Rollback Triggered:", err)

    print_balances(client, "After Failed Transfer (Balances & History Unchanged)")
    client.close()
