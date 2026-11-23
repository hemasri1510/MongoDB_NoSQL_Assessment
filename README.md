# MongoDB Advanced Operations & Polyglot Persistence Labs (Labs 7 to 17)

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
