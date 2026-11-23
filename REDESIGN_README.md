# MongoDB Labs & Website — Redesigned UI

This package contains the MongoDB lab exercises, the interactive Labs 7–18 website, and the CTI chatbot application. The visual redesign changes the interface styling while retaining the original page content and JavaScript interactions.

## What changed (v3 — SENTINEL command-center redesign)
A new visual identity and layout (not just a recolour): charcoal base with electric-blue, violet and cyan accents, glow, gradients and motion.
- **Collapsible command rail** replaces the sidebar: a floating glass panel that collapses to an icon-only strip (state remembered), with accordion module groups, a lab filter, visited-lab indicators and a progress footer. It becomes a slide-in drawer on phones.
- **Mission Control dashboard** is the new landing view: radar hero, five interactive stat cards (cursor glow, animated counters, sparklines; click to browse labs, spotlight unexplored modules, open the CTI assistant or jump to the scanner), a module matrix with one-click lab chips, a coverage ring + 24-cell lab heat map, and a live activity log. Lab progress is stored in this browser only.
- **Glass top bar** with breadcrumb, simulator status pill, live clock and theme toggle (dark default, optional "daylight" variant).
- **HUD-style content**: corner-bracket cards, hexagon headings, mono-header tables, gradient buttons with sheen, pulsing status badges, glowing metrics and progress bars, terminal-style code and console panels.
- **Chatbot**: the floating button is now an animated orb with a live status LED and expanding label; the chat window is a glass terminal panel with a scanning header, themed bubbles and typing indicator. `.streamlit/config.toml` gives the Streamlit chat the same palette.
- **Unchanged**: all 24 interactive labs and the Final Assessment, every lab simulator and MongoDB operation, the Gemini integration (browser key + backend), the FastAPI backend and the background scanner. Deep links such as `index.html#lab92` still work.

## Open the interactive website
1. Open `Mongodb website/index.html` in a modern browser (Chrome or Edge).
2. The interactive Labs 7–18 website runs in the browser.

## Run the Streamlit chatbot frontend
Open a terminal in `Mongodb website/LLM_Cti_Chatbot` and run:

```bash
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

The Streamlit frontend expects the FastAPI backend at `http://localhost:8000` by default. Set `BACKEND_URL` if your backend uses a different address.

## Configuration and security
- Real `.env` files and Git metadata were intentionally excluded from this shareable ZIP.
- Copy `.env.example` to `.env` and enter your own credentials before running services that require them.
- Do not commit API keys, passwords, database credentials, or private certificates.
- The static website's Gemini integration contains a placeholder (`YOUR_API_KEY_HERE`); configure a key securely before using hosted Gemini features. Avoid putting real API keys into public HTML.

## Important
The static website and Streamlit frontend are separate entry points. The HTML website can be opened directly. The chatbot frontend requires its FastAPI backend and configured services to be running.

## General-purpose AI assistant (chat widget + backend)
- The floating "CTI AI Assistant" and the Final Assessment chat now answer questions on any topic (explanations, problem solving, code, study help, casual conversation) using the hosted Gemini API. CTI/lab knowledge is passed as optional context, not a restriction.
- The full conversation is sent on every request, so follow-up questions work. History is saved in the browser (localStorage); **Clear Memory** resets it.
- To enable AI answers in the web page, click **🔑 API Key** (in the chat panel or the floating drawer) and paste a Gemini API key. It is stored only in your browser. Without a key (and without a running backend that has `GEMINI_API_KEY`) the chat shows a clear error; there is no canned answer.
- Errors (invalid key, quota/rate limit, timeout, offline, blocked content) show a plain-language message. Platform/lifecycle filters and the CTI knowledge base are applied only to threat-related questions (including short follow-ups to them).
- Backend (`LLM_Cti_Chatbot/main.py`): same general-purpose system prompt and multi-turn history via `GEMINI_API_KEY`. The Streamlit frontend uses this automatically. Fixed existing syntax errors in `main.py` and `ingest_threats.py` (broken string literals) so they run again.
