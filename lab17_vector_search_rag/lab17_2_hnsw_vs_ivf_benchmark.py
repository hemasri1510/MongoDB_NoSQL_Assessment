"""
Lab 17.2: Compare HNSW and IVF Vector Index Types on 5,000 384-D Embeddings
Measures Index Build Time, Query Latency across 100 queries, and Recall@20 vs Brute-Force Cosine Similarity.
"""
import os
import time
import numpy as np
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel
from sentence_transformers import SentenceTransformer

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/")
DB_NAME = os.getenv("DB_NAME", "vector_benchmark_lab")
COLLECTION_NAME = "embeddings_5k"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
col = db[COLLECTION_NAME]


def generate_and_insert_vectors(num_docs: int = 5000, batch_size: int = 500) -> np.ndarray:
    model = SentenceTransformer("all-MiniLM-L6-v2")
    corpus = [
        f"Threat advisory {i}: IOC pattern observed on subnet 10.{(i // 256) % 256}.{i % 256}.0/24 involving lateral movement and credential dumping."
        for i in range(num_docs)
    ]
    embeddings = model.encode(corpus, normalize_embeddings=True, show_progress_bar=False)

    col.delete_many({})
    for start in range(0, num_docs, batch_size):
        batch = [
            {"doc_id": idx, "text": corpus[idx], "embedding": embeddings[idx].tolist()}
            for idx in range(start, min(start + batch_size, num_docs))
        ]
        col.insert_many(batch)
    return embeddings


def create_vector_index(index_name: str, index_option: dict) -> float:
    model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": 384,
                    "similarity": "cosine",
                    "indexOption": index_option
                }
            ]
        },
        name=index_name,
        type="vectorSearch"
    )
    t0 = time.perf_counter()
    col.create_search_index(model=model)
    while True:
        indexes = list(col.list_search_indexes(name=index_name))
        if indexes and indexes[0].get("queryable") is True:
            break
        time.sleep(1.0)
    return round(time.perf_counter() - t0, 2)


def brute_force_top_k(matrix: np.ndarray, query_vec: np.ndarray, k: int = 20) -> set:
    scores = matrix @ query_vec
    top_indices = np.argpartition(scores, -k)[-k:]
    return set(int(idx) for idx in top_indices)


def benchmark_index(index_name: str, matrix: np.ndarray, queries: np.ndarray, num_candidates: int = 100, k: int = 20) -> dict:
    latencies_ms = []
    recalls = []

    for q in queries:
        gt_ids = brute_force_top_k(matrix, q, k=k)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": index_name,
                    "path": "embedding",
                    "queryVector": q.tolist(),
                    "numCandidates": num_candidates,
                    "limit": k
                }
            },
            {"$project": {"_id": 0, "doc_id": 1}}
        ]
        t0 = time.perf_counter()
        results = list(col.aggregate(pipeline))
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        retrieved_ids = {r["doc_id"] for r in results}
        recalls.append(len(retrieved_ids.intersection(gt_ids)) / float(k))

    return {
        "avg_latency_ms": round(float(np.mean(latencies_ms)), 2),
        "p95_latency_ms": round(float(np.percentile(latencies_ms, 95)), 2),
        "recall_pct": round(float(np.mean(recalls)) * 100.0, 2)
    }


if __name__ == "__main__":
    print("Ready to run 5,000-vector 384-D HNSW vs IVF benchmark against MongoDB Atlas.")
