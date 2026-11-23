"""
Final Assessment (A.3 - A.7): FastAPI Backend for Threat Intelligence RAG Chatbot
- Hosted LLM API via Google Gemini REST/SDK (NOT Ollama)
- MongoDB Operational Store (`threats`, `vulnerabilities`, `remediation_audit`)
- MongoDB Vector Search / Cosine RAG Store (`knowledge`)
- MongoDB Conversation Memory Store (`sessions`)
- Background Vulnerability Scanner & Conversational Confirmation Mitigation
- Production Hardening: Rate Limiting, /health check, Telemetry & Graceful Shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymongo import MongoClient, DESCENDING

from background_scanner import (
    apply_mitigation,
    background_scan_loop,
    detect_host_platform,
    run_platform_scan,
)
from ingest_threats import deterministic_embedding, get_embeddings, ingest_corpus

import sys
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

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "threat_intel_chatbot")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

# ThreatGuard AI Multi-Model Failover Configuration (FREE-tier Google Gemini models only)
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MAX_ATTEMPTS = max(1, int(os.getenv("GEMINI_MAX_ATTEMPTS", "3")))          # attempts per model
GEMINI_RETRY_BASE_DELAY = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "1.0"))      # 1s, 2s, 4s ... (+ jitter)
GEMINI_RETRY_MAX_DELAY = float(os.getenv("GEMINI_RETRY_MAX_DELAY", "8.0"))        # never sleep longer than this
GEMINI_REQUEST_TIMEOUT = float(os.getenv("GEMINI_REQUEST_TIMEOUT", "30"))         # seconds per HTTP call
GEMINI_TOTAL_BUDGET = float(os.getenv("GEMINI_TOTAL_BUDGET_SECONDS", "90"))       # stop retrying after this
GEMINI_COOLDOWN_SECONDS = float(os.getenv("GEMINI_COOLDOWN_SECONDS", "60.0"))     # health manager cooldown
GEMINI_MAX_FALLBACKS = max(0, int(os.getenv("GEMINI_MAX_FALLBACKS", "4")))
GEMINI_FREE_MODELS = [
    m.strip().removeprefix("models/")
    for m in os.getenv(
        "GEMINI_FREE_MODELS",
        "gemini-3.1-flash-lite,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash-lite",
    ).split(",")
    if m.strip()
]
GEMINI_FALLBACK_MODELS = list(GEMINI_FREE_MODELS)
GEMINI_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
GEMINI_MODEL_LIST_TTL = 600.0  # seconds to cache the "which models can this key use" lookup

logger = logging.getLogger("threatguard.gemini")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{30,}")

# Initialize Model Health Manager and Model Discovery
health_manager = ModelHealthManager(base_cooldown_seconds=GEMINI_COOLDOWN_SECONDS)
model_discovery = GeminiModelDiscovery(cache_ttl_seconds=GEMINI_MODEL_LIST_TTL)


def _redact(text: Any) -> str:
    """Strip the API key (and anything shaped like a Google API key) from text before logging/showing it."""
    return redact_api_key(text, getattr(sys.modules[__name__], "GEMINI_API_KEY", GEMINI_API_KEY))

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]
knowledge_col = db["knowledge"]
threats_col = db["threats"]
sessions_col = db["sessions"]
vulns_col = db["vulnerabilities"]
audit_col = db["remediation_audit"]

# Production metrics & in-memory sliding-window rate limiter (A.7)
METRICS: Dict[str, Any] = {
    "requests_total": 0,
    "chat_requests": 0,
    "errors_total": 0,
    "total_latency_ms": 0.0,
    "estimated_tokens": 0,
}
RATE_BUCKETS: Dict[str, Deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure knowledge base is seeded on startup
    if knowledge_col.count_documents({}) == 0:
        ingest_corpus()
    # Run initial platform scan & launch background daemon
    run_platform_scan(detect_host_platform(), scope="startup-daemon")
    scanner_task = asyncio.create_task(background_scan_loop(SCAN_INTERVAL_SECONDS))
    yield
    scanner_task.cancel()
    mongo_client.close()


app = FastAPI(
    title="MongoDB CTI LLM Threat Intelligence & Autonomous Remediation Chatbot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def enforce_rate_limit(client_ip: str) -> None:
    now = time.time()
    bucket = RATE_BUCKETS[client_ip]
    while bucket and (now - bucket[0]) > 60.0:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again in 60 seconds.")
    bucket.append(now)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def retrieve_context(
    query: str,
    platform_filter: Optional[str] = None,
    lifecycle_filter: Optional[str] = None,
    n_results: int = 4,
) -> List[Dict[str, Any]]:
    """
    Retrieves top-k relevant threat advisories using Atlas $vectorSearch when available,
    falling back seamlessly to local cosine similarity over MongoDB embeddings.
    """
    query_emb = get_embeddings([query])[0]

    # Try Atlas $vectorSearch pipeline first
    try:
        vs_stage: Dict[str, Any] = {
            "index": "vector_index",
            "path": "embedding",
            "queryVector": query_emb,
            "numCandidates": 20,
            "limit": n_results,
        }
        if platform_filter and platform_filter.lower() != "all":
            vs_stage["filter"] = {"platform": platform_filter}
        results = list(knowledge_col.aggregate([{"$vectorSearch": vs_stage}]))
        if results:
            return results
    except Exception:
        pass

    # Local MongoDB Community / Docker fallback using stored embeddings
    mongo_filter: Dict[str, Any] = {}
    if platform_filter and platform_filter.lower() != "all":
        mongo_filter["platform"] = {"$regex": f"^{re.escape(platform_filter)}$", "$options": "i"}
    if lifecycle_filter and lifecycle_filter.lower() != "all":
        mongo_filter["lifecycle"] = {"$regex": f"^{re.escape(lifecycle_filter)}$", "$options": "i"}

    candidates = list(knowledge_col.find(mongo_filter, {"_id": 0}))
    if not candidates:
        candidates = list(knowledge_col.find({}, {"_id": 0}))

    scored = []
    for doc in candidates:
        emb = doc.get("embedding") or deterministic_embedding(doc.get("text", ""))
        score = cosine_similarity(query_emb, emb)
        scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:n_results]]


def get_history(session_id: str, n: int = 5) -> List[Dict[str, str]]:
    msgs = list(
        sessions_col.find({"session_id": session_id}, {"_id": 0})
        .sort("timestamp", DESCENDING)
        .limit(n * 2)
    )[::-1]
    return [{"role": m["role"], "content": m["content"]} for m in msgs]


class LLMError(Exception):
    """Raised when the hosted LLM cannot produce an answer; the message is safe to show to the user."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


THREAT_KEYWORDS = re.compile(
    r"\b(threat|vuln\w*|cve|ioc|iocs|malware|ransomware|exploit\w*|mitigat\w*|remediat\w*|patch\w*|harden\w*|"
    r"scan\w*|apt|attack\w*|phish\w*|zero[- ]?day|cvss|advisor\w*|confirm fix|"
    r"linux|windows|macos|android|ios|anticipated|evolving|resolved|lifecycle)\b",
    re.I,
)


def is_threat_query(user_msg: str, history: List[Dict[str, str]]) -> bool:
    """True when the message (or, for short follow-ups, the previous user turn) is about threat scanning/remediation."""
    if THREAT_KEYWORDS.search(user_msg):
        return True
    if len(user_msg.split()) <= 8:
        prev_users = [h["content"] for h in history if h.get("role") == "user"]
        # history already contains the current message as its last user turn
        if len(prev_users) >= 2 and THREAT_KEYWORDS.search(prev_users[-2]):
            return True
    return False


GENERAL_ASSISTANT_SYSTEM_PROMPT = (
    "You are ThreatGuard AI, a friendly, knowledgeable, general-purpose AI assistant and cybersecurity intelligence specialist with a natural, conversational tone. "
    "Answer questions on ANY topic, explain concepts clearly, solve problems step by step, write and debug code "
    "(use fenced code blocks), and help with studying, writing and analysis. Use the conversation history to "
    "understand follow-up questions, pronouns and references to earlier messages. Be accurate; if you are unsure, "
    "say so instead of guessing. Match the user's depth and language, and keep answers concise unless asked for detail.\n\n"
    "You ALSO have specialist knowledge of this MongoDB Labs 7-18 platform and Cyber Threat Intelligence (CTI) "
    "(Anticipated, Active, Evolving, Resolved threats across Linux, Windows, macOS, Android, iOS). The CTI context "
    "supplied with a message is optional reference material: use it only when the question is about those topics, "
    "and ignore it otherwise. When a pending scanner vulnerability is relevant, give its VULN-ID and invite the "
    "user to reply `CONFIRM FIX <VULN-ID>` before any remediation. Never refuse a question just because it is "
    "unrelated to this website."
)


def _build_gemini_contents(history: List[Dict[str, str]], user_msg: str, grounded_prompt: str) -> List[Dict[str, Any]]:
    """Convert stored history into alternating Gemini turns; the latest user turn carries the optional CTI context."""
    turns: List[Dict[str, Any]] = []
    for h in history[-20:]:
        role = "user" if h.get("role") == "user" else "model"
        text = (h.get("content") or "").strip()
        if not text:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            turns.append({"role": role, "parts": [{"text": text}]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    if turns and turns[-1]["role"] == "user":
        turns[-1] = {"role": "user", "parts": [{"text": grounded_prompt}]}
    else:
        turns.append({"role": "user", "parts": [{"text": grounded_prompt}]})
    return turns


class _GeminiAttemptError(Exception):
    """One failed Gemini HTTP attempt. `retryable` marks temporary failures (429/5xx/timeouts/network)."""

    def __init__(
        self,
        http_status: int,
        user_message: str,
        retryable: bool,
        retry_after: Optional[float] = None,
        api_status: str = "",
        detail: str = "",
        kind: str = "http",
        is_auth: bool = False,
        is_not_found: bool = False,
        is_permanent: bool = False,
    ):
        super().__init__(user_message)
        self.http_status = http_status      # 0 = no HTTP response (network error)
        self.user_message = user_message
        self.retryable = retryable
        self.retry_after = retry_after      # seconds suggested by Google (Retry-After / RetryInfo)
        self.api_status = api_status        # e.g. UNAVAILABLE, RESOURCE_EXHAUSTED
        self.detail = detail                # Google's own (redacted) error text
        self.kind = kind                    # http | timeout | network
        self.is_auth = is_auth
        self.is_not_found = is_not_found
        self.is_permanent = is_permanent
        self.attempts = 1
        self.model = ""


def _parse_retry_delay(resp: "requests.Response", body: Dict[str, Any]) -> Optional[float]:
    """Read Google's suggested wait from the Retry-After header or the RetryInfo error detail."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    for item in (body.get("error", {}) or {}).get("details", []) or []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return max(0.0, float(delay[:-1]))
            except ValueError:
                pass
    return None


def _gemini_single_request(model: str, payload: Dict[str, Any]) -> str:
    """One HTTP call to Gemini. Returns the answer text or raises _GeminiAttemptError."""
    # Strict free-tier validation: Never allow paid or Pro models
    if not is_free_tier_model(model):
        raise _GeminiAttemptError(
            403,
            f"Model '{model}' is not a verified free-tier Gemini model. Paid models are prohibited.",
            False,
            is_permanent=True,
            detail="Paid model restriction enforced.",
        )

    endpoint = f"{GEMINI_API_BASE}/models/{model}:generateContent"
    try:
        current_key = getattr(sys.modules[__name__], "GEMINI_API_KEY", GEMINI_API_KEY)
        resp = requests.post(
            endpoint, json=payload, headers={"x-goog-api-key": current_key}, timeout=GEMINI_REQUEST_TIMEOUT
        )
    except requests.Timeout as exc:
        raise _GeminiAttemptError(
            504, "The AI service timed out.", True, detail=_redact(exc)[:200], kind="timeout"
        )
    except requests.RequestException as exc:
        raise _GeminiAttemptError(
            0, "Could not reach the AI service (network error).", True, detail=_redact(exc)[:200], kind="network"
        )

    if resp.status_code != 200:
        try:
            body = resp.json()
        except Exception:
            body = {}
        err = body.get("error", {}) if isinstance(body, dict) else {}
        detail = _redact(err.get("message", "") or "")[:300]
        api_status = str(err.get("status", ""))
        status = resp.status_code
        if status == 400 and "api key" in detail.lower():
            raise _GeminiAttemptError(401, "The Gemini API key is invalid. Check GEMINI_API_KEY.", False, is_auth=True, api_status=api_status, detail=detail)
        if status in (401, 403):
            raise _GeminiAttemptError(401, "The Gemini API key was rejected or lacks access. Check GEMINI_API_KEY.", False, is_auth=True, api_status=api_status, detail=detail)
        if status == 404 or "not found" in detail.lower() or "unsupported" in detail.lower():
            raise _GeminiAttemptError(404, f"Gemini model '{model}' is not available or unsupported for this key.", False, is_not_found=True, is_permanent=True, api_status=api_status, detail=detail)
        if status in GEMINI_RETRYABLE_STATUSES:
            raise _GeminiAttemptError(
                status, f"Gemini returned HTTP {status}.", True,
                retry_after=_parse_retry_delay(resp, body if isinstance(body, dict) else {}),
                api_status=api_status, detail=detail,
            )
        raise _GeminiAttemptError(502, f"The Gemini API returned HTTP {status}. {detail}".strip(), False, api_status=api_status, detail=detail)

    try:
        data = resp.json()
    except Exception:
        raise _GeminiAttemptError(502, "The Gemini API returned an unreadable response.", True, kind="network")
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            return text
    if data.get("promptFeedback", {}).get("blockReason") or (candidates and candidates[0].get("finishReason") == "SAFETY"):
        raise _GeminiAttemptError(422, "The request was blocked by the AI safety filters. Try rephrasing it.", False)
    raise _GeminiAttemptError(502, "The AI returned an empty response. Please rephrase and try again.", False)


def _gemini_retry_loop(model: str, payload: Dict[str, Any], deadline: float) -> str:
    """Up to GEMINI_MAX_ATTEMPTS calls on one model with exponential backoff (1s, 2s, ... plus jitter)."""
    last: Optional[_GeminiAttemptError] = None
    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            text = _gemini_single_request(model, payload)
            if attempt > 1:
                logger.info("Gemini model=%s succeeded on attempt %d/%d", model, attempt, GEMINI_MAX_ATTEMPTS)
            return text
        except _GeminiAttemptError as exc:
            exc.attempts = attempt
            exc.model = model
            last = exc
            logger.warning(
                "Gemini attempt %d/%d failed: model=%s http=%s api_status=%s kind=%s retryable=%s detail=%s",
                attempt, GEMINI_MAX_ATTEMPTS, model, exc.http_status or "n/a", exc.api_status or "n/a",
                exc.kind, exc.retryable, exc.detail or exc.user_message,
            )
            if not exc.retryable or attempt >= GEMINI_MAX_ATTEMPTS:
                break
            delay = min(GEMINI_RETRY_MAX_DELAY, GEMINI_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            if exc.retry_after is not None:
                if exc.retry_after > GEMINI_RETRY_MAX_DELAY:
                    logger.warning("Gemini asked us to wait %.0fs; not waiting that long on model=%s", exc.retry_after, model)
                    break
                delay = max(delay, exc.retry_after)
            if time.monotonic() + delay > deadline:
                logger.warning("Gemini retry budget exhausted for model=%s", model)
                break
            logger.info("Retrying Gemini model=%s in %.1fs", model, delay)
            time.sleep(delay)
    assert last is not None
    raise last


_MODEL_CACHE: Dict[str, Any] = {"models": None, "expires": 0.0}
_MODEL_CACHE_LOCK = threading.Lock()


def _list_available_models() -> Optional[Set[str]]:
    """Models this API key can call generateContent on (via the models.list endpoint). None if unconfirmed."""
    now = time.monotonic()
    with _MODEL_CACHE_LOCK:
        if _MODEL_CACHE["expires"] > now:
            return _MODEL_CACHE["models"]

    names: Optional[Set[str]] = set()
    try:
        token = ""
        for _ in range(10):
            params: Dict[str, Any] = {"pageSize": 1000}
            if token:
                params["pageToken"] = token
            r = requests.get(
                f"{GEMINI_API_BASE}/models", params=params, headers={"x-goog-api-key": GEMINI_API_KEY}, timeout=10
            )
            if r.status_code != 200:
                logger.warning("Could not list Gemini models for fallback check: HTTP %s", r.status_code)
                names = None
                break
            data = r.json()
            for m in data.get("models", []):
                if "generateContent" in (m.get("supportedGenerationMethods") or []):
                    names.add(str(m.get("name", "")).removeprefix("models/"))
            token = data.get("nextPageToken") or ""
            if not token:
                break
    except Exception as exc:
        logger.warning("Could not list Gemini models for fallback check: %s", _redact(exc)[:200])
        names = None

    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE["models"] = names
        _MODEL_CACHE["expires"] = now + (GEMINI_MODEL_LIST_TTL if names is not None else 60.0)
    return names


def _confirmed_fallback_models() -> Tuple[List[str], bool]:
    """Fallback models that are confirmed usable by this key and verified free-tier. Second value: whether check succeeded."""
    current_fallbacks = getattr(sys.modules[__name__], "GEMINI_FALLBACK_MODELS", None) or getattr(sys.modules[__name__], "GEMINI_FREE_MODELS", DEFAULT_FREE_MODELS)
    current_primary = getattr(sys.modules[__name__], "GEMINI_MODEL", GEMINI_MODEL).strip().removeprefix("models/")
    max_fb = getattr(sys.modules[__name__], "GEMINI_MAX_FALLBACKS", GEMINI_MAX_FALLBACKS)

    if max_fb == 0 or not current_fallbacks:
        return [], True

    # Filter out primary model and any paid models
    pool = [
        m.strip().removeprefix("models/")
        for m in current_fallbacks
        if m.strip().removeprefix("models/") != current_primary and is_free_tier_model(m.strip().removeprefix("models/"))
    ]

    available = _list_available_models()
    if available is None:
        return [], False

    confirmed = [m for m in pool if m in available]
    if not confirmed:
        logger.info("None of the configured fallback models %s is available in models.list for this API key", pool)
    return confirmed[:max_fb], True


def _final_failure(
    primary: _GeminiAttemptError,
    fallback_errors: List[_GeminiAttemptError],
    fallback_check_ok: bool,
) -> LLMError:
    """Logs detailed technical failure in backend logs and returns a friendly message for users."""
    st = primary.http_status
    code = 503 if st in (500, 502, 503) else (429 if st == 429 else (504 if st == 504 else 502))

    # Backend log with complete technical breakdown
    tech_parts = [f"Primary '{primary.model}' failed after {primary.attempts} attempt(s) (HTTP {primary.http_status})"]
    for fe in fallback_errors:
        tech_parts.append(f"fallback '{fe.model}' failed ({fe.attempts} attempts, HTTP {fe.http_status})")
    logger.error("ThreatGuard AI Multi-Model Failover Exhausted: %s", " -> ".join(tech_parts))

    # Friendly user-facing message - never expose technical errors (HTTP 503, stack traces) to normal users
    friendly_msg = (
        "ThreatGuard AI is currently unable to reach the AI service due to temporary high demand. "
        "All verified free models were attempted. Please try again shortly."
    )
    return LLMError(friendly_msg, code)


def _generate_with_resilience(payload: Dict[str, Any]) -> Tuple[str, str, bool]:
    """Primary model with retries and automatic failover across verified free Gemini models."""
    current_key = getattr(sys.modules[__name__], "GEMINI_API_KEY", GEMINI_API_KEY)
    current_primary = getattr(sys.modules[__name__], "GEMINI_MODEL", GEMINI_MODEL).strip().removeprefix("models/")
    current_budget = getattr(sys.modules[__name__], "GEMINI_TOTAL_BUDGET", GEMINI_TOTAL_BUDGET)
    deadline = time.monotonic() + current_budget

    if not current_key:
        raise LLMError("ThreatGuard AI: No Gemini API key is configured. Please set GEMINI_API_KEY.", 503)

    # 1. Try primary model first (if not in cooldown)
    primary_err_record: Optional[_GeminiAttemptError] = None
    if health_manager.is_available(current_primary):
        try:
            answer = _gemini_retry_loop(current_primary, payload, deadline)
            health_manager.record_success(current_primary)
            logger.info("ThreatGuard AI: Successfully generated response using primary model '%s'", current_primary)
            return answer, current_primary, False
        except _GeminiAttemptError as exc:
            primary_err_record = exc
            if exc.is_auth:
                # Halt immediately on auth failure (401/403) - do NOT switch models
                logger.error("Authentication failed on model '%s' (HTTP %s). Halting model switching.", current_primary, exc.http_status)
                raise LLMError(exc.user_message, exc.http_status or 401)
            if exc.is_not_found or exc.http_status == 404:
                health_manager.record_failure(current_primary, 404, exc.detail, is_permanent=True)
                logger.warning("Primary model '%s' returned 404 / unsupported. Marking UNAVAILABLE and skipping.", current_primary)
            else:
                health_manager.record_failure(current_primary, exc.http_status, exc.detail, is_permanent=False, retry_after=exc.retry_after)
                logger.warning("Primary model '%s' temporarily unavailable (HTTP %s). Entering cooldown and initiating failover.", current_primary, exc.http_status)
    else:
        logger.info("Primary model '%s' is currently in cooldown. Checking fallback models.", current_primary)
        primary_err_record = _GeminiAttemptError(503, f"Model '{current_primary}' is cooling down.", True)
        primary_err_record.model = current_primary

    # 2. Automatic Failover to next verified free models
    fallbacks, check_ok = _confirmed_fallback_models()
    failed: List[_GeminiAttemptError] = []
    for fb_model in fallbacks:
        if time.monotonic() >= deadline:
            break
        # Skip if in cooldown or unavailable
        if not health_manager.is_available(fb_model):
            logger.info("Fallback model '%s' is currently cooling down. Trying next.", fb_model)
            continue

        # Strict free-tier check
        if not is_free_tier_model(fb_model):
            logger.warning("Model '%s' is not in free-tier whitelist. Skipping.", fb_model)
            continue

        logger.info("ThreatGuard AI Failover: Primary unavailable; trying verified free model '%s'", fb_model)
        try:
            answer = _gemini_retry_loop(fb_model, payload, deadline)
            health_manager.record_success(fb_model)
            logger.info("ThreatGuard AI: Successfully generated response using fallback model '%s' (fallback=True)", fb_model)
            return answer, fb_model, True
        except _GeminiAttemptError as fb_err:
            failed.append(fb_err)
            if fb_err.is_auth:
                logger.error("Authentication failed on fallback model '%s'. Halting failover.", fb_model)
                raise LLMError(fb_err.user_message, fb_err.http_status or 401)
            if fb_err.is_not_found or fb_err.http_status == 404:
                health_manager.record_failure(fb_model, 404, fb_err.detail, is_permanent=True)
                logger.warning("Fallback model '%s' returned 404 / unsupported. Marking UNAVAILABLE.", fb_model)
            else:
                health_manager.record_failure(fb_model, fb_err.http_status, fb_err.detail, is_permanent=False, retry_after=fb_err.retry_after)
            if not fb_err.retryable and fb_err.http_status in (401, 422):
                raise LLMError(fb_err.user_message, fb_err.http_status)

    assert primary_err_record is not None
    raise _final_failure(primary_err_record, failed, check_ok)


def call_hosted_gemini_llm_ex(
    user_msg: str,
    context_docs: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    pending_vulns: List[Dict[str, Any]],
    platform: str,
    lifecycle: str,
    use_cti: bool = False,
) -> Tuple[str, str, bool]:
    """Calls the hosted Google Gemini API (NOT Ollama) with retry/backoff and verified fallback.

    Returns (answer, model_actually_used, fallback_used). Raises LLMError on any failure - never returns canned text.
    """
    context_block = "\n".join(
        f"- [{d.get('doc_id')}] ({d.get('lifecycle')} | {d.get('platform')} | {d.get('severity')} CVSS {d.get('cvss')}) "
        f"{d.get('title')}: {d.get('text')} | Command: `{d.get('mitigation_cmd')}`"
        for d in context_docs
    ) or "None."
    vuln_block = "\n".join(
        f"- {v.get('vuln_id')} ({v.get('platform')}, {v.get('severity')}, status={v.get('status')}): "
        f"{v.get('title')} -> Fix: `{v.get('remediation_cmd')}`"
        for v in pending_vulns
    ) or "None currently pending."

    if use_cti:
        grounded_prompt = (
            f"[Threat-intelligence reference - the user is asking about threats/scanning/remediation]\n"
            f"Selected platform filter: {platform} | lifecycle filter: {lifecycle}\n"
            f"CTI knowledge (MongoDB RAG):\n{context_block}\n"
            f"Pending scanner findings:\n{vuln_block}\n\n"
            f"[User message]\n{user_msg}"
        )
    else:
        grounded_prompt = user_msg

    if not GEMINI_API_KEY:
        raise LLMError(
            "No Gemini API key is configured on the server. Set the GEMINI_API_KEY environment variable "
            "(see .env.example) and restart the backend.",
            503,
        )
    payload = {
        "system_instruction": {"parts": [{"text": GENERAL_ASSISTANT_SYSTEM_PROMPT}]},
        "contents": _build_gemini_contents(history, user_msg, grounded_prompt),
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
    }
    return _generate_with_resilience(payload)


def call_hosted_gemini_llm(
    user_msg: str,
    context_docs: List[Dict[str, Any]],
    history: List[Dict[str, str]],
    pending_vulns: List[Dict[str, Any]],
    platform: str,
    lifecycle: str,
    use_cti: bool = False,
) -> str:
    """Backward-compatible wrapper that returns only the answer text."""
    return call_hosted_gemini_llm_ex(
        user_msg, context_docs, history, pending_vulns, platform, lifecycle, use_cti
    )[0]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(default="default-session")
    platform: str = Field(default="All")
    lifecycle: str = Field(default="All")


class ScanRequest(BaseModel):
    platform: str = Field(default="Linux")
    scope: str = Field(default="system-wide")
    project_path: Optional[str] = Field(default="/")


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    try:
        mongo_client.admin.command("ping")
        mongo_status = "connected"
    except Exception as exc:
        mongo_status = f"error: {exc}"
    current_primary = getattr(sys.modules[__name__], "GEMINI_MODEL", GEMINI_MODEL)
    current_free_models = getattr(sys.modules[__name__], "GEMINI_FREE_MODELS", DEFAULT_FREE_MODELS)
    return {
        "status": "healthy" if mongo_status == "connected" else "degraded",
        "system_name": "ThreatGuard AI",
        "mongodb": mongo_status,
        "llm_backend": "hosted_google_gemini_api",
        "primary_model": current_primary,
        "configured_free_models": current_free_models,
        "available_models": health_manager.get_available_models(current_free_models),
        "model_health": health_manager.get_health_summary(),
        "knowledge_chunks": knowledge_col.count_documents({}),
        "pending_vulnerabilities": vulns_col.count_documents({"status": "pending_confirmation"}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def get_metrics() -> Dict[str, Any]:
    avg_ms = (
        round(METRICS["total_latency_ms"] / METRICS["requests_total"], 2)
        if METRICS["requests_total"]
        else 0.0
    )
    return {
        **METRICS,
        "avg_latency_ms": avg_ms,
        "stored_sessions": len(sessions_col.distinct("session_id")),
        "stored_messages": sessions_col.count_documents({}),
    }


@app.get("/threats")
async def list_threats(
    platform: Optional[str] = None,
    lifecycle: Optional[str] = None,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if platform and platform.lower() != "all":
        query["platform"] = {"$regex": f"^{re.escape(platform)}$", "$options": "i"}
    if lifecycle and lifecycle.lower() != "all":
        query["lifecycle"] = {"$regex": f"^{re.escape(lifecycle)}$", "$options": "i"}
    items = list(threats_col.find(query, {"_id": 0}))
    return {"count": len(items), "threats": items}


@app.get("/vulnerabilities")
async def list_vulnerabilities(platform: Optional[str] = None) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if platform and platform.lower() != "all":
        query["platform"] = {"$regex": f"^{re.escape(platform)}$", "$options": "i"}
    items = list(vulns_col.find(query, {"_id": 0}))
    audits = list(audit_col.find({}, {"_id": 0}).sort("timestamp", DESCENDING).limit(10))
    return {"vulnerabilities": items, "recent_audits": audits}


@app.post("/scan")
async def trigger_scan(req: ScanRequest) -> Dict[str, Any]:
    result = run_platform_scan(
        target_platform=req.platform,
        scope=req.scope,
        project_path=req.project_path,
    )
    return result


@app.post("/mitigate/{vuln_id}")
async def confirm_mitigation_endpoint(vuln_id: str) -> Dict[str, Any]:
    res = apply_mitigation(vuln_id, confirmed_by="api_confirmation")
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Not found"))
    return res


@app.get("/history/{session_id}")
async def fetch_history(session_id: str) -> Dict[str, Any]:
    return {"session_id": session_id, "messages": get_history(session_id, n=20)}


@app.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request) -> Dict[str, Any]:
    t0 = time.perf_counter()
    METRICS["requests_total"] += 1
    METRICS["chat_requests"] += 1
    client_ip = request.client.host if request.client else "127.0.0.1"
    enforce_rate_limit(client_ip)

    now_iso = datetime.now(timezone.utc).isoformat()
    user_msg = req.message.strip()

    # Persist user message in MongoDB sessions collection (A.4)
    sessions_col.insert_one(
        {
            "session_id": req.session_id,
            "role": "user",
            "content": user_msg,
            "platform": req.platform,
            "lifecycle": req.lifecycle,
            "timestamp": now_iso,
        }
    )

    # Check if user issued conversational confirmation: e.g., "CONFIRM FIX VULN-LNX-101"
    model_used, fallback_used = GEMINI_MODEL, False
    confirm_match = re.search(r"confirm\s+(?:fix|mitigate|remediate)\s+(vuln-[a-z]+-\d+)", user_msg, re.I)
    if confirm_match:
        target_vuln_id = confirm_match.group(1).upper()
        mit_res = apply_mitigation(target_vuln_id, confirmed_by=f"chat:{req.session_id}")
        if mit_res.get("ok"):
            v = mit_res["vulnerability"]
            answer = (
                f"**Conversational Confirmation Verified — Mitigation Executed (`{mit_res['audit_id']}`)**\n\n"
                f"- **Vulnerability:** `{v['vuln_id']}` ({v['cve']}) — {v['title']}\n"
                f"- **Platform:** {v['platform']} | **New Lifecycle State:** `{v['lifecycle']}` (`{v['status']}`)\n"
                f"- **Executed Procedure:** `{v['remediation_cmd']}`\n"
                f"- **Verification:** Configuration hardened (`{v['expected_value']}`) and logged to MongoDB `remediation_audit`."
            )
        else:
            answer = f"Could not execute mitigation: {mit_res.get('error')}"
    else:
        # Check if user asked to scan a platform in natural language
        scan_match = re.search(r"scan.*?(linux|windows|macos|android|ios)", user_msg, re.I)
        if scan_match:
            scan_plat = scan_match.group(1)
            run_platform_scan(scan_plat, scope="chat-triggered-scan")

        history = get_history(req.session_id, n=10)
        # Platform/lifecycle filters and CTI retrieval apply only to threat-related questions.
        use_cti = is_threat_query(user_msg, history)
        context_docs: List[Dict[str, Any]] = []
        pending_vulns: List[Dict[str, Any]] = []
        if use_cti:
            context_docs = retrieve_context(
                user_msg,
                platform_filter=req.platform,
                lifecycle_filter=req.lifecycle,
                n_results=4,
            )
            pending_vulns = list(
                vulns_col.find({"status": "pending_confirmation"}, {"_id": 0}).limit(5)
            )
        try:
            # Blocking HTTP + backoff sleeps run in a worker thread so /health, /metrics etc. stay responsive.
            answer, model_used, fallback_used = await asyncio.to_thread(
                call_hosted_gemini_llm_ex,
                user_msg=user_msg,
                context_docs=context_docs,
                history=history,
                pending_vulns=pending_vulns,
                platform=req.platform,
                lifecycle=req.lifecycle,
                use_cti=use_cti,
            )
        except LLMError as exc:
            METRICS["errors_total"] += 1
            logger.error("ThreatGuard AI Chat failed (HTTP %s): %s", exc.status_code, _redact(exc))
            # Do not persist an assistant turn: errors must never pollute the conversation memory.
            raise HTTPException(status_code=exc.status_code, detail=str(exc))

    sessions_col.insert_one(
        {
            "session_id": req.session_id,
            "role": "assistant",
            "content": answer,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    METRICS["total_latency_ms"] += elapsed_ms
    METRICS["estimated_tokens"] += (len(user_msg) + len(answer)) // 4

    logger.info(
        "ThreatGuard AI: Chat request complete model=%s fallback=%s latency_ms=%.1f session=%s",
        model_used,
        fallback_used,
        elapsed_ms,
        req.session_id,
    )

    return {
        "session_id": req.session_id,
        "system_name": "ThreatGuard AI",
        "answer": answer,
        "latency_ms": elapsed_ms,
        "llm_provider": "hosted_google_gemini_api",
        "model": model_used,
        "fallback_used": fallback_used,
    }
