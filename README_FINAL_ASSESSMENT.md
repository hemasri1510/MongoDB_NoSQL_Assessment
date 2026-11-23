# ThreatGuard AI: Multi-Model CTI Threat Intelligence & Autonomous Remediation System

Synthesizes **Exercises 1 through 18** into an enterprise-grade AI Threat Intelligence and Remediation platform powered by **Hosted Google Gemini Multi-Model Automatic Failover** (`GEMINI_API_KEY` — **FREE Models ONLY**, **not Ollama**) and **MongoDB** (operational threat store, vector RAG index, and persistent session memory).

---

## Key Capabilities

1. **Intelligent Automatic Multi-Model Failover (FREE Models ONLY)**:
   - Configurable list of verified free-tier models: `gemini-3.1-flash-lite`, `gemini-4-hydrogen`, `gemini-3.5-flash-lite`, `gemini-flash-lite-latest`, `gemini-2.5-flash-lite`.
   - **Zero Paid Models**: Strictly blocks and excludes paid, Pro, or Ultra models (`gemini-2.5-pro`, `gemini-3.1-pro`, `gemini-ultra`, `deep-research`).
   - **Automatic Failover**: Tries primary model first; upon temporary errors (HTTP 503 high demand, 429 quota exhaustion, 5xx, timeouts), retries with exponential backoff and automatically fails over to the next free model.
   - **Unsupported Model Auto-Skip**: Models returning HTTP 404 or unsupported status are marked `UNAVAILABLE` and immediately skipped without repeated retrying.
   - **Authentication Safeguard**: On HTTP 401 or 403, halts immediately and reports the API key / permission issue without useless model cycling.

2. **Model Health Manager (`gemini_failover.py`)**:
   - Tracks operational status for every model: `HEALTHY`, `COOLDOWN`, `UNAVAILABLE`.
   - Maintains failure counts, error codes, and cooldown periods.
   - **Auto-Recovery**: When a model's cooldown period elapses, it automatically recovers to `HEALTHY` and is seamlessly retried for subsequent requests.

3. **Gemini API Model Discovery**:
   - Queries `https://generativelanguage.googleapis.com/v1beta/models` on startup and caches verified content generation support (`generateContent`).
   - Intersects with the strict free-tier whitelist to ensure safety.

4. **Multi-Platform & Lifecycle Threat Intelligence**:
   - Covers **Anticipated**, **Active**, **Evolving**, and **Resolved** threats across **Linux**, **Windows**, **macOS**, **Android**, and **iOS** with CVEs, IoCs, and mitigation commands.

5. **State & Context Preservation**:
   - When switching models, the entire conversation history, ThreatGuard AI system instructions, RAG context, and MongoDB scanner findings are 100% preserved.

6. **User Privacy & Clean UI**:
   - Successful model name is logged in backend telemetry and returned in API responses.
   - Normal chatbot users are protected from raw technical error dumps: if all free models fail, a friendly reassurance message is displayed.

---

## Quick Start

### 1. Configure Environment
```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY
```

Configuration in `.env`:
```ini
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_FREE_MODELS=gemini-3.1-flash-lite,gemini-4-hydrogen,gemini-3.5-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash-lite
GEMINI_COOLDOWN_SECONDS=60
GEMINI_MAX_ATTEMPTS=3
GEMINI_RETRY_BASE_DELAY=1.0
GEMINI_RETRY_MAX_DELAY=8.0
```

### 2. Run with Docker Compose
```bash
docker compose up --build -d
```
- FastAPI Backend: `http://localhost:8000/docs`
- Health Endpoint: `http://localhost:8000/health`
- Streamlit UI: `http://localhost:8501`
- Web Portal with Floating Button: Open `index.html` in browser

---

## Verification Test Suites

Run all unit and integration test suites:
```bash
python -m pytest test_gemini_failover_system.py test_gemini_resilience.py test_chatbot_assessment.py -v
```
All 32 tests validate:
- HTTP 503 exponential retry and automatic fallback
- HTTP 429 quota exhaustion retry and fallback
- HTTP 404 / unsupported model immediate skip
- HTTP 401/403 authentication immediate stop
- Strict free-tier enforcement
- Cooldown and auto-recovery
- End-to-end FastAPI `/chat` and `/health` integration
