"""Tests for Gemini retry/backoff, verified fallback, error messages and key redaction (no network, no MongoDB)."""
import logging
import os

os.environ["GEMINI_API_KEY"] = "AIzaSyFAKEKEYFAKEKEYFAKEKEYFAKEKEY12345"
os.environ["GEMINI_MODEL"] = "primary-model"
os.environ["GEMINI_FALLBACK_MODELS"] = "fallback-a,fallback-b"
os.environ["GEMINI_RETRY_BASE_DELAY"] = "1.0"

import pytest
import requests

import main

KEY = os.environ["GEMINI_API_KEY"]
PAYLOAD = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body


def ok(text="hello"):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def err(status, message="boom", api_status="UNAVAILABLE", details=None, headers=None):
    body = {"error": {"code": status, "message": message, "status": api_status}}
    if details:
        body["error"]["details"] = details
    return FakeResp(status, body, headers)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(main, "GEMINI_MODEL", "primary-model")
    monkeypatch.setattr(main, "GEMINI_API_KEY", KEY)
    monkeypatch.setattr(main, "GEMINI_FALLBACK_MODELS", ["fallback-a", "fallback-b"])
    monkeypatch.setattr(main, "GEMINI_MAX_FALLBACKS", 1)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0.0)
    main._MODEL_CACHE.update({"models": None, "expires": 0.0})
    main.health_manager.reset_all()
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def install(monkeypatch, post_script, models=("primary-model", "fallback-a")):
    """post_script: dict model -> list of responses/exceptions consumed in order."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        item = post_script[model].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def fake_get(url, params=None, headers=None, timeout=None):
        if models is None:
            return FakeResp(500, {})
        return FakeResp(200, {"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]} for m in models]})

    monkeypatch.setattr(main.requests, "post", fake_post)
    monkeypatch.setattr(main.requests, "get", fake_get)
    return calls


def test_success_first_try(monkeypatch, env):
    calls = install(monkeypatch, {"primary-model": [ok("yes")]})
    assert main._generate_with_resilience(PAYLOAD) == ("yes", "primary-model", False)
    assert calls == ["primary-model"] and env == []


def test_503_then_success_uses_exponential_backoff(monkeypatch, env):
    calls = install(monkeypatch, {"primary-model": [err(503), err(503), ok("recovered")]})
    assert main._generate_with_resilience(PAYLOAD) == ("recovered", "primary-model", False)
    assert calls == ["primary-model"] * 3
    assert env == [1.0, 2.0]


def test_429_is_retried(monkeypatch, env):
    install(monkeypatch, {"primary-model": [err(429, api_status="RESOURCE_EXHAUSTED"), ok()]})
    assert main._generate_with_resilience(PAYLOAD)[1] == "primary-model"


def test_timeout_and_network_errors_are_retried(monkeypatch, env):
    install(monkeypatch, {"primary-model": [requests.Timeout("slow"), requests.ConnectionError("down"), ok()]})
    assert main._generate_with_resilience(PAYLOAD)[0] == "hello"


def test_fallback_used_only_when_confirmed_available(monkeypatch, env):
    calls = install(
        monkeypatch,
        {"primary-model": [err(503)] * 3, "fallback-a": [ok("from fallback")]},
        models=("primary-model", "fallback-a"),
    )
    assert main._generate_with_resilience(PAYLOAD) == ("from fallback", "fallback-a", True)
    assert calls == ["primary-model"] * 3 + ["fallback-a"]


def test_fallback_skipped_when_not_in_models_list(monkeypatch, env):
    calls = install(monkeypatch, {"primary-model": [err(503)] * 3}, models=("primary-model",))
    with pytest.raises(main.LLMError) as ei:
        main._generate_with_resilience(PAYLOAD)
    assert calls == ["primary-model"] * 3  # never guessed at an unconfirmed model
    assert ei.value.status_code == 503
    assert "ThreatGuard AI is currently unable to reach the AI service" in str(ei.value)


def test_fallback_skipped_when_models_list_fails(monkeypatch, env):
    calls = install(monkeypatch, {"primary-model": [err(503)] * 3}, models=None)
    with pytest.raises(main.LLMError) as ei:
        main._generate_with_resilience(PAYLOAD)
    assert calls == ["primary-model"] * 3
    assert ei.value.status_code == 503
    assert "ThreatGuard AI is currently unable to reach the AI service" in str(ei.value)


def test_all_retries_fail_message_is_specific(monkeypatch, env):
    install(
        monkeypatch,
        {"primary-model": [err(503, "This model is currently experiencing high demand.")] * 3, "fallback-a": [err(503)] * 3},
    )
    with pytest.raises(main.LLMError) as ei:
        main._generate_with_resilience(PAYLOAD)
    msg = str(ei.value)
    # User message must be friendly, never exposing raw technical error codes like HTTP 503 or internal model names
    assert "ThreatGuard AI is currently unable to reach the AI service" in msg
    assert "HTTP 503" not in msg
    assert ei.value.status_code == 503


def test_429_final_message_and_retry_after(monkeypatch, env):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}]
    install(monkeypatch, {"primary-model": [err(429, "quota", "RESOURCE_EXHAUSTED", details)] * 3}, models=("primary-model",))
    with pytest.raises(main.LLMError) as ei:
        main._generate_with_resilience(PAYLOAD)
    assert ei.value.status_code == 429
    assert "ThreatGuard AI is currently unable to reach the AI service" in str(ei.value)
    assert env == [3.0, 3.0]  # honoured Google's suggested delay (> backoff of 1s)... then 3s > 2s


def test_long_retry_after_does_not_block(monkeypatch, env):
    details = [{"retryDelay": "55s"}]
    calls = install(monkeypatch, {"primary-model": [err(429, "daily quota", "RESOURCE_EXHAUSTED", details)] * 3}, models=("primary-model",))
    with pytest.raises(main.LLMError):
        main._generate_with_resilience(PAYLOAD)
    assert calls == ["primary-model"] and env == []


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_errors_are_not_retried(monkeypatch, env, status):
    calls = install(monkeypatch, {"primary-model": [err(status, "API key not valid" if status == 400 else "nope", "X")] * 3})
    with pytest.raises(main.LLMError):
        main._generate_with_resilience(PAYLOAD)
    assert calls == ["primary-model"] and env == []


def test_404_unsupported_skips_model_and_fails_over(monkeypatch, env):
    calls = install(
        monkeypatch,
        {"primary-model": [err(404, "models/primary-model is not found", "NOT_FOUND")], "fallback-a": [ok("from fallback")]},
        models=("primary-model", "fallback-a"),
    )
    res = main._generate_with_resilience(PAYLOAD)
    assert res == ("from fallback", "fallback-a", True)
    assert calls == ["primary-model", "fallback-a"]


def test_total_budget_stops_retries(monkeypatch, env):
    monkeypatch.setattr(main, "GEMINI_TOTAL_BUDGET", 0.5)  # smaller than the 1s first backoff
    calls = install(monkeypatch, {"primary-model": [err(503)] * 3}, models=("primary-model",))
    with pytest.raises(main.LLMError):
        main._generate_with_resilience(PAYLOAD)
    assert calls == ["primary-model"]


def test_api_key_never_logged_or_returned(monkeypatch, env, caplog):
    leaky = f"Bad request for key {KEY} at https://x?key={KEY}"
    install(monkeypatch, {"primary-model": [err(503, leaky)] * 3}, models=("primary-model",))
    caplog.set_level(logging.INFO, logger="cti.gemini")
    main.logger.propagate = True
    try:
        with pytest.raises(main.LLMError) as ei:
            main._generate_with_resilience(PAYLOAD)
    finally:
        main.logger.propagate = False
    assert KEY not in caplog.text and KEY not in str(ei.value)
    assert "http=503" in caplog.text and "[REDACTED]" in caplog.text


def test_wrapper_returns_text_only(monkeypatch, env):
    install(monkeypatch, {"primary-model": [ok("plain")]})
    out = main.call_hosted_gemini_llm("hi", [], [{"role": "user", "content": "hi"}], [], "All", "All")
    assert out == "plain"
