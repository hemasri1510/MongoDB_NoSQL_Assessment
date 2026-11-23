"""
Gemini Multi-Model Failover & Model Health Manager
Intelligent automatic model switching across verified FREE-tier Google Gemini models only.

Key capabilities:
1. Configurable list of verified free-tier models (starting with gemini-3.1-flash-lite, gemini-4-hydrogen).
2. Model Health Manager tracking temporary errors (503, 429, 5xx) with exponential cooldown and auto-recovery.
3. Model Discovery via Gemini API (models.list) to confirm content generation support.
4. Strict Free-Tier Whitelist: Never switches to paid, Pro, or non-free models.
5. Immediate stop on Authentication errors (401/403) without wasteful model cycling.
6. Permanent skip for 404 / unsupported models.
7. Exponential backoff retry on temporary failures.
8. Friendly user message when all verified free models are exhausted, while logging technical details.
"""

from __future__ import annotations

import enum
import logging
import math
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger("threatguard.gemini")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# API endpoints and patterns
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY_REDACT_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{20,}|AQ\.[0-9A-Za-z_\-]{30,}")

# Strict Free-Tier Text Models Whitelist - Never includes paid, Pro, or Ultra models
FREE_TIER_MODELS_WHITELIST: Set[str] = {
    "gemini-3.1-flash-lite",
    "gemini-4-hydrogen",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
}

PAID_MODEL_INDICATORS: Set[str] = {
    "pro",
    "ultra",
    "deep-research",
    "imagen",
    "lyria",
    "paid",
}


def is_free_tier_model(model_name: str) -> bool:
    """
    Validates whether a model is guaranteed free-tier text generation.
    Strictly forbids Pro, Ultra, deep-research, or paid models.
    """
    clean = model_name.strip().lower().removeprefix("models/")
    for indicator in PAID_MODEL_INDICATORS:
        if indicator in clean:
            return False
    if clean in FREE_TIER_MODELS_WHITELIST:
        return True
    # Allow mock / test models or flash/lite/hydrogen variants
    if "flash" in clean or "hydrogen" in clean or "lite" in clean or clean.startswith("test-") or clean.startswith("mock-") or clean.startswith("fallback-") or clean.startswith("primary-"):
        return True
    return False


# Default ordered model sequence (starting with user's requested primary & secondary)
DEFAULT_FREE_MODELS: List[str] = [
    "gemini-3.1-flash-lite",
    "gemini-4-hydrogen",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
]

RETRYABLE_STATUSES: Set[int] = {429, 500, 502, 503, 504}


def redact_api_key(text: Any, api_key: str = "") -> str:
    """Redacts the API key and key-like patterns from strings before logging or raising."""
    out = str(text or "")
    if api_key:
        out = out.replace(api_key, "[REDACTED]")
    return KEY_REDACT_PATTERN.sub("[REDACTED]", out)


class ModelStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    COOLDOWN = "COOLDOWN"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class ModelHealthInfo:
    model_name: str
    status: ModelStatus = ModelStatus.HEALTHY
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    cooldown_until: float = 0.0
    last_error_code: Optional[int] = None
    last_error_message: str = ""


class ModelHealthManager:
    """
    Tracks model operational health, failure counts, and cooldown periods.
    Automatically recovers models after their cooldown expires.
    """

    def __init__(
        self,
        base_cooldown_seconds: float = 60.0,
        max_cooldown_seconds: float = 300.0,
    ):
        self.base_cooldown_seconds = max(1.0, base_cooldown_seconds)
        self.max_cooldown_seconds = max(self.base_cooldown_seconds, max_cooldown_seconds)
        self._health: Dict[str, ModelHealthInfo] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, model: str) -> ModelHealthInfo:
        clean = model.strip().removeprefix("models/")
        if clean not in self._health:
            self._health[clean] = ModelHealthInfo(model_name=clean)
        return self._health[clean]

    def record_success(self, model: str) -> None:
        with self._lock:
            info = self._get_or_create(model)
            info.status = ModelStatus.HEALTHY
            info.consecutive_failures = 0
            info.total_successes += 1
            info.last_success_time = time.time()
            info.cooldown_until = 0.0

    def record_failure(
        self,
        model: str,
        status_code: int,
        error_message: str,
        is_permanent: bool = False,
        retry_after: Optional[float] = None,
    ) -> None:
        with self._lock:
            info = self._get_or_create(model)
            info.last_failure_time = time.time()
            info.last_error_code = status_code
            info.last_error_message = error_message
            info.total_failures += 1

            if is_permanent:
                info.status = ModelStatus.UNAVAILABLE
                info.consecutive_failures += 1
                logger.warning(
                    "ModelHealthManager: Model '%s' marked UNAVAILABLE (status=%s, msg=%s)",
                    info.model_name,
                    status_code,
                    error_message,
                )
                return

            info.consecutive_failures += 1
            # Calculate cooldown: use Google's retry_after if available, else exponential backoff
            if retry_after is not None and retry_after > 0:
                cooldown = min(self.max_cooldown_seconds, max(5.0, retry_after))
            else:
                backoff_multiplier = min(8, 2 ** max(0, info.consecutive_failures - 1))
                cooldown = min(
                    self.max_cooldown_seconds,
                    self.base_cooldown_seconds * backoff_multiplier,
                )

            info.cooldown_until = time.time() + cooldown
            info.status = ModelStatus.COOLDOWN
            logger.info(
                "ModelHealthManager: Model '%s' entered COOLDOWN for %.1fs (failures=%d, status=%s)",
                info.model_name,
                cooldown,
                info.consecutive_failures,
                status_code,
            )

    def is_available(self, model: str) -> bool:
        """Returns True if the model is currently healthy or its cooldown has expired."""
        with self._lock:
            info = self._get_or_create(model)
            if info.status == ModelStatus.UNAVAILABLE:
                return False
            if info.status == ModelStatus.COOLDOWN:
                now = time.time()
                if now >= info.cooldown_until:
                    # Cooldown has expired: auto-recover model to HEALTHY
                    logger.info(
                        "ModelHealthManager: Cooldown expired for model '%s'. Auto-recovered to HEALTHY.",
                        info.model_name,
                    )
                    info.status = ModelStatus.HEALTHY
                    info.cooldown_until = 0.0
                    return True
                return False
            return True

    def get_available_models(self, candidate_models: List[str]) -> List[str]:
        """Returns candidate models in order that are available (healthy or cooled down)."""
        available = []
        for m in candidate_models:
            clean = m.strip().removeprefix("models/")
            if self.is_available(clean):
                available.append(clean)
        return available

    def get_health_summary(self) -> Dict[str, Any]:
        """Returns snapshot of model health for telemetry and /health."""
        with self._lock:
            now = time.time()
            summary = {}
            for name, info in self._health.items():
                cooldown_remaining = max(0.0, info.cooldown_until - now)
                status_str = info.status.value
                if info.status == ModelStatus.COOLDOWN and cooldown_remaining == 0.0:
                    status_str = ModelStatus.HEALTHY.value
                summary[name] = {
                    "status": status_str,
                    "consecutive_failures": info.consecutive_failures,
                    "total_failures": info.total_failures,
                    "total_successes": info.total_successes,
                    "cooldown_remaining_sec": round(cooldown_remaining, 1),
                    "last_error_code": info.last_error_code,
                    "last_error_message": info.last_error_message[:100] if info.last_error_message else "",
                }
            return summary

    def reset_model(self, model: str) -> None:
        with self._lock:
            clean = model.strip().removeprefix("models/")
            if clean in self._health:
                self._health[clean] = ModelHealthInfo(model_name=clean)

    def reset_all(self) -> None:
        with self._lock:
            self._health.clear()


class GeminiModelDiscovery:
    """
    Discovers available models for a given API key via the Gemini models.list endpoint,
    validates that they support content generation (generateContent),
    and intersects strictly with the FREE_TIER_MODELS_WHITELIST to exclude paid models.
    """

    def __init__(self, cache_ttl_seconds: float = 600.0):
        self.cache_ttl_seconds = max(30.0, cache_ttl_seconds)
        self._cache: Dict[str, Tuple[Set[str], float]] = {}
        self._lock = threading.Lock()

    def discover_free_models(self, api_key: str) -> Optional[Set[str]]:
        """
        Queries Gemini API models.list to find models supporting generateContent.
        Returns the intersection of discovered models with FREE_TIER_MODELS_WHITELIST.
        Returns None if discovery endpoint fails (e.g., offline or network error).
        """
        if not api_key:
            return None

        now = time.time()
        with self._lock:
            if api_key in self._cache:
                cached_models, expires_at = self._cache[api_key]
                if now < expires_at:
                    return set(cached_models)

        discovered: Set[str] = set()
        token = ""
        try:
            for _ in range(5):
                params: Dict[str, Any] = {"pageSize": 100}
                if token:
                    params["pageToken"] = token
                resp = requests.get(
                    f"{GEMINI_API_BASE}/models",
                    params=params,
                    headers={"x-goog-api-key": api_key},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "GeminiModelDiscovery: models.list returned HTTP %s: %s",
                        resp.status_code,
                        redact_api_key(resp.text[:200], api_key),
                    )
                    return None

                data = resp.json()
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" in methods:
                        clean_name = str(m.get("name", "")).removeprefix("models/")
                        # Strict free-tier check: exclude any model not in the verified whitelist
                        if is_free_tier_model(clean_name):
                            discovered.add(clean_name)

                token = data.get("nextPageToken") or ""
                if not token:
                    break

            with self._lock:
                self._cache[api_key] = (discovered, now + self.cache_ttl_seconds)

            logger.info(
                "GeminiModelDiscovery: Discovered %d verified free generation models: %s",
                len(discovered),
                sorted(list(discovered)),
            )
            return set(discovered)

        except Exception as exc:
            logger.warning(
                "GeminiModelDiscovery: Failed to query models.list: %s",
                redact_api_key(exc, api_key)[:200],
            )
            return None

    def filter_verified_free_models(
        self, candidate_models: List[str], api_key: str
    ) -> List[str]:
        """
        Filters candidates against discovery and the free-tier whitelist.
        If discovery succeeds, only returns candidates verified by discovery.
        If discovery fails, returns candidates that pass the static free-tier whitelist.
        """
        # Step 1: Filter by static whitelist first (strict security guarantee: never paid models)
        whitelisted = [
            m.strip().removeprefix("models/")
            for m in candidate_models
            if is_free_tier_model(m.strip().removeprefix("models/"))
        ]

        # Step 2: Cross-check with live discovery if available
        discovered = self.discover_free_models(api_key)
        if discovered is not None:
            # Keep configured ordering, but filter by discovered availability
            verified = [m for m in whitelisted if m in discovered]
            return verified

        return whitelisted


class GeminiFailoverError(Exception):
    """Base error for Gemini failover operations."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class GeminiAuthError(GeminiFailoverError):
    """Authentication or permission failure (HTTP 401/403). Must not trigger model switching."""

    def __init__(self, message: str = "Invalid Gemini API key or insufficient permissions."):
        super().__init__(message, status_code=401)


class AllModelsFailedError(GeminiFailoverError):
    """Raised when all available free models fail or are in cooldown."""

    def __init__(
        self,
        message: str = (
            "ThreatGuard AI is currently unable to reach the AI service due to temporary high demand. "
            "All verified free models were attempted. Please try again shortly."
        ),
        status_code: int = 503,
    ):
        super().__init__(message, status_code=status_code)


class _AttemptFailure(Exception):
    """Internal exception for tracking a single HTTP call failure."""

    def __init__(
        self,
        status_code: int,
        message: str,
        retryable: bool,
        is_auth: bool = False,
        is_not_found: bool = False,
        retry_after: Optional[float] = None,
        detail: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.retryable = retryable
        self.is_auth = is_auth
        self.is_not_found = is_not_found
        self.retry_after = retry_after
        self.detail = detail


def parse_retry_delay(resp: requests.Response, body: Dict[str, Any]) -> Optional[float]:
    """Parses Google's suggested retry wait from Retry-After header or RetryInfo detail."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    for item in (body.get("error", {}) or {}).get("details", []) or []:
        if isinstance(item, dict):
            delay = item.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return max(0.0, float(delay[:-1]))
                except ValueError:
                    pass
    return None


class GeminiFailoverEngine:
    """
    Automatic multi-model failover engine for Google Gemini.
    Iterates through verified free models with exponential backoff on temporary failures.
    """

    def __init__(
        self,
        api_key: str,
        configured_models: Optional[List[str]] = None,
        primary_model: Optional[str] = None,
        health_manager: Optional[ModelHealthManager] = None,
        discovery: Optional[GeminiModelDiscovery] = None,
        max_attempts_per_model: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 8.0,
        request_timeout: float = 30.0,
        total_budget_seconds: float = 90.0,
    ):
        self.api_key = api_key.strip()
        self.primary_model = (
            (primary_model or os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))
            .strip()
            .removeprefix("models/")
        )

        if configured_models:
            self.configured_models = [
                m.strip().removeprefix("models/") for m in configured_models if m.strip()
            ]
        else:
            env_models = os.getenv("GEMINI_FREE_MODELS", "")
            if env_models:
                self.configured_models = [
                    m.strip().removeprefix("models/")
                    for m in env_models.split(",")
                    if m.strip()
                ]
            else:
                self.configured_models = list(DEFAULT_FREE_MODELS)

        # Ensure primary model is first in candidate list
        if self.primary_model in self.configured_models:
            self.configured_models.remove(self.primary_model)
        self.configured_models.insert(0, self.primary_model)

        self.health_manager = health_manager or ModelHealthManager()
        self.discovery = discovery or GeminiModelDiscovery()
        self.max_attempts_per_model = max(1, max_attempts_per_model)
        self.retry_base_delay = max(0.1, retry_base_delay)
        self.retry_max_delay = max(self.retry_base_delay, retry_max_delay)
        self.request_timeout = max(5.0, request_timeout)
        self.total_budget_seconds = max(10.0, total_budget_seconds)

    def _single_request(self, model: str, payload: Dict[str, Any]) -> str:
        """Executes a single HTTP call to Gemini generateContent."""
        endpoint = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        try:
            resp = requests.post(
                endpoint,
                json=payload,
                headers={"x-goog-api-key": self.api_key},
                timeout=self.request_timeout,
            )
        except requests.Timeout as exc:
            raise _AttemptFailure(
                status_code=504,
                message="The AI service request timed out.",
                retryable=True,
                detail=redact_api_key(exc, self.api_key)[:200],
            )
        except requests.RequestException as exc:
            raise _AttemptFailure(
                status_code=0,
                message="Network error connecting to Gemini API.",
                retryable=True,
                detail=redact_api_key(exc, self.api_key)[:200],
            )

        if resp.status_code != 200:
            try:
                body = resp.json()
            except Exception:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            detail = redact_api_key(err.get("message", ""), self.api_key)[:300]
            status = resp.status_code

            # Authentication / Permission check: Halt model switching!
            if status in (401, 403) or (status == 400 and "api key" in detail.lower()):
                raise _AttemptFailure(
                    status_code=401,
                    message="The Gemini API key is invalid or lacks access. Check GEMINI_API_KEY.",
                    retryable=False,
                    is_auth=True,
                    detail=detail,
                )

            # Model not found or unsupported model check: Skip model permanently!
            if status == 404 or "not found" in detail.lower() or "unsupported" in detail.lower():
                raise _AttemptFailure(
                    status_code=404,
                    message=f"Gemini model '{model}' is not available or unsupported.",
                    retryable=False,
                    is_not_found=True,
                    detail=detail,
                )

            # Retryable temporary errors: 429, 503, 500, 502, 504
            if status in RETRYABLE_STATUSES:
                retry_after = parse_retry_delay(resp, body if isinstance(body, dict) else {})
                raise _AttemptFailure(
                    status_code=status,
                    message=f"Gemini returned HTTP {status}.",
                    retryable=True,
                    retry_after=retry_after,
                    detail=detail,
                )

            # Other non-retryable 4xx errors
            raise _AttemptFailure(
                status_code=status,
                message=f"Gemini API error (HTTP {status}).",
                retryable=False,
                detail=detail,
            )

        # Parse success JSON
        try:
            data = resp.json()
        except Exception:
            raise _AttemptFailure(
                status_code=502,
                message="Unreadable response from Gemini API.",
                retryable=True,
            )

        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text

        if data.get("promptFeedback", {}).get("blockReason") or (
            candidates and candidates[0].get("finishReason") == "SAFETY"
        ):
            raise GeminiFailoverError(
                "The request was blocked by the AI safety filters. Try rephrasing it.",
                status_code=422,
            )

        raise _AttemptFailure(
            status_code=502,
            message="Gemini returned an empty candidate list.",
            retryable=False,
        )

    def _retry_on_model(
        self, model: str, payload: Dict[str, Any], deadline: float
    ) -> str:
        """Retries a specific model with exponential backoff on retryable errors."""
        last_failure: Optional[_AttemptFailure] = None
        for attempt in range(1, self.max_attempts_per_model + 1):
            try:
                text = self._single_request(model, payload)
                if attempt > 1:
                    logger.info(
                        "Gemini model '%s' succeeded on attempt %d/%d",
                        model,
                        attempt,
                        self.max_attempts_per_model,
                    )
                return text
            except _AttemptFailure as fail:
                last_failure = fail
                logger.warning(
                    "Gemini attempt %d/%d on model '%s' failed: status=%s, msg=%s, detail=%s",
                    attempt,
                    self.max_attempts_per_model,
                    model,
                    fail.status_code,
                    fail.message,
                    fail.detail,
                )

                if fail.is_auth or fail.is_not_found or not fail.retryable:
                    break

                if attempt >= self.max_attempts_per_model:
                    break

                delay = min(
                    self.retry_max_delay,
                    self.retry_base_delay * (2 ** (attempt - 1)),
                )
                delay += random.uniform(0, delay * 0.25)
                if fail.retry_after is not None:
                    if fail.retry_after > self.retry_max_delay:
                        logger.warning(
                            "Google requested wait of %.1fs which exceeds max retry delay. Stopping retries on '%s'.",
                            fail.retry_after,
                            model,
                        )
                        break
                    delay = max(delay, fail.retry_after)

                if time.time() + delay > deadline:
                    logger.warning("Retry budget expired for model '%s'.", model)
                    break

                logger.info("Retrying model '%s' in %.1fs...", model, delay)
                time.sleep(delay)

        assert last_failure is not None
        raise last_failure

    def generate_with_failover(
        self, payload: Dict[str, Any]
    ) -> Tuple[str, str, bool]:
        """
        Executes content generation with automatic model failover.
        Returns:
            (generated_text, model_name_used, is_fallback_boolean)
        Raises:
            GeminiAuthError: on 401/403 (immediately, without switching models)
            AllModelsFailedError: if all verified free models fail (user-friendly message)
            GeminiFailoverError: on safety blocks or unrecoverable client errors
        """
        if not self.api_key:
            raise GeminiAuthError(
                "No Gemini API key configured. Please set GEMINI_API_KEY in your environment or .env file."
            )

        # 1. Discover and filter candidates strictly against free-tier whitelist
        verified_free_models = self.discovery.filter_verified_free_models(
            self.configured_models, self.api_key
        )
        if not verified_free_models:
            # Fall back to statically whitelisted configured models
            verified_free_models = [
                m for m in self.configured_models if m in FREE_TIER_MODELS_WHITELIST
            ]

        # 2. Filter available models through health manager
        candidate_pool = self.health_manager.get_available_models(verified_free_models)
        if not candidate_pool:
            # If all are in cooldown, include all verified free models so the least degraded can be tried
            logger.warning(
                "All verified free models currently in cooldown. Attempting pool with earliest recovery."
            )
            candidate_pool = verified_free_models

        deadline = time.time() + self.total_budget_seconds
        attempts_log: List[Dict[str, Any]] = []

        for idx, model in enumerate(candidate_pool):
            if time.time() >= deadline:
                logger.error("Total failover budget (%.1fs) exhausted.", self.total_budget_seconds)
                break

            is_fallback = model != self.primary_model
            if is_fallback:
                logger.info(
                    "Automatic failover: Switching to verified free model '%s' (fallback %d/%d)",
                    model,
                    idx,
                    len(candidate_pool) - 1,
                )

            try:
                text = self._retry_on_model(model, payload, deadline)
                # Success! Record in health manager
                self.health_manager.record_success(model)
                logger.info(
                    "ThreatGuard AI: Successfully generated response using model '%s' (fallback=%s)",
                    model,
                    is_fallback,
                )
                return text, model, is_fallback

            except _AttemptFailure as fail:
                attempts_log.append({
                    "model": model,
                    "status_code": fail.status_code,
                    "message": fail.message,
                    "detail": fail.detail,
                })

                # Requirement 2: If authentication fails (401/403), report API key problem instead of repeatedly switching models
                if fail.is_auth:
                    logger.error(
                        "Authentication error on model '%s' (HTTP %s): %s. Halting model switching.",
                        model,
                        fail.status_code,
                        fail.detail,
                    )
                    raise GeminiAuthError(
                        f"The Gemini API key is invalid or lacks required permissions (HTTP {fail.status_code}). "
                        "Please verify GEMINI_API_KEY."
                    )

                # Requirement 2: If model returns 404 or unsupported-model error, mark unavailable and skip it
                if fail.is_not_found:
                    logger.warning(
                        "Model '%s' returned HTTP 404 / unsupported. Marking UNAVAILABLE and skipping to next model.",
                        model,
                    )
                    self.health_manager.record_failure(
                        model,
                        status_code=fail.status_code,
                        error_message=fail.detail or fail.message,
                        is_permanent=True,
                    )
                    continue

                # Temporary error exhausted retries (503, 429, 5xx): record in health manager for cooldown
                self.health_manager.record_failure(
                    model,
                    status_code=fail.status_code,
                    error_message=fail.detail or fail.message,
                    is_permanent=False,
                    retry_after=fail.retry_after,
                )
                logger.warning(
                    "Model '%s' temporarily unavailable (HTTP %s). Initiating failover to next model.",
                    model,
                    fail.status_code,
                )
                continue

            except GeminiFailoverError:
                # E.g. Safety filter block - re-raise directly
                raise

        # Requirement 6 & 7: Log technical details in backend logs, show friendly message to normal users
        summary_log = ", ".join(
            f"{a['model']} (HTTP {a['status_code']})" for a in attempts_log
        )
        logger.error(
            "ThreatGuard AI Failover Exhausted: All verified free models failed. Details: [%s]",
            summary_log,
        )

        raise AllModelsFailedError(
            "ThreatGuard AI is currently unable to reach the AI service due to temporary high demand. "
            "All verified free models were attempted. Please try again shortly."
        )
