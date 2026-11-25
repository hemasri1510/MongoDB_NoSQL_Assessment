# MongoDB Labs 1-18 + ThreatGuard AI Chatbot

## Repository structure
| Folder / file | Contents |
|---|---|
| `lab01` ... `lab06` | Labs 1-6 (MongoDB basics, queries, aggregation, joins) |
| `lab07_schema_monitoring` ... `lab18_capstone_comparative_system` | Labs 7-18 (see Lab Index below) |
| `lab11_sharding/` | also holds `init_sharded_cluster_lab11_2.js` (Lab 11.2 cluster setup, formerly at repo root) |
| `lab13_kubernetes_statefulset/` | also holds `mongo-headless-svc.yaml` and `mongo-statefulset.yaml` (formerly at repo root) |
| `LLM_Cti_Chatbot/` | **ThreatGuard AI threat-intelligence chatbot**: FastAPI backend (`main.py`), Gemini failover, background scanner, Streamlit app, Windows software scanner (`windows_agent.py`, `software_scanner.py`, `software_inventory.py`), Docker files. See `LLM_Cti_Chatbot/README_FINAL_ASSESSMENT.md` and `LLM_Cti_Chatbot/SOFTWARE_SCANNER_README.md` |
| `index.html` | Lab web portal with the floating ThreatGuard AI chat (talks to the backend at `http://localhost:8000`) |
| `LAB_REPORT_AND_GUIDE.md`, `REDESIGN_README.md`, `requirements.txt` | Reports, guides and shared Python requirements |

Secrets: `LLM_Cti_Chatbot/.env` is not stored in the repository. Copy `.env.example` to `.env` and add your own `GEMINI_API_KEY`.

---

# MongoDB Advanced Operations & Polyglot Persistence Labs (Labs 7 to 18)

Complete suite of operational scripts, architectures, security configurations, Kubernetes manifests, and polyglot persistence services.

## Lab Index
- **Lab 7:** Schema Analysis & WiredTiger Cache Monitoring (`lab7_queries.js`, `monitor_and_scrape.py`)
- **Lab 8:** Replica Set Deployment & Failover Reader (`lab8_replica_set_commands.js`, `replica_failover_reader.py`)
- **Lab 9:** Write Concern Benchmarks & Network Partitions (`write_concern_benchmark.py`, `network_partition_test.py`, `simulate_partition.sh`)
- **Lab 10:** Multi-Document ACID Transactions (`bank_transfer_tx.py`, `inventory_reservation_tx.py`)
- **Lab 11:** Sharding Strategies & Hotspot Experiments (`init_sharded_cluster.js`, `shard_hotspot_experiment.py`)
- **Lab 12:** RBAC Security, Read-Only Views & TLS (`lab12_rbac_and_views.js`, `setup_tls.sh`)
- **Lab 13:** Kubernetes StatefulSets & Failover (`k8s_mongodb_statefulset.yaml`, `lab13_failover_reader.py`)
- **Lab 14:** Monitoring Dashboard & Backup Verification (`mongodb_monitor_dashboard.py`, `backup_verify.py`)
- **Lab 15:** Multi-Tenant SaaS API & Migration Benchmark (`multi_tenant_api.py`, `tenant_migration_benchmark.py`)
- **Lab 16:** Polyglot Persistence & Hybrid Fraud Detection (`lab16_1_polyglot_enrollment_models.py`, `lab16_2_fraud_query_service.py`, `test_lab16.py`)


## Lab 17: Vector Search RAG & Index Tuning (`lab17_vector_search_rag/`)
- `lab17_1_rag_pipeline_with_memory.py`: Sliding-window 100-word chunking, Voyage AI embeddings, `$vectorSearch` (`numCandidates=20, limit=3`), and 5-exchange MongoDB conversation memory.
- `lab17_2_hnsw_vs_ivf_benchmark.py`: 5,000-vector 384-D HNSW vs. IVF latency and Recall@20 benchmark against brute-force cosine similarity.

## Lab 18: Capstone Comparative System & Analysis (`lab18_capstone_comparative_system/` — Lab 18.1 & Lab 18.2)
- `lab18_1_comparative_benchmark.py`: Ingests 100,000 multi-source threats into MongoDB, Cassandra, and Neo4j; runs 4 benchmark queries (10 runs each, reporting median latency); executes Activity 18.1 graph community detection in Neo4j cross-referenced with MongoDB transaction details.
- `LAB18_COMPARATIVE_ANALYSIS_REPORT.md` (Lab 18.2): Complete capstone comparative write-up covering Executive Summary, System Architecture Diagram, Query Performance Comparison, Data Modeling Analysis, Scalability Assessment (1M, 10M, 100M transactions), Ergonomics, and Final Recommendation.
- `test_lab18.py`: Automated verification test suite for Lab 18.1 and Activity 18.1.


## Final Assessment: LLM-Backed Threat Intelligence Chatbot & Autonomous Remediation (`final_assessment_llm_cti_chatbot/`)
Synthesizes **Exercises 1 through 18** into a full-stack AI Threat Intelligence application backed by **Hosted Google Gemini API** (`gemini-2.5-flash`, **not Ollama**) and **MongoDB**:
- `final_assessment_llm_cti_chatbot/main.py`: FastAPI `/chat`, `/threats`, `/vulnerabilities`, `/scan`, `/mitigate/{vuln_id}`, `/health`, and `/metrics` endpoints with MongoDB vector RAG (`knowledge`), persistent conversation memory (`sessions`), sliding-window rate limiting, and hosted Google Gemini API integration.
- `final_assessment_llm_cti_chatbot/ingest_threats.py`: Embedding and ingestion pipeline for **Anticipated**, **Active**, **Evolving**, and **Resolved** threats across **Linux**, **Windows**, **macOS**, **Android**, and **iOS**.
- `final_assessment_llm_cti_chatbot/background_scanner.py`: Autonomous background vulnerability scanner service that detects platform/project vulnerabilities and executes mitigation upon conversational confirmation (`CONFIRM FIX <VULN-ID>`).
- `final_assessment_llm_cti_chatbot/streamlit_app.py`: Streamlit chat frontend (`:8501`).
- `final_assessment_llm_cti_chatbot/Dockerfile` & `docker-compose.yml`: Full-stack containerized deployment (`MongoDB 7` + `FastAPI` + `Streamlit`).
