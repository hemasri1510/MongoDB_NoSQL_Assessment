#!/usr/bin/env python3
import unittest
from lab18_1_comparative_benchmark import generate_threats, ComparativeBenchmarkSuite


class TestLab18ComparativeSystem(unittest.TestCase):
    def test_100k_ingestion_and_benchmarks(self):
        threats = generate_threats(count=100_000, seed=42)
        self.assertEqual(len(threats), 100_000)

        suite = ComparativeBenchmarkSuite(simulate=True)
        ingest = suite.ingest_all(threats)
        self.assertLess(ingest["cassandra_ingest_sec"], ingest["mongodb_ingest_sec"])
        self.assertLess(ingest["mongodb_ingest_sec"], ingest["neo4j_ingest_sec"])

        medians = suite.run_benchmarks(runs=10)
        # Q1: Cassandra fastest for threat history
        self.assertLess(medians["Q1_Threat_History_ms"]["Cassandra"], medians["Q1_Threat_History_ms"]["MongoDB"])
        # Q2: Neo4j fastest for 3-hop spread
        self.assertLess(medians["Q2_Target_Spread_3Hop_ms"]["Neo4j"], medians["Q2_Target_Spread_3Hop_ms"]["MongoDB"])
        # Q3: MongoDB fastest for daily volume analytics
        self.assertLess(medians["Q3_Daily_Volume_Analytics_ms"]["MongoDB"], medians["Q3_Daily_Volume_Analytics_ms"]["Cassandra"])
        # Q4: Cassandra fastest for real-time insert throughput
        self.assertLess(medians["Q4_Realtime_Insert_1k_Batch_ms"]["Cassandra"], medians["Q4_Realtime_Insert_1k_Batch_ms"]["MongoDB"])

    def test_activity_18_1_community_crossref(self):
        threats = generate_threats(count=20_000, seed=42)
        suite = ComparativeBenchmarkSuite(simulate=True)
        suite.ingest_all(threats)
        communities = suite.activity_18_1_community_detection_and_crossref()
        self.assertEqual(len(communities), 5)
        self.assertGreater(len(communities[0]["mongodb_cross_referenced_txns"]), 0)


if __name__ == "__main__":
    unittest.main()
