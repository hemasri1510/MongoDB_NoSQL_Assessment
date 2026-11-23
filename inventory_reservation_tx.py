import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymongo import MongoClient, WriteConcern, ReadPreference, ReturnDocument
from pymongo.errors import PyMongoError

URI = "mongodb://localhost:27017,localhost:27018,localhost:27019/?replicaSet=rs0"

def reset_inventory(client, product_id="PROD-YUBIKEY-5C", stock=5, price=4500.00):
    db = client["cti_inventory_lab"]
    db["inventory"].drop()
    db["orders"].drop()
    db["inventory"].insert_one({
        "product_id": product_id,
        "name": "YubiKey 5C NFC Hardware Token",
        "stock": stock,
        "price": price,
        "version": 1
    })
    db["orders"].create_index("order_id", unique=True)

def reserve_inventory_tx(client, product_id, customer_id, qty=1, max_retries=5):
    """
    Transactional reservation using ClientSession + start_transaction() and
    optimistic concurrency control via version field. Retries on write conflicts
    (TransientTransactionError / WriteConflict) up to max_retries times.
    """
    db = client["cti_inventory_lab"]
    inventory = db["inventory"]
    orders = db["orders"]

    for attempt in range(max_retries):
        try:
            with client.start_session() as session:
                with session.start_transaction(
                    write_concern=WriteConcern(w="majority"),
                    read_preference=ReadPreference.PRIMARY
                ):
                    prod = inventory.find_one({"product_id": product_id}, session=session)
                    if not prod:
                        return {"ok": False, "customer_id": customer_id, "reason": "PRODUCT_NOT_FOUND"}

                    current_stock = prod["stock"]
                    current_ver = prod["version"]
                    if current_stock < qty:
                        return {
                            "ok": False,
                            "customer_id": customer_id,
                            "reason": f"INSUFFICIENT_STOCK (available={current_stock}, req={qty})"
                        }

                    upd = inventory.update_one(
                        {"product_id": product_id, "stock": {"$gte": qty}, "version": current_ver},
                        {"$inc": {"stock": -qty, "version": 1}},
                        session=session
                    )
                    if upd.modified_count == 0:
                        raise PyMongoError("Optimistic concurrency version/stock conflict")

                    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
                    total = float(prod["price"]) * qty
                    orders.insert_one({
                        "order_id": order_id,
                        "customer_id": customer_id,
                        "items": [{"product_id": product_id, "qty": qty, "price": float(prod["price"])}],
                        "status": "PENDING",
                        "total": total,
                        "created_at": time.time()
                    }, session=session)

                    return {"ok": True, "order_id": order_id, "customer_id": customer_id, "attempts": attempt + 1}

        except PyMongoError as exc:
            is_conflict = exc.has_error_label("TransientTransactionError") or "WriteConflict" in str(exc) or "version/stock conflict" in str(exc)
            if is_conflict and attempt < max_retries - 1:
                time.sleep(0.02 * (attempt + 1))
                continue
            return {"ok": False, "customer_id": customer_id, "reason": f"TX_ABORTED: {type(exc).__name__}"}

    return {"ok": False, "customer_id": customer_id, "reason": "MAX_RETRIES_EXCEEDED"}

def reserve_non_tx_find_and_modify(client, product_id, customer_id, qty=1):
    """
    Non-transactional comparison using find_one_and_update (findAndModify)
    followed by a separate order insert (higher raw throughput, no multi-doc atomicity).
    """
    db = client["cti_inventory_lab"]
    updated = db["inventory"].find_one_and_update(
        {"product_id": product_id, "stock": {"$gte": qty}},
        {"$inc": {"stock": -qty, "version": 1}},
        return_document=ReturnDocument.AFTER
    )
    if not updated:
        return {"ok": False, "customer_id": customer_id, "reason": "OUT_OF_STOCK"}

    order_id = f"ORD-NTX-{uuid.uuid4().hex[:8].upper()}"
    db["orders"].insert_one({
        "order_id": order_id,
        "customer_id": customer_id,
        "items": [{"product_id": product_id, "qty": qty, "price": float(updated["price"])}],
        "status": "PENDING",
        "total": float(updated["price"]) * qty
    })
    return {"ok": True, "order_id": order_id, "customer_id": customer_id}

def run_contention_demo():
    client = MongoClient(URI, maxPoolSize=50)
    reset_inventory(client, stock=5)
    print("=== Contention Test: 8 Concurrent Threads Requesting 1 Unit Each (Stock = 5) ===")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(reserve_inventory_tx, client, "PROD-YUBIKEY-5C", f"CUST-{i:02d}", 1, 5)
            for i in range(8)
        ]
        results = [f.result() for f in as_completed(futures)]

    succeeded = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    final_prod = client["cti_inventory_lab"]["inventory"].find_one({"product_id": "PROD-YUBIKEY-5C"})
    order_cnt = client["cti_inventory_lab"]["orders"].count_documents({})
    print(f"Succeeded: {len(succeeded)} | Failed: {len(failed)} | Remaining Stock: {final_prod['stock']} | Orders Created: {order_cnt}")
    for r in failed:
        print("  Failed detail:", r)
    client.close()

def measure_throughput(concurrency_levels=(1, 4, 8, 16), total_requests=64):
    client = MongoClient(URI, maxPoolSize=64)
    print("
=== Throughput Benchmark Across Concurrency Levels ===")
    print(f"{'Threads':<10} | {'TX Success/s':<15} | {'Non-TX (findAndModify)/s':<25}")
    print("-" * 58)
    for threads in concurrency_levels:
        reset_inventory(client, stock=total_requests + 100)
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futs = [pool.submit(reserve_inventory_tx, client, "PROD-YUBIKEY-5C", f"U-{i}", 1, 5) for i in range(total_requests)]
            tx_ok = sum(1 for f in as_completed(futs) if f.result()["ok"])
        tx_tps = tx_ok / max(time.perf_counter() - t0, 1e-6)

        reset_inventory(client, stock=total_requests + 100)
        t1 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futs = [pool.submit(reserve_non_tx_find_and_modify, client, "PROD-YUBIKEY-5C", f"U-{i}", 1) for i in range(total_requests)]
            ntx_ok = sum(1 for f in as_completed(futs) if f.result()["ok"])
        ntx_tps = ntx_ok / max(time.perf_counter() - t1, 1e-6)

        print(f"{threads:<10} | {tx_tps:<15.2f} | {ntx_tps:<25.2f}")
    client.close()

if __name__ == "__main__":
    run_contention_demo()
    measure_throughput()
