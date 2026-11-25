"""
Lab 17.1 & Activity 17.1: Minimal RAG Pipeline with Sliding-Window Chunking
and Multi-Turn MongoDB Conversation Memory
"""
import os
from datetime import datetime, timezone
from typing import List, Dict, Any
from pymongo import MongoClient
import voyageai
from openai import OpenAI

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/")
DB_NAME = os.getenv("DB_NAME", "rag_lab")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "<YOUR_VOYAGE_API_KEY>")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "<YOUR_OPENAI_API_KEY>")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
docs_col = db["documents"]
conv_col = db["conversations"]

vo_client = voyageai.Client(api_key=VOYAGE_API_KEY)
llm_client = OpenAI(api_key=OPENAI_API_KEY)


def chunk_document(text: str, source: str, window_words: int = 100, overlap_words: int = 20) -> List[Dict[str, Any]]:
    words = text.split()
    step = max(1, window_words - overlap_words)
    chunks = []
    for i in range(0, len(words), step):
        segment = " ".join(words[i : i + window_words])
        if segment.strip():
            chunks.append({"text": segment, "source": source, "chunk_index": len(chunks)})
    return chunks


def ingest_threat_intel_docs(documents: List[Dict[str, str]]) -> int:
    all_chunks = []
    for doc in documents:
        all_chunks.extend(chunk_document(doc["content"], doc["source"], window_words=100, overlap_words=20))

    texts = [c["text"] for c in all_chunks]
    embeddings = vo_client.embed(texts, model="voyage-3", input_type="document").embeddings

    records = []
    for chunk, emb in zip(all_chunks, embeddings):
        records.append({
            "text": chunk["text"],
            "source": chunk["source"],
            "chunk_index": chunk["chunk_index"],
            "embedding": emb
        })

    if records:
        docs_col.insert_many(records)
    return len(records)


def retrieve_top_chunks(query: str, num_candidates: int = 20, limit: int = 3) -> List[Dict[str, Any]]:
    query_embedding = vo_client.embed([query], model="voyage-3", input_type="query").embeddings[0]
    pipeline = [
        {
            "$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": num_candidates,
                "limit": limit
            }
        },
        {
            "$project": {
                "_id": 0,
                "text": 1,
                "source": 1,
                "score": {"$meta": "vectorSearchScore"}
            }
        }
    ]
    return list(docs_col.aggregate(pipeline))


def get_recent_exchanges(user_id: str, max_exchanges: int = 5) -> List[Dict[str, str]]:
    msgs = list(
        conv_col.find({"user_id": user_id}, {"_id": 0, "role": 1, "content": 1})
        .sort("timestamp", -1)
        .limit(max_exchanges * 2)
    )
    msgs.reverse()
    return msgs


def save_exchange(user_id: str, user_query: str, assistant_reply: str) -> None:
    now = datetime.now(timezone.utc)
    conv_col.insert_many([
        {"user_id": user_id, "timestamp": now, "role": "user", "content": user_query},
        {"user_id": user_id, "timestamp": now, "role": "assistant", "content": assistant_reply}
    ])


def answer_with_rag(user_id: str, query: str) -> Dict[str, Any]:
    retrieved = retrieve_top_chunks(query, num_candidates=20, limit=3)
    history = get_recent_exchanges(user_id, max_exchanges=5)

    history_block = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history]) or "None"
    context_block = "\n\n".join([f"[{i+1}] ({c['source']}): {c['text']}" for i, c in enumerate(retrieved)])

    prompt = (
        f"Conversation History (Last 5 Exchanges):\n{history_block}\n\n"
        f"Answer based on these documents:\n{context_block}\n\n"
        f"User Question: {query}"
    )

    completion = llm_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a Threat Intelligence analyst. Ground answers strictly in retrieved context."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )
    answer = completion.choices[0].message.content
    save_exchange(user_id, query, answer)
    return {"query": query, "retrieved_chunks": retrieved, "history_turns_used": len(history), "answer": answer}


if __name__ == "__main__":
    sample_docs = [
        {
            "source": "CTI-2026-01_LockBit_Tactics.txt",
            "content": "Threat actors deploying LockBit variants in 2026 rely heavily on exposed Citrix NetScaler gateways and compromised VPN credentials for initial access. Once inside the perimeter, operators execute Cobalt Strike beacons, harvest LSASS credentials, disable Windows Defender via Group Policy, and exfiltrate sensitive databases over Rclone prior to encrypting ESXi hypervisors."
        },
        {
            "source": "CTI-2026-02_APT29_Cloud_Token_Theft.txt",
            "content": "APT29 targets Microsoft Entra ID and cloud identity providers using residential proxy networks and MFA fatigue prompts. Following token replay, the group registers rogue OAuth applications with Mail.Read permissions to silently collect executive communications without triggering traditional endpoint antivirus alerts."
        },
        {
            "source": "CTI-2026-03_ZeroDay_Firewall_Mitigation.txt",
            "content": "Emergency mitigation guidance for perimeter firewall command injection vulnerabilities requires disabling external management interfaces immediately, enforcing hardware FIDO2 multi-factor authentication, rotating all active session tokens, and hunting for anomalous cron jobs or unauthorized SSH keys under /etc/ssh."
        }
    ]
    print("Sample chunk count:", sum(len(chunk_document(d["content"], d["source"])) for d in sample_docs))
