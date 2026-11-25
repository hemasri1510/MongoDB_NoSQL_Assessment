import argparse
import datetime
import os
import shutil
import subprocess
import time
from pathlib import Path
from pymongo import MongoClient

try:
    import schedule
    HAS_SCHEDULE = True
except ImportError:
    HAS_SCHEDULE = False


def run_backup(uri: str, base_output_dir: str, db_name: str = None) -> Path:
    """Executes mongodump into a timestamped backup directory and returns its Path."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(base_output_dir) / f"backup_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["mongodump", "--uri", uri, "--out", str(backup_dir)]
    if db_name:
        cmd.extend(["--db", db_name])

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0

    if proc.returncode != 0:
        raise RuntimeError(f"mongodump failed (code {proc.returncode}): {proc.stderr}")

    print(f"[BACKUP OK] Created {backup_dir} in {elapsed:.3f}s")
    return backup_dir


def enforce_retention_policy(base_output_dir: str, retention_days: int = 7):
    """Scans output directory, sorts backup folders by creation time, and deletes folders older than 7 days."""
    root = Path(base_output_dir)
    if not root.exists():
        return

    cutoff_ts = time.time() - (retention_days * 86400)
    folders = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("backup_")]
    folders.sort(key=lambda p: p.stat().st_ctime)

    deleted_count = 0
    for folder in folders:
        if folder.stat().st_ctime < cutoff_ts:
            shutil.rmtree(folder)
            deleted_count += 1
            print(f"[RETENTION] Deleted expired backup folder (> {retention_days} days): {folder}")

    if deleted_count == 0:
        print(f"[RETENTION] Verified {len(folders)} backup folder(s); none older than {retention_days} days.")


def verify_point_in_time_restore(uri: str, base_output_dir: str, db_name: str = "cti_backup_lab"):
    """
    1. Inserts 1,000 test documents into backup_test with timestamps.
    2. Takes a mongodump backup.
    3. Corrupts/modifies all documents (verified: false, status: 'CORRUPTED').
    4. Runs mongorestore --drop --nsInclude=<db>.backup_test from the backup directory.
    5. Asserts document count and original field values are fully recovered.
    """
    client = MongoClient(uri)
    db = client[db_name]
    coll = db["backup_test"]
    coll.drop()

    print("=== Step 1: Inserting 1,000 Original Test Documents into backup_test ===")
    original_docs = [
        {
            "doc_id": i,
            "verified": True,
            "integrity_tag": f"ORIG-HASH-{i:05d}",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        for i in range(1000)
    ]
    coll.insert_many(original_docs)
    expected_map = {d["doc_id"]: d["integrity_tag"] for d in original_docs}

    print("=== Step 2: Taking Timestamped Backup via mongodump ===")
    backup_dir = run_backup(uri, base_output_dir, db_name=db_name)

    print("=== Step 3: Simulating Accidental Data Modification / Corruption ===")
    coll.update_many({}, {"$set": {"verified": False, "integrity_tag": "CORRUPTED"}})
    corrupted_count = coll.count_documents({"verified": False})
    print(f"Modified {corrupted_count} documents to verified=False.")

    print("=== Step 4: Restoring Original Data via mongorestore --drop ===")
    ns_pattern = f"{db_name}.backup_test"
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            "mongorestore",
            "--uri",
            uri,
            "--drop",
            f"--nsInclude={ns_pattern}",
            str(backup_dir),
        ],
        capture_output=True,
        text=True,
    )
    restore_sec = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError(f"mongorestore failed (code {proc.returncode}): {proc.stderr}")
    print(f"[RESTORE OK] Completed mongorestore in {restore_sec:.3f}s")

    print("=== Step 5: Asserting Restored Count & Field Values ===")
    restored_docs = list(coll.find({}, {"_id": 0}))
    try:
        assert len(restored_docs) == 1000, f"Count mismatch: expected 1000, got {len(restored_docs)}"
        for doc in restored_docs:
            did = doc["doc_id"]
            assert doc["verified"] is True, f"Field mismatch on doc_id={did}: verified={doc['verified']}"
            assert doc["integrity_tag"] == expected_map[did], f"Tag mismatch on doc_id={did}"
        print("[VERIFICATION PASSED] All 1,000 documents and original field values match pre-modification state!")
    except AssertionError as err:
        print(f"[ALERT - INTEGRITY MISMATCH] {err}")
        raise
    finally:
        client.close()


def run_rpo_rto_benchmarks(uri: str, base_output_dir: str, db_name: str = "cti_backup_bench"):
    """Benchmarks mongodump and mongorestore at 1,000, 10,000, and 100,000 documents (~1 KB/doc)."""
    client = MongoClient(uri)
    db = client[db_name]
    volumes = [1000, 10000, 100000]
    payload_pad = "B" * 920  # ~1 KB per BSON document

    print("\n=== Lab 14.2: Backup & Restore Benchmark (1K, 10K, 100K Docs) ===")
    print(f"{'Doc Count':<12} | {'Approx Size':<12} | {'Backup Time (s)':<16} | {'Restore Time (s)':<16} | {'Est. RTO (s)':<14}")
    print("-" * 80)

    for count in volumes:
        coll = db["bench_records"]
        coll.drop()
        batch_size = 5000
        for start in range(0, count, batch_size):
            end = min(count, start + batch_size)
            coll.insert_many([{"seq": i, "payload": payload_pad} for i in range(start, end)], ordered=False)

        stats = db.command("collStats", "bench_records")
        size_mb = stats.get("size", 0) / (1024 * 1024)

        b_dir = Path(base_output_dir) / f"bench_{count}"
        if b_dir.exists():
            shutil.rmtree(b_dir)
        b_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        subprocess.run(["mongodump", "--uri", uri, "--db", db_name, "--out", str(b_dir)], check=True, capture_output=True)
        backup_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        subprocess.run(["mongorestore", "--uri", uri, "--drop", "--db", db_name, str(b_dir / db_name)], check=True, capture_output=True)
        restore_s = time.perf_counter() - t1

        app_restart_s = 15.0  # Typical application/service reconnection + health-check warm-up
        rto_s = restore_s + app_restart_s
        print(f"{count:<12,} | {size_mb:>8.2f} MB  | {backup_s:>14.3f} s | {restore_s:>14.3f} s | {rto_s:>12.2f} s")

    print("\nRPO (Hourly Scheduled Backup): Maximum 1 hour (60 minutes) of data loss.")
    print("RTO Formula: RTO = mongorestore_time + application_restart_and_verification_time (~15s).")
    client.close()


def start_hourly_scheduler(uri: str, output_dir: str):
    if not HAS_SCHEDULE:
        raise RuntimeError("Install schedule library: pip install schedule")

    def job():
        run_backup(uri, output_dir)
        enforce_retention_policy(output_dir, retention_days=7)

    job()  # Run immediately on startup
    schedule.every().hour.do(job)
    print("Hourly backup scheduler running (7-day retention enabled). Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 14.2: Automated Backup, Retention & Restore Verification")
    parser.add_argument("--uri", default="mongodb://localhost:27017/", help="MongoDB connection URI")
    parser.add_argument("--out", default="./mongo_backups", help="Output directory for timestamped backups")
    parser.add_argument(
        "--mode",
        choices=["backup", "verify", "benchmark", "schedule"],
        default="verify",
        help="Execution mode: backup | verify | benchmark | schedule",
    )
    args = parser.parse_args()

    if args.mode == "backup":
        run_backup(args.uri, args.out)
        enforce_retention_policy(args.out, retention_days=7)
    elif args.mode == "verify":
        verify_point_in_time_restore(args.uri, args.out)
    elif args.mode == "benchmark":
        run_rpo_rto_benchmarks(args.uri, args.out)
    elif args.mode == "schedule":
        start_hourly_scheduler(args.uri, args.out)
