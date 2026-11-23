import time
import random
import urllib.request
import re
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
db = client["cti_working_set"]
collection = db["security_reports"]
REPO_README_URL = "https://raw.githubusercontent.com/jacobdjwilson/awesome-annual-security-reports/main/README.md"

def sync_new_reports():
    try:
        req = urllib.request.urlopen(REPO_README_URL, timeout=5)
        markdown = req.read().decode("utf-8")
        links = re.findall(r'\[([^\]]+)\]\((https?://[^\)]+)\)', markdown)
        new_count = 0
        for title, url in links[:25]:
            if not collection.find_one({"source_url": url}):
                collection.insert_one({"title": title, "source_url": url, "ingested_at": time.time(), "payload": "R" * 1850})
                new_count += 1
        return new_count
    except Exception:
        return 0

def get_wt_metrics():
    cache = db.command("serverStatus")["wiredTiger"]["cache"]
    req = cache["pages requested from the cache"]
    read_in = cache["pages read into cache"]
    hit_ratio = ((req - read_in) / req) * 100 if req > 0 else 100.0
    used_mb = cache["bytes currently in the cache"] / (1024 * 1024)
    max_mb = cache["maximum bytes configured"] / (1024 * 1024)
    return round(hit_ratio, 2), round(used_mb, 2), round(max_mb, 2)

if __name__ == "__main__":
    print("Starting WiredTiger Cache & Threat Report Monitor (10s interval)...")
    while True:
        added = sync_new_reports()
        total_docs = collection.estimated_document_count()
        if total_docs > 0:
            for _ in range(200):
                collection.find_one({"report_id": f"ASR-2026-{random.randint(0, total_docs - 1)}"})
        hit_ratio, used_mb, max_mb = get_wt_metrics()
        print(f"[{time.strftime('%H:%M:%S')}] New Reports Added: {added} | Cache Usage: {used_mb}/{max_mb} MB | Hit Ratio: {hit_ratio}%")
        time.sleep(10)
