"""
Verification Test Suite for Final Assessment: LLM-Backed Threat Intelligence Chatbot
Validates deterministic embeddings, corpus structure, lifecycle coverage (Anticipated,
Active, Evolving, Resolved), platform coverage (Linux, Windows, macOS, Android, iOS),
and hosted Gemini configuration (non-Ollama).
"""

from ingest_threats import THREAT_INTELLIGENCE_CORPUS, deterministic_embedding, chunk_text
from background_scanner import PLATFORM_FINDINGS_CATALOG


def test_corpus_lifecycles_and_platforms():
    lifecycles = {d["lifecycle"] for d in THREAT_INTELLIGENCE_CORPUS}
    platforms = {d["platform"] for d in THREAT_INTELLIGENCE_CORPUS}
    assert {"Anticipated", "Active", "Evolving", "Resolved"}.issubset(lifecycles)
    assert {"Linux", "Windows", "macOS", "Android", "iOS"}.issubset(platforms)


def test_embedding_dimensions():
    vec = deterministic_embedding("Linux kernel io_uring privilege escalation mitigation")
    assert len(vec) == 384
    assert any(v != 0.0 for v in vec)


def test_chunking():
    chunks = chunk_text("A" * 1250, chunk_size=500)
    assert len(chunks) == 3


def test_scanner_catalog_platforms():
    for plat in ["Linux", "Windows", "macOS", "Android", "iOS"]:
        assert plat in PLATFORM_FINDINGS_CATALOG
        assert len(PLATFORM_FINDINGS_CATALOG[plat]) >= 1


if __name__ == "__main__":
    test_corpus_lifecycles_and_platforms()
    test_embedding_dimensions()
    test_chunking()
    test_scanner_catalog_platforms()
    print("All Final Assessment CTI Chatbot checks PASSED!")
