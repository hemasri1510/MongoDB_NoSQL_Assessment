"""
Comprehensive Test Suite for ThreatGuard AI Multi-Model Failover System
Simulates HTTP 503, 429, 404, 401/403, and verified model fallback sequences.

Guarantees:
1. Try primary model first.
2. HTTP 503, 429, 5xx retry with exponential backoff; automatically switch to next free model.
3. HTTP 404 or unsupported model marked UNAVAILABLE and skipped immediately.
4. HTTP 401/403 authentication halts immediately without repeated model cycling.
5. Strict free-tier enforcement (blocks paid/pro/ultra models).
6. Model Health Manager tracking cooldown periods and auto-recovery.
7. Preservation of conversation history, system prompt, and RAG context across failover.
8. Friendly user message when all free models fail.
9. Successful model name logged and returned.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional
import pytest
import requests

from gemini_failover import (
    AllModelsFailedError,
    DEFAULT_FREE_MODELS,
    FREE_TIER_MODELS_WHITELIST,
    GeminiAuthError,
    GeminiFailoverEngine,
    GeminiFailoverError,
    GeminiModelDiscovery,
    ModelHealthManager,
    ModelStatus,
    is_free_tier_model,
    redact_api_key,
)
import main


class MockResponse:
    def __init__(self, status_code: int = 200, json_data: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}
        self.text = json.dumps(self._json_data)

    def json(self):
        return self._json_data


def make_ok_response(text: str = "Threat analysis complete.") -> MockResponse:
    return MockResponse(
        status_code=200,
        json_data={
            "candidates": [
                {
                    "content": {"parts": [{"text": text}]},
                    "finishReason": "STOP",
                }
            ]
        },
    )


def make_err_response(
    status: int, message: str = "Error", api_status: str = "UNAVAILABLE", retry_delay: Optional[str] = None
) -> MockResponse:
    body: Dict[str, Any] = {
        "error": {
            "code": status,
            "message": message,
            "status": api_status,
        }
    }
    if retry_delay:
        body["error"]["details"] = [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        ]
    headers = {}
    if retry_delay and retry_delay.endswith("s"):
        headers["Retry-After"] = retry_delay[:-1]
    return MockResponse(status_code=status, json_data=body, headers=headers)


@pytest.fixture(autouse=True)
def reset_system(monkeypatch):
    main.health_manager.reset_all()
    main._MODEL_CACHE.update({"models": None, "expires": 0.0})
    # Default hermetic mock for models.list GET requests
    def default_get(url, params=None, headers=None, timeout=None):
        return MockResponse(200, {
            "models": [
                {"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
                for m in ["gemini-3.1-flash-lite", "gemini-4-hydrogen", "gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash-lite"]
            ]
        })
    monkeypatch.setattr(requests, "get", default_get)
    monkeypatch.setattr(time, "sleep", lambda s: None)


def test_free_tier_whitelist_rejects_paid_models():
    """Verify that paid/pro/ultra models are strictly forbidden, and verified free models are permitted."""
    assert is_free_tier_model("gemini-3.1-flash-lite") is True
    assert is_free_tier_model("gemini-4-hydrogen") is True
    assert is_free_tier_model("gemini-3.5-flash-lite") is True
    assert is_free_tier_model("gemini-flash-lite-latest") is True
    assert is_free_tier_model("gemini-2.5-flash-lite") is True
    assert is_free_tier_model("gemini-2.5-flash") is True

    # Strictly forbidden paid / pro / ultra models
    assert is_free_tier_model("gemini-2.5-pro") is False
    assert is_free_tier_model("gemini-3.1-pro-preview") is False
    assert is_free_tier_model("gemini-pro-latest") is False
    assert is_free_tier_model("gemini-ultra") is False
    assert is_free_tier_model("deep-research-preview-04-2026") is False
    assert is_free_tier_model("lyria-3.5") is False


def test_model_discovery_filters_paid_and_unsupported_models(monkeypatch):
    """Verify Gemini API model discovery retains only free-tier models with generateContent support."""
    discovery = GeminiModelDiscovery(cache_ttl_seconds=300)

    fake_api_models = [
        {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4-hydrogen", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},  # PAID: MUST BE FILTERED
        {"name": "models/gemini-ultra", "supportedGenerationMethods": ["generateContent"]},    # PAID: MUST BE FILTERED
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},  # NO generateContent
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return MockResponse(200, {"models": fake_api_models})

    monkeypatch.setattr(requests, "get", fake_get)

    free_discovered = discovery.discover_free_models("fake-key")
    assert free_discovered is not None
    assert "gemini-3.1-flash-lite" in free_discovered
    assert "gemini-4-hydrogen" in free_discovered
    assert "gemini-2.5-pro" not in free_discovered
    assert "gemini-ultra" not in free_discovered
    assert "gemini-embedding-001" not in free_discovered


def test_503_automatic_failover_sequence(monkeypatch):
    """Simulate HTTP 503 on primary model -> retries with backoff -> automatic failover to gemini-4-hydrogen."""
    calls = []
    sleeps = []

    script = {
        "gemini-3.1-flash-lite": [
            make_err_response(503, "High demand", "UNAVAILABLE"),
            make_err_response(503, "High demand", "UNAVAILABLE"),
            make_err_response(503, "High demand", "UNAVAILABLE"),
        ],
        "gemini-4-hydrogen": [
            make_ok_response("Remediation procedure for CVE-2024-1086: Update Linux kernel to 6.8+"),
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return script[model].pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0.0)

    # Mock discovery to confirm both models
    monkeypatch.setattr(
        main.model_discovery,
        "discover_free_models",
        lambda key: {"gemini-3.1-flash-lite", "gemini-4-hydrogen"},
    )

    engine = GeminiFailoverEngine(
        api_key="fake-test-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
        max_attempts_per_model=3,
        retry_base_delay=1.0,
    )

    payload = {"contents": [{"role": "user", "parts": [{"text": "Explain Linux vulnerability CVE-2024-1086"}]}]}
    answer, model_used, is_fallback = engine.generate_with_failover(payload)

    # Primary was tried 3 times with exponential backoff (1s, 2s)
    assert calls == ["gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "gemini-4-hydrogen"]
    assert sleeps == [1.0, 2.0]
    # Succeeded on fallback model
    assert model_used == "gemini-4-hydrogen"
    assert is_fallback is True
    assert "CVE-2024-1086" in answer

    # Verify primary model health manager record
    summary = main.health_manager.get_health_summary()
    assert summary["gemini-3.1-flash-lite"]["status"] == "COOLDOWN"
    assert summary["gemini-3.1-flash-lite"]["consecutive_failures"] == 1
    assert summary["gemini-4-hydrogen"]["status"] == "HEALTHY"


def test_429_quota_exhaustion_failover(monkeypatch):
    """Simulate HTTP 429 quota exhaustion on primary model -> automatic failover to next free model."""
    calls = []
    sleeps = []

    script = {
        "gemini-3.1-flash-lite": [
            make_err_response(429, "Quota exceeded", "RESOURCE_EXHAUSTED", retry_delay="2s"),
            make_err_response(429, "Quota exceeded", "RESOURCE_EXHAUSTED", retry_delay="2s"),
            make_err_response(429, "Quota exceeded", "RESOURCE_EXHAUSTED", retry_delay="2s"),
        ],
        "gemini-4-hydrogen": [
            make_ok_response("Hydrogen model answering quota overflow."),
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return script[model].pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0.0)

    monkeypatch.setattr(
        main.model_discovery,
        "discover_free_models",
        lambda key: {"gemini-3.1-flash-lite", "gemini-4-hydrogen"},
    )

    engine = GeminiFailoverEngine(
        api_key="fake-test-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
        max_attempts_per_model=3,
    )

    payload = {"contents": [{"role": "user", "parts": [{"text": "Mitigation steps for Windows SMBv3"}]}]}
    answer, model_used, is_fallback = engine.generate_with_failover(payload)

    assert model_used == "gemini-4-hydrogen"
    assert is_fallback is True
    assert "Hydrogen model answering" in answer
    assert calls.count("gemini-3.1-flash-lite") == 3
    assert "gemini-4-hydrogen" in calls


def test_404_unsupported_model_immediate_skip(monkeypatch):
    """Simulate HTTP 404 (model unsupported/not found) -> mark UNAVAILABLE, skip immediately without retrying."""
    calls = []

    script = {
        "gemini-3.1-flash-lite": [
            make_err_response(404, "Model gemini-3.1-flash-lite not found for API version v1beta", "NOT_FOUND"),
        ],
        "gemini-4-hydrogen": [
            make_ok_response("Fallback hydrogen response."),
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return script[model].pop(0)

    monkeypatch.setattr(requests, "post", fake_post)

    monkeypatch.setattr(
        main.model_discovery,
        "discover_free_models",
        lambda key: {"gemini-3.1-flash-lite", "gemini-4-hydrogen"},
    )

    engine = GeminiFailoverEngine(
        api_key="fake-test-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
        max_attempts_per_model=3,
    )

    payload = {"contents": [{"role": "user", "parts": [{"text": "Check status"}]}]}
    answer, model_used, is_fallback = engine.generate_with_failover(payload)

    # 404 was NOT retried 3 times - skipped after 1 attempt!
    assert calls == ["gemini-3.1-flash-lite", "gemini-4-hydrogen"]
    assert model_used == "gemini-4-hydrogen"
    assert is_fallback is True

    # Verify model is marked UNAVAILABLE in health manager
    summary = main.health_manager.get_health_summary()
    assert summary["gemini-3.1-flash-lite"]["status"] == "UNAVAILABLE"
    assert main.health_manager.is_available("gemini-3.1-flash-lite") is False


def test_auth_failure_401_403_halts_without_model_cycling(monkeypatch):
    """Verify that 401/403 authentication error immediately halts without cycling through other models."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return make_err_response(401, "API key not valid. Please pass a valid API key.", "UNAUTHENTICATED")

    monkeypatch.setattr(requests, "post", fake_post)

    engine = GeminiFailoverEngine(
        api_key="invalid-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen", "gemini-3.5-flash-lite"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
    )

    payload = {"contents": [{"role": "user", "parts": [{"text": "Hello"}]}]}
    with pytest.raises(GeminiAuthError) as exc_info:
        engine.generate_with_failover(payload)

    # Halts immediately on 401 without cycling through hydrogen or other models!
    assert calls == ["gemini-3.1-flash-lite"]
    assert "invalid or lacks required permissions" in str(exc_info.value)
    assert exc_info.value.status_code == 401


def test_all_models_fail_shows_friendly_message(monkeypatch):
    """Verify that when all verified free models fail, a friendly user message is shown without technical leak."""
    def fake_post(url, json=None, headers=None, timeout=None):
        return make_err_response(503, "Backend service overloaded on server cluster 9", "UNAVAILABLE")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(
        main.model_discovery,
        "discover_free_models",
        lambda key: {"gemini-3.1-flash-lite", "gemini-4-hydrogen"},
    )

    engine = GeminiFailoverEngine(
        api_key="fake-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
        max_attempts_per_model=2,
    )

    payload = {"contents": [{"role": "user", "parts": [{"text": "Analyze threats"}]}]}
    with pytest.raises(AllModelsFailedError) as exc_info:
        engine.generate_with_failover(payload)

    msg = str(exc_info.value)
    # Must be friendly and professional
    assert "ThreatGuard AI is currently unable to reach the AI service due to temporary high demand." in msg
    assert "All verified free models were attempted." in msg
    # Must NOT expose raw technical errors or cluster internal details to normal users
    assert "HTTP 503" not in msg
    assert "cluster 9" not in msg
    assert exc_info.value.status_code == 503


def test_model_health_cooldown_and_recovery():
    """Verify health manager transitions: HEALTHY -> COOLDOWN -> auto-recovery after expiry."""
    manager = ModelHealthManager(base_cooldown_seconds=10.0, max_cooldown_seconds=60.0)

    # Initial state is available
    assert manager.is_available("gemini-3.1-flash-lite") is True

    # Record failure with 10s cooldown
    manager.record_failure("gemini-3.1-flash-lite", status_code=503, error_message="Overloaded")
    assert manager.is_available("gemini-3.1-flash-lite") is False

    # Simulate time passing past cooldown
    info = manager._health["gemini-3.1-flash-lite"]
    info.cooldown_until = time.time() - 1.0  # Cooldown expired

    # is_available should auto-recover to True and status to HEALTHY
    assert manager.is_available("gemini-3.1-flash-lite") is True
    assert info.status == ModelStatus.HEALTHY


def test_context_and_history_preserved_across_failover(monkeypatch):
    """Verify that conversation history, system prompt, and RAG context are fully preserved when switching models."""
    captured_payloads = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        captured_payloads[model] = json
        if model == "gemini-3.1-flash-lite":
            return make_err_response(503, "High demand", "UNAVAILABLE")
        return make_ok_response("Context preserved successfully.")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(
        main.model_discovery,
        "discover_free_models",
        lambda key: {"gemini-3.1-flash-lite", "gemini-4-hydrogen"},
    )

    engine = GeminiFailoverEngine(
        api_key="fake-key",
        configured_models=["gemini-3.1-flash-lite", "gemini-4-hydrogen"],
        primary_model="gemini-3.1-flash-lite",
        health_manager=main.health_manager,
        max_attempts_per_model=1,
    )

    full_payload = {
        "system_instruction": {"parts": [{"text": main.GENERAL_ASSISTANT_SYSTEM_PROMPT}]},
        "contents": [
            {"role": "user", "parts": [{"text": "What is CVE-2023-38606?"}]},
            {"role": "model", "parts": [{"text": "CVE-2023-38606 is an iOS kernel memory vulnerability."}]},
            {"role": "user", "parts": [{"text": "How do I mitigate it in our environment?"}]},
        ],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
    }

    answer, model_used, is_fallback = engine.generate_with_failover(full_payload)
    assert model_used == "gemini-4-hydrogen"
    assert is_fallback is True

    # Verify both models received the identical payload (history and system instruction preserved)
    assert captured_payloads["gemini-3.1-flash-lite"] == full_payload
    assert captured_payloads["gemini-4-hydrogen"] == full_payload
    assert len(captured_payloads["gemini-4-hydrogen"]["contents"]) == 3


def test_fastapi_health_endpoint_reports_model_health():
    """Verify that GET /health exposes ThreatGuard AI metadata and model health."""
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["system_name"] == "ThreatGuard AI"
    assert data["llm_backend"] == "hosted_google_gemini_api"
    assert "primary_model" in data
    assert "configured_free_models" in data
    assert "model_health" in data
    assert "available_models" in data


def test_fastapi_chat_endpoint_automatic_failover(monkeypatch):
    """Verify that POST /chat triggers automatic model failover and returns model name and fallback flag."""
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    calls = []
    script = {
        "gemini-3.1-flash-lite": [
            make_err_response(503, "High demand", "UNAVAILABLE"),
            make_err_response(503, "High demand", "UNAVAILABLE"),
            make_err_response(503, "High demand", "UNAVAILABLE"),
        ],
        "gemini-4-hydrogen": [
            make_ok_response("ThreatGuard AI active defense recommendation."),
        ],
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        return script[model].pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(main, "GEMINI_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setattr(main, "GEMINI_FALLBACK_MODELS", ["gemini-4-hydrogen"])
    monkeypatch.setattr(main, "GEMINI_FREE_MODELS", ["gemini-3.1-flash-lite", "gemini-4-hydrogen"])
    monkeypatch.setattr(main, "GEMINI_API_KEY", "fake-test-key")

    resp = client.post(
        "/chat",
        json={
            "message": "What is the status of active Linux exploits?",
            "session_id": "test-session-001",
            "platform": "Linux",
            "lifecycle": "Active",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["system_name"] == "ThreatGuard AI"
    assert data["model"] == "gemini-4-hydrogen"
    assert data["fallback_used"] is True
    assert "ThreatGuard AI active defense recommendation." in data["answer"]
    assert calls.count("gemini-3.1-flash-lite") == 3
    assert "gemini-4-hydrogen" in calls

