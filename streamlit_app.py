"""
Final Assessment (A.5): Streamlit Chat Frontend for ThreatGuard AI
Intelligent multi-model automatic failover across verified FREE Google Gemini models.
"""

import os
import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="ThreatGuard AI - CTI Chatbot", page_icon="🛡️", layout="wide")


st.markdown("""
<style>
:root { color-scheme: light; }
.stApp {
  background:
    radial-gradient(circle at 8% 3%, rgba(190,177,235,.28), transparent 30%),
    radial-gradient(circle at 92% 10%, rgba(255,202,190,.20), transparent 28%),
    #f4f2fb;
  color: #29243e;
}
[data-testid="stHeader"] { background: rgba(244,242,251,.82); }
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #eeebf8 0%, #f8f6fc 100%);
  border-right: 1px solid #ded7ee;
}
h1, h2, h3 { color: #51429d !important; letter-spacing: -.025em; }
p, label, [data-testid="stMarkdownContainer"] { color: #39334f; }
div[data-testid="stChatMessage"] {
  background: rgba(255,255,255,.88);
  border: 1px solid #e3ddef;
  border-radius: 18px;
  padding: 14px 16px;
  box-shadow: 0 8px 24px rgba(55,43,91,.06);
}
div[data-testid="stChatInput"] {
  background: white;
  border: 1px solid #dcd5ea;
  border-radius: 16px;
}
.stButton > button {
  background: linear-gradient(135deg, #6554c0, #8b69c9);
  color: white;
  border: 0;
  border-radius: 12px;
  font-weight: 650;
  padding: .55rem 1rem;
}
.stButton > button:hover { color: white; border: 0; filter: brightness(1.05); }
div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {
  background: white;
  border-color: #dcd5ea;
  border-radius: 11px;
}
hr { border-color: #e3ddef; }
.model-pill {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 6px;
  background: #ede9fe;
  color: #5b21b6;
  font-size: 0.78rem;
  font-weight: 600;
  margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)

st.title("🛡️ ThreatGuard AI: CTI Threat Intelligence & Remediation")
st.caption("Multi-Model Automatic Failover (FREE Google Gemini API) • MongoDB Vector RAG • Autonomous Scanner")

with st.sidebar:
    st.header("ThreatGuard AI Scope & Filters")
    platform = st.selectbox("Platform", ["All", "Linux", "Windows", "macOS", "Android", "iOS"])
    lifecycle = st.selectbox("Threat Lifecycle", ["All", "Anticipated", "Active", "Evolving", "Resolved"])
    session_id = st.text_input("Session ID", value="cti-analyst-session-01")

    st.divider()
    st.subheader("Model Resilience & Health")
    try:
        hr = requests.get(f"{BACKEND_URL}/health", timeout=3)
        if hr.ok:
            hdata = hr.json()
            st.success(f"System: {hdata.get('system_name', 'ThreatGuard AI')} ({hdata.get('status', 'healthy')})")
            st.caption(f"Primary Model: `{hdata.get('primary_model', 'gemini-3.1-flash-lite')}`")
            avail = hdata.get("available_models", [])
            if avail:
                st.caption(f"Available Free Models: {', '.join(f'`{m}`' for m in avail[:3])}")
        else:
            st.warning("Backend degraded")
    except Exception:
        st.info("Backend health check pending")

    st.divider()
    st.subheader("Background Scanner Service")
    scan_plat = st.selectbox("Scan Platform", ["Linux", "Windows", "macOS", "Android", "iOS"], key="scan_p")
    scan_scope = st.selectbox("Scan Scope", ["system-wide", "project-directory"])
    if st.button("Run Vulnerability Scan", use_container_width=True):
        try:
            r = requests.post(
                f"{BACKEND_URL}/scan",
                json={"platform": scan_plat, "scope": scan_scope},
                timeout=15,
            )
            data = r.json()
            st.success(f"Scan {data['scan_id']} complete ({len(data['findings'])} findings).")
        except Exception as exc:
            st.error(f"Scan failed: {exc}")

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Welcome to **ThreatGuard AI** — your intelligent Cyber Threat Intelligence and Remediation Assistant. "
                "I operate on an automatic multi-model failover engine using verified free-tier Google Gemini models "
                "(`gemini-3.1-flash-lite`, `gemini-4-hydrogen`, etc.) with MongoDB RAG and platform scanning memory.\n\n"
                "Ask about **Anticipated**, **Active**, **Evolving**, or **Resolved** threats across Linux, Windows, macOS, "
                "Android, and iOS, or reply `CONFIRM FIX <VULN-ID>` to remediate scanner findings."
            ),
        }
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "model_used" in msg:
            st.markdown(f"<span class='model-pill'>🤖 Served by: {msg['model_used']}{' (fallback)' if msg.get('fallback_used') else ''}</span>", unsafe_allow_html=True)

if prompt := st.chat_input("Ask about platform threats, CVEs, IoCs, or type 'CONFIRM FIX VULN-LNX-101'..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        model_used = None
        fallback_used = False
        try:
            resp = requests.post(
                f"{BACKEND_URL}/chat",
                json={
                    "message": prompt,
                    "session_id": session_id,
                    "platform": platform,
                    "lifecycle": lifecycle,
                },
                timeout=150,  # backend may retry with backoff and automatic failover
            )
            if resp.ok:
                resp_data = resp.json()
                answer = resp_data.get("answer") or "⚠️ The backend returned an empty response."
                model_used = resp_data.get("model")
                fallback_used = resp_data.get("fallback_used", False)
            else:
                try:
                    detail = resp.json().get("detail", "")
                except Exception:
                    detail = ""
                answer = f"⚠️ {detail or 'ThreatGuard AI is currently unable to reach the AI service due to temporary high demand. Please try again shortly.'}"
        except Exception as exc:
            answer = f"Backend unreachable ({exc}). Ensure FastAPI is running on `{BACKEND_URL}`."

        st.markdown(answer)
        if model_used:
            st.markdown(f"<span class='model-pill'>🤖 Served by: {model_used}{' (fallback)' if fallback_used else ''}</span>", unsafe_allow_html=True)

        bot_msg = {"role": "assistant", "content": answer}
        if model_used:
            bot_msg["model_used"] = model_used
            bot_msg["fallback_used"] = fallback_used
        st.session_state.messages.append(bot_msg)
